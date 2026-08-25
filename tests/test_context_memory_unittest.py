# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from backend.agents.context_memory import (
    forget_user_memory,
    list_user_memories,
    load_context_memory,
    maintain_context_memory,
    update_user_memory,
)
from backend.agents.memory import update_user_preferences
from backend.agents.schemas import (
    LongTermMemoryExtraction,
    LongTermMemoryProposal,
    SessionMemorySummary,
)
from backend.db.models import (
    AgentMessage,
    AgentSession,
    AgentSessionMemory,
    Base,
    User,
    UserMemoryFact,
    UserPreference,
)
from backend.db.session import get_sessionmaker, get_sync_engine


class _MemoryModel:
    def __init__(
        self,
        source_message_id: int | None = None,
        proposals: list[dict] | None = None,
    ):
        self.source_message_id = source_message_id
        self.proposals = proposals
        self.schema = None

    def with_structured_output(self, schema):
        self.schema = schema
        return self

    def invoke(self, _messages):
        if self.schema is SessionMemorySummary:
            return SessionMemorySummary(
                summary="用户当前想寻找轻松动画，并在比较此前提到的作品。",
                current_goal="寻找轻松动画",
                temporary_preferences={"moods": ["轻松"]},
                current_constraints=["本次不要过度沉重"],
                referenced_anime=["轻音少女"],
                unresolved_questions=["是否接受慢节奏"],
            )
        if self.schema is LongTermMemoryExtraction:
            if self.proposals is not None:
                return LongTermMemoryExtraction(
                    memories=[LongTermMemoryProposal(**item) for item in self.proposals]
                )
            return LongTermMemoryExtraction(
                memories=[
                    LongTermMemoryProposal(
                        action="remember",
                        source_message_id=int(self.source_message_id or 0),
                        memory_type="content_dislike",
                        value="后宫",
                        polarity="negative",
                        strength="soft",
                        explicit=True,
                        confidence=0.95,
                    )
                ]
            )
        raise AssertionError(f"unexpected schema {self.schema}")


class _InvalidMemoryModel:
    def with_structured_output(self, _schema):
        return self

    def invoke(self, _messages):
        return None


