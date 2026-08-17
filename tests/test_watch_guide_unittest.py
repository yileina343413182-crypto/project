# -*- coding: utf-8 -*-
"""待看番剧指南状态、作品解析、生成降级与原子持久化测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select

from backend.agents.watch_guide import (
    anime_title_key,
    build_watch_guide_event,
    classify_offer_reply,
    generate_watch_guide,
    normalize_anime_title,
    reconstruct_watch_guide_state,
    resolve_anime_subject,
    save_watch_guide_with_message,
    should_offer_watch_guide,
)
from backend.db.models import AgentMessage, AgentSession, AgentTask, Base, User, WatchGuide
from backend.db.session import get_sync_engine, session_scope


def _anime(name="CLANNAD ～AFTER STORY～", anime_id=876):
    return {"anime_id": anime_id, "name": name, "source": "test"}


class WatchGuideStateTest(unittest.TestCase):
    def test_title_key_normalizes_width_case_space_and_punctuation(self):
        variants = (
            "CLANNAD ～AFTER STORY～",
            "clannad~after story~",
            "ＣＬＡＮＮＡＤ AFTER-STORY",
        )
        self.assertEqual(
            {anime_title_key(value) for value in variants},
            {anime_title_key(variants[0])},
        )
        self.assertEqual(normalize_anime_title("《 K 》"), "k")

    def test_confirmation_requires_a_complete_short_reply(self):
        pending = {"offer_id": "1:2", "anime": _anime()}
        for text in ("需要", "好的", "帮我加入待看番剧指南", "生成吧"):
            self.assertEqual(classify_offer_reply(text, pending), "accept", text)
        for text in ("不需要", "先不加", "暂时不用，谢谢"):
            self.assertEqual(classify_offer_reply(text, pending), "decline", text)
        for text in ("我需要知道有几集", "需要注意什么", "可以再讲角色吗", "要不要准备纸巾"):
            self.assertEqual(classify_offer_reply(text, pending), "other", text)
        self.assertEqual(classify_offer_reply("需要", None), "other")

    def test_pending_offer_is_recovered_only_for_adjacent_agent_message(self):
        offered = build_watch_guide_event("offered", _anime(), offer_id="1:2")
        messages = [
            {
                "id": 7,
                "role": "agent",
                "content": "是否加入？",
                "metadata": {"watch_guide_events": [offered]},
            }
        ]
        state = reconstruct_watch_guide_state(messages)
        self.assertEqual(state["pending_offer"]["offer_id"], "1:2")
        self.assertEqual(state["active_target"]["anime_id"], 876)
        self.assertEqual(state["offered_keys"], [anime_title_key(_anime()["name"])])

        stale = reconstruct_watch_guide_state(
            [*messages, {"id": 8, "role": "user", "content": "我还想问几集", "metadata": None}]
        )
        self.assertIsNone(stale["pending_offer"])

    @patch("backend.agents.watch_guide.orm_session")
    def test_subject_resolution_is_exact_longest_and_ambiguity_safe(self, session_scope_mock):
        rows = [
            {"id": 51, "name": "CLANNAD", "platform": "local"},
            {"id": 876, "name": "CLANNAD ～AFTER STORY～", "platform": "local"},
            {"id": 3, "name": "K", "platform": "local"},
            {"id": 4, "name": "86", "platform": "local"},
        ]
        session = session_scope_mock.return_value.__enter__.return_value
        session.execute.return_value.mappings.return_value = rows
        context = {
            "recommendations": [
                {"anime_id": 876, "name": "CLANNAD ～AFTER STORY～"},
                {"anime_id": 3, "name": "K"},
            ]
        }

        longest = resolve_anime_subject(
            "详细介绍 CLANNAD ～AFTER STORY～",
            context,
            None,
        )
        self.assertEqual(longest["anime_id"], 876)
        self.assertIsNone(
            resolve_anime_subject(
                "CLANNAD ～AFTER STORY～ 和 CLANNAD 有什么区别？",
                context,
                None,
            )
        )
        self.assertEqual(resolve_anime_subject("详细介绍第二部", context, None)["name"], "K")
        self.assertEqual(resolve_anime_subject("《K》怎么样", context, None)["name"], "K")
        self.assertIsNone(resolve_anime_subject("这个回答 OK 吗", context, None))
        self.assertIsNone(resolve_anime_subject("86岁怎么样", context, None))
        self.assertIsNone(
            resolve_anime_subject(
                "为什么你说“需要注意”，能解释一下吗？",
                context,
                None,
            )
        )
        self.assertIsNone(
            resolve_anime_subject(
                "推荐一些类似的作品",
                context,
                _anime(),
            )
        )
        self.assertEqual(
            resolve_anime_subject("它有几集？", context, _anime())["anime_id"],
            876,
        )
        self.assertEqual(
            resolve_anime_subject("我需要知道有几集", context, _anime())["anime_id"],
            876,
        )

    @patch("backend.agents.watch_guide.watch_guide_exists", return_value=False)
    def test_offer_only_for_specific_unasked_followup(self, exists):
        anime = _anime()
        empty_state = {"pending_offer": None, "active_target": None, "offered_keys": []}
        self.assertTrue(should_offer_watch_guide("详细介绍这部作品", anime, empty_state, 7))
        exists.assert_called_once()
        self.assertFalse(
            should_offer_watch_guide(
                "详细介绍这部作品",
                anime,
                {**empty_state, "offered_keys": [anime_title_key(anime["name"])]},
                7,
            )
        )
        another = _anime("四月是你的谎言", 22)
        self.assertTrue(
            should_offer_watch_guide(
                "详细介绍《四月是你的谎言》",
                another,
                {**empty_state, "offered_keys": [anime_title_key(anime["name"])]},
                7,
            )
        )
        exists.return_value = True
        self.assertFalse(
            should_offer_watch_guide(
                "《四月是你的谎言》有几集？",
                another,
                empty_state,
                7,
            )
        )
        self.assertFalse(should_offer_watch_guide("我已经看过这部作品", anime, empty_state, 7))

    def test_generation_uses_safe_fallback_without_model(self):
        with patch("backend.agents.watch_guide.get_chat_model", return_value=None):
            result = generate_watch_guide(_anime(), "此前的详细回答", [], {})

        self.assertTrue(result["fallback"])
        self.assertIn("观看前", result["content"])
        self.assertIn("分阶段观看计划", result["content"])
        self.assertIn("无法可靠核验具体集数", result["content"])
        self.assertEqual(result["prompt_trace"]["template_name"], "watch_guide")


class WatchGuidePersistenceTest(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(prefix="watch_guide_", suffix=".db", delete=False)
        handle.close()
        self.path = Path(handle.name)
        self.engine = get_sync_engine(db_path=str(self.path))
        Base.metadata.create_all(
            self.engine,
            tables=[
                User.__table__,
                AgentSession.__table__,
                WatchGuide.__table__,
                AgentMessage.__table__,
                AgentTask.__table__,
            ],
        )
        with session_scope(db_path=str(self.path)) as session:
            user = User(username="guide-user", password_hash="test")
            session.add(user)
            session.flush()
            self.user_id = user.id
            agent_session = AgentSession(
                user_id=user.id,
                agent_type="recommendation",
                title="guide test",
            )
            session.add(agent_session)
            session.flush()
            self.session_id = agent_session.id
            task = AgentTask(
                user_id=user.id,
                session_id=agent_session.id,
                agent_type="recommendation",
                status="running",
                input_data={},
                progress=10,
                current_step="running_agent",
            )
            session.add(task)
            session.flush()
            self.task_id = task.id

    def tearDown(self):
        self.engine.dispose()
        if self.path.exists():
            self.path.unlink()

    def _session(self):
        return session_scope(db_path=str(self.path))

    def test_atomic_save_is_idempotent_by_guide_key_and_offer_id(self):
        payload = {
            "response_mode": "conversation",
            "answer": "观看指南已经生成。",
            "offer_id": "session:task",
        }
        with patch("backend.agents.watch_guide.orm_session", side_effect=self._session):
            first = save_watch_guide_with_message(
                self.user_id,
                self.session_id,
                _anime(),
                {"content": "第一版指南", "prompt_trace": {}, "fallback": False},
                payload,
                task_id=self.task_id,
            )
            second = save_watch_guide_with_message(
                self.user_id,
                self.session_id,
                _anime(),
                {"content": "不得覆盖的第二版", "prompt_trace": {}, "fallback": False},
                payload,
                task_id=self.task_id,
            )

        self.assertTrue(first["watch_guide_created"])
        self.assertEqual(second["watch_guide"]["id"], first["watch_guide"]["id"])
        with session_scope(db_path=str(self.path)) as session:
            guide = session.scalar(select(WatchGuide))
            self.assertEqual(guide.guide_content, "第一版指南")
            self.assertEqual(session.scalar(select(func.count(AgentMessage.id))), 1)
            task = session.get(AgentTask, self.task_id)
            self.assertEqual(task.status, "succeeded")
            self.assertEqual(task.result["watch_guide"]["id"], guide.id)


if __name__ == "__main__":
    unittest.main()