class ContextMemoryTest(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(
            prefix="context_memory_",
            suffix=".db",
            delete=False,
        )
        handle.close()
        self.path = Path(handle.name)
        self.engine = get_sync_engine(db_path=str(self.path))
        Base.metadata.create_all(
            self.engine,
            tables=[
                User.__table__,
                AgentSession.__table__,
                AgentMessage.__table__,
                AgentSessionMemory.__table__,
                UserPreference.__table__,
                UserMemoryFact.__table__,
            ],
        )
        self.factory = get_sessionmaker(db_path=str(self.path))

        @contextmanager
        def temporary_orm_session():
            with self.factory() as session:
                try:
                    yield session
                    session.commit()
                except Exception:
                    session.rollback()
                    raise

        self.orm_patch = patch(
            "backend.agents.context_memory.orm_session",
            temporary_orm_session,
        )
        self.orm_patch.start()
        self.preference_patch = patch(
            "backend.agents.memory.orm_session",
            temporary_orm_session,
        )
        self.preference_patch.start()
        with self.factory.begin() as session:
            user = User(username="memory-user", password_hash="x")
            session.add(user)
            session.flush()
            self.user_id = user.id
            agent_session = AgentSession(
                user_id=user.id,
                agent_type="recommendation",
                title="memory",
                status="active",
            )
            session.add(agent_session)
            session.flush()
            self.session_id = agent_session.id

    def tearDown(self):
        self.preference_patch.stop()
        self.orm_patch.stop()
        self.engine.dispose()
        self.path.unlink(missing_ok=True)

    def _messages(self, contents):
        ids = []
        with self.factory.begin() as session:
            for index, content in enumerate(contents):
                message = AgentMessage(
                    session_id=self.session_id,
                    role="user" if index % 2 == 0 else "agent",
                    content=content,
                )
                session.add(message)
                session.flush()
                ids.append(message.id)
        return ids

    def test_summary_requires_six_messages_and_four_thousand_chars(self):
        ids = self._messages(["完整句子。" + ("甲" * 695) for _ in range(6)])
        with patch(
            "backend.agents.context_memory.get_chat_model",
            return_value=_MemoryModel(ids[4]),
        ):
            result = maintain_context_memory(
                self.user_id,
                self.session_id,
                ids[4],
                {},
            )

        self.assertTrue(result["summary_triggered"])
        self.assertTrue(result["summary_updated"])
        with self.factory() as session:
            memory = session.get(AgentSessionMemory, self.session_id)
            self.assertEqual(memory.last_processed_message_id, ids[-1])
            self.assertIn("轻松动画", memory.summary)

    def test_six_short_messages_do_not_trigger_summary(self):
        ids = self._messages(["这是一条短消息。" for _ in range(6)])
        with patch("backend.agents.context_memory.get_chat_model", return_value=None):
            result = maintain_context_memory(self.user_id, self.session_id, ids[4], {})
        self.assertFalse(result["summary_triggered"])
        with self.factory() as session:
            memory = session.get(AgentSessionMemory, self.session_id)
            self.assertIsNone(memory.last_processed_message_id)

    def test_five_long_messages_do_not_trigger_summary(self):
        ids = self._messages(["完整句子。" + ("乙" * 995) for _ in range(5)])
        with patch("backend.agents.context_memory.get_chat_model", return_value=None):
            result = maintain_context_memory(self.user_id, self.session_id, ids[4], {})
        self.assertFalse(result["summary_triggered"])

    def test_explicit_long_term_memory_is_cross_session_and_idempotent(self):
        ids = self._messages(["我一直不喜欢后宫番。", "明白了。"])
        model = _MemoryModel(ids[0])
        with patch("backend.agents.context_memory.get_chat_model", return_value=model):
            first = maintain_context_memory(self.user_id, self.session_id, ids[0], {})
            second = maintain_context_memory(self.user_id, self.session_id, ids[0], {})

        self.assertEqual(first["memory_fact_count"], 1)
        self.assertEqual(second["status"], "skipped")
        with self.factory.begin() as session:
            other = AgentSession(
                user_id=self.user_id,
                agent_type="recommendation",
                title="other",
                status="active",
            )
            session.add(other)
            session.flush()
            other_id = other.id
        context = load_context_memory(self.user_id, other_id, "请推荐动画")
        self.assertEqual(context["long_term_memories"][0]["value"], "后宫")
        with self.factory() as session:
            fact = session.scalar(select(UserMemoryFact))
            preference = session.scalar(select(UserPreference))
            self.assertEqual(fact.occurrence_count, 1)
            self.assertIn("后宫", preference.dislikes)

    def test_failed_extraction_keeps_cursor_retryable(self):
        ids = self._messages(["我一直不喜欢后宫番。", "明白了。"])
        with patch("backend.agents.context_memory.get_chat_model", return_value=None):
            first = maintain_context_memory(self.user_id, self.session_id, ids[0], {})

        self.assertEqual(first["status"], "retryable")
        self.assertTrue(first["memory_retryable"])
        with self.factory() as session:
            memory = session.get(AgentSessionMemory, self.session_id)
            self.assertIsNone(memory.last_memory_message_id)
            self.assertEqual(session.scalar(select(UserMemoryFact)), None)

        with patch(
            "backend.agents.context_memory.get_chat_model",
            return_value=_MemoryModel(ids[0]),
        ):
            second = maintain_context_memory(self.user_id, self.session_id, ids[0], {})

        self.assertEqual(second["status"], "updated")
        self.assertEqual(second["memory_fact_count"], 1)
        with self.factory() as session:
            memory = session.get(AgentSessionMemory, self.session_id)
            self.assertEqual(memory.last_memory_message_id, ids[0])

    def test_valid_empty_extraction_advances_cursor(self):
        ids = self._messages(["我一直在考虑看什么。", "明白了。"])
        with patch(
            "backend.agents.context_memory.get_chat_model",
            return_value=_MemoryModel(ids[0], proposals=[]),
        ):
            result = maintain_context_memory(self.user_id, self.session_id, ids[0], {})

        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["memory_fact_count"], 0)
        with self.factory() as session:
            memory = session.get(AgentSessionMemory, self.session_id)
            self.assertEqual(memory.last_memory_message_id, ids[0])

    def test_invalid_structured_extraction_keeps_cursor_retryable(self):
        ids = self._messages(["我一直不喜欢后宫番。", "明白了。"])
        with patch(
            "backend.agents.context_memory.get_chat_model",
            return_value=_InvalidMemoryModel(),
        ):
            result = maintain_context_memory(self.user_id, self.session_id, ids[0], {})

        self.assertEqual(result["status"], "retryable")
        self.assertEqual(result["memory_error"], "ValueError")
        with self.factory() as session:
            memory = session.get(AgentSessionMemory, self.session_id)
            self.assertIsNone(memory.last_memory_message_id)

    @staticmethod
    def _proposal(source_message_id: int, memory_type: str, value: str, polarity: str):
        return {
            "action": "remember",
            "source_message_id": source_message_id,
            "memory_type": memory_type,
            "value": value,
            "polarity": polarity,
            "strength": "soft",
            "explicit": True,
            "confidence": 0.95,
        }

    def test_new_negative_preference_supersedes_positive(self):
        positive_id = self._messages(["我一直喜欢战斗番。"])[0]
        positive = self._proposal(positive_id, "genre_preference", "战斗", "positive")
        with patch(
            "backend.agents.context_memory.get_chat_model",
            return_value=_MemoryModel(positive_id, [positive]),
        ):
            maintain_context_memory(self.user_id, self.session_id, positive_id, {})

        negative_id = self._messages(["以后不要给我战斗番。"])[0]
        negative = self._proposal(negative_id, "content_dislike", "战斗", "negative")
        with patch(
            "backend.agents.context_memory.get_chat_model",
            return_value=_MemoryModel(negative_id, [negative]),
        ):
            maintain_context_memory(self.user_id, self.session_id, negative_id, {})

        with self.factory() as session:
            facts = session.scalars(select(UserMemoryFact).order_by(UserMemoryFact.id)).all()
            preference = session.scalar(select(UserPreference))
            self.assertEqual([fact.polarity for fact in facts if fact.status == "active"], ["negative"])
            self.assertNotIn("战斗", preference.preferred_genres)
            self.assertIn("战斗", preference.dislikes)

    def test_new_positive_preference_supersedes_negative(self):
        negative_id = self._messages(["我一直不喜欢战斗番。"])[0]
        negative = self._proposal(negative_id, "content_dislike", "战斗", "negative")
        with patch(
            "backend.agents.context_memory.get_chat_model",
            return_value=_MemoryModel(negative_id, [negative]),
        ):
            maintain_context_memory(self.user_id, self.session_id, negative_id, {})

        positive_id = self._messages(["以后我喜欢战斗番。"])[0]
        positive = self._proposal(positive_id, "genre_preference", "战斗", "positive")
        with patch(
            "backend.agents.context_memory.get_chat_model",
            return_value=_MemoryModel(positive_id, [positive]),
        ):
            maintain_context_memory(self.user_id, self.session_id, positive_id, {})

        with self.factory() as session:
            facts = session.scalars(select(UserMemoryFact).order_by(UserMemoryFact.id)).all()
            preference = session.scalar(select(UserPreference))
            self.assertEqual([fact.polarity for fact in facts if fact.status == "active"], ["positive"])
            self.assertIn("战斗", preference.preferred_genres)
            self.assertNotIn("战斗", preference.dislikes)

    def test_same_message_conflict_prefers_negative(self):
        message_id = self._messages(["我以前喜欢战斗番，以后不要给我战斗番。"])[0]
        proposals = [
            self._proposal(message_id, "genre_preference", "战斗", "positive"),
            self._proposal(message_id, "content_dislike", "战斗", "negative"),
        ]
        with patch(
            "backend.agents.context_memory.get_chat_model",
            return_value=_MemoryModel(message_id, proposals),
        ):
            maintain_context_memory(self.user_id, self.session_id, message_id, {})

        with self.factory() as session:
            active = session.scalars(
                select(UserMemoryFact).where(UserMemoryFact.status == "active")
            ).all()
            preference = session.scalar(select(UserPreference))
            self.assertEqual([fact.polarity for fact in active], ["negative"])
            self.assertNotIn("战斗", preference.preferred_genres)
            self.assertIn("战斗", preference.dislikes)

    def test_delayed_old_message_cannot_override_newer_conflict(self):
        old_id = self._messages(["我一直喜欢战斗番。"])[0]
        with self.factory.begin() as session:
            other = AgentSession(
                user_id=self.user_id,
                agent_type="recommendation",
                title="newer",
                status="active",
            )
            session.add(other)
            session.flush()
            newer = AgentMessage(
                session_id=other.id,
                role="user",
                content="以后不要给我战斗番。",
            )
            session.add(newer)
            session.flush()
            other_id = other.id
            newer_id = newer.id

        negative = self._proposal(newer_id, "content_dislike", "战斗", "negative")
        with patch(
            "backend.agents.context_memory.get_chat_model",
            return_value=_MemoryModel(newer_id, [negative]),
        ):
            maintain_context_memory(self.user_id, other_id, newer_id, {})

        positive = self._proposal(old_id, "genre_preference", "战斗", "positive")
        with patch(
            "backend.agents.context_memory.get_chat_model",
            return_value=_MemoryModel(old_id, [positive]),
        ):
            maintain_context_memory(self.user_id, self.session_id, old_id, {})

        with self.factory() as session:
            active = session.scalars(
                select(UserMemoryFact).where(UserMemoryFact.status == "active")
            ).all()
            preference = session.scalar(select(UserPreference))
            self.assertEqual([fact.polarity for fact in active], ["negative"])
            self.assertNotIn("战斗", preference.preferred_genres)
            self.assertIn("战斗", preference.dislikes)

    def test_compatibility_projection_resolves_same_turn_conflict(self):
        result = update_user_preferences(
            self.user_id,
            {"preferred_genres": ["战斗"], "dislikes": ["战斗"]},
        )
        self.assertNotIn("战斗", result["preferred_genres"])
        self.assertIn("战斗", result["dislikes"])

        result = update_user_preferences(self.user_id, {"preferred_genres": ["战斗"]})
        self.assertIn("战斗", result["preferred_genres"])
        self.assertNotIn("战斗", result["dislikes"])

    def test_manual_memory_edit_reuses_conflict_resolution(self):
        positive_id = self._messages(["我一直喜欢战斗番。"])[0]
        positive = self._proposal(positive_id, "genre_preference", "战斗", "positive")
        with patch(
            "backend.agents.context_memory.get_chat_model",
            return_value=_MemoryModel(positive_id, [positive]),
        ):
            maintain_context_memory(self.user_id, self.session_id, positive_id, {})

        negative_id = self._messages(["我一直不喜欢后宫番。"])[0]
        negative = self._proposal(negative_id, "content_dislike", "后宫", "negative")
        with patch(
            "backend.agents.context_memory.get_chat_model",
            return_value=_MemoryModel(negative_id, [negative]),
        ):
            maintain_context_memory(self.user_id, self.session_id, negative_id, {})
        negative_memory_id = next(
            item["id"]
            for item in list_user_memories(self.user_id)
            if item["polarity"] == "negative"
        )

        updated = update_user_memory(
            self.user_id,
            negative_memory_id,
            {"value": "战斗"},
        )

        self.assertEqual(updated["value"]["text"], "战斗")
        with self.factory() as session:
            active = session.scalars(
                select(UserMemoryFact).where(UserMemoryFact.status == "active")
            ).all()
            preference = session.scalar(select(UserPreference))
            self.assertEqual([fact.polarity for fact in active], ["negative"])
            self.assertNotIn("战斗", preference.preferred_genres)
            self.assertIn("战斗", preference.dislikes)

    def test_temporary_request_does_not_become_long_term_memory(self):
        ids = self._messages(["这次我喜欢轻松的氛围。", "好的。"])
        with patch(
            "backend.agents.context_memory.get_chat_model",
            return_value=_MemoryModel(ids[0]),
        ):
            result = maintain_context_memory(self.user_id, self.session_id, ids[0], {})
        self.assertEqual(result["memory_fact_count"], 0)
        self.assertEqual(list_user_memories(self.user_id), [])

    def test_user_can_forget_exact_memory(self):
        ids = self._messages(["我一直不喜欢后宫番。", "明白了。"])
        with patch(
            "backend.agents.context_memory.get_chat_model",
            return_value=_MemoryModel(ids[0]),
        ):
            maintain_context_memory(self.user_id, self.session_id, ids[0], {})
        memory_id = list_user_memories(self.user_id)[0]["id"]

        self.assertTrue(forget_user_memory(self.user_id, memory_id))
        self.assertFalse(forget_user_memory(self.user_id, memory_id))
        self.assertEqual(list_user_memories(self.user_id), [])
        with self.factory() as session:
            preference = session.scalar(select(UserPreference))
            self.assertNotIn("后宫", preference.dislikes)


if __name__ == "__main__":
    unittest.main()
