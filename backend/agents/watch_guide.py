# -*- coding: utf-8 -*-
"""待看番剧指南的确定性状态、作品解析、内容生成与同步持久化。

本模块不负责 HTTP 路由。API 层只需要传入完整历史恢复状态，并在后台任务中
调用生成和保存函数。是否确认加入由本地规则决定，不能交给回答模型猜测。
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.agents.model_factory import get_chat_model
from backend.agents.prompt_security import inspect_untrusted_text
from backend.agents.recommend_context import compact_history
from backend.config import (
    LLM_MODEL,
    RECOMMEND_FOLLOWUP_MAX_TOKENS,
    RECOMMEND_LLM_TIMEOUT,
)
from backend.database import orm_session
from backend.db.models import Anime, AgentMessage, AgentSession, AgentTask, WatchGuide
from backend.prompts.registry import get_prompt, prompt_trace


WATCH_GUIDE_EVENT_FIELD = "watch_guide_events"
WATCH_GUIDE_OFFER_TYPES = {"offered"}
WATCH_GUIDE_TERMINAL_TYPES = {"accepted", "declined", "ignored", "created", "failed"}


def normalize_anime_title(title: str) -> str:
    """生成不受大小写、空白和常见标题标点影响的作品名。"""
    normalized = unicodedata.normalize("NFKC", str(title or "")).casefold()
    return "".join(character for character in normalized if character.isalnum())


def anime_title_key(title: str) -> str:
    """为规范化作品名生成跨 SQLite/MySQL 一致的稳定键。"""
    normalized = normalize_anime_title(title)
    if not normalized:
        raise ValueError("anime title cannot be empty")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _anime_reference(value: Any, *, source: str = "history") -> dict | None:
    raw = _as_dict(value)
    if "anime" in raw and isinstance(raw.get("anime"), dict):
        raw = raw["anime"]
    name = str(raw.get("name") or raw.get("anime_name") or "").strip()
    if not name:
        return None
    try:
        key = anime_title_key(name)
    except ValueError:
        return None
    anime_id = raw.get("anime_id", raw.get("id"))
    try:
        anime_id = int(anime_id) if anime_id is not None else None
    except (TypeError, ValueError):
        anime_id = None
    return {
        "anime_id": anime_id,
        "name": name[:255],
        "key": key,
        "source": str(raw.get("source") or source)[:32],
    }


def build_watch_guide_event(
    event_type: str,
    anime: dict,
    *,
    offer_id: str | None = None,
    **details: Any,
) -> dict:
    """构造可安全落入 AgentMessage.metadata 的指南状态事件。"""
    if event_type not in WATCH_GUIDE_OFFER_TYPES | WATCH_GUIDE_TERMINAL_TYPES:
        raise ValueError(f"unsupported watch-guide event: {event_type}")
    reference = _anime_reference(anime, source="event")
    if reference is None:
        raise ValueError("watch-guide event requires a valid anime")
    event = {"type": event_type, "anime": reference}
    if offer_id:
        event["offer_id"] = str(offer_id)[:128]
    for key, value in details.items():
        if value is not None:
            event[key] = value
    return event


def format_watch_guide_offer_question(anime: dict) -> str:
    reference = _anime_reference(anime, source="offer")
    if reference is None:
        raise ValueError("offer question requires a valid anime")
    return (
        f"要将《{reference['name']}》加入“待看番剧指南”，"
        "并为它生成一份详细的观看指南吗？"
    )


def _message_events(message: dict) -> list[dict]:
    metadata = _as_dict(message.get("metadata"))
    containers = [metadata]
    result = _as_dict(metadata.get("result"))
    nested = _as_dict(result.get("result"))
    containers.extend((result, nested))
    for container in containers:
        raw_events = container.get(WATCH_GUIDE_EVENT_FIELD)
        if isinstance(raw_events, dict):
            raw_events = [raw_events]
        if isinstance(raw_events, list):
            return [event for event in raw_events if isinstance(event, dict)]
    return []


def reconstruct_watch_guide_state(messages: list[dict] | None) -> dict:
    """从持久化消息恢复待确认对象、活动作品和已经询问过的作品键。

    ``pending_offer`` 只在 offered 事件所在消息仍是最后一条消息时成立。这一
    邻接约束可防止较早的“需要”被误用于已经过期的询问。
    """
    history = [message for message in (messages or []) if isinstance(message, dict)]
    offered_keys: list[str] = []
    offered_set: set[str] = set()
    pending: dict | None = None
    pending_message_index = -1
    active_target: dict | None = None

    for index, message in enumerate(history):
        metadata = _as_dict(message.get("metadata"))
        message_target = _anime_reference(
            metadata.get("anime_target"),
            source="history",
        )
        if message_target is not None:
            active_target = message_target
        events = _message_events(message)
        if index > pending_message_index >= 0:
            pending = None
            pending_message_index = -1
        for event in events:
            event_type = str(event.get("type") or event.get("status") or "").strip().lower()
            anime = _anime_reference(event.get("anime") or event, source="history")
            if anime is None:
                continue
            active_target = anime
            key = anime["key"]
            if event_type in WATCH_GUIDE_OFFER_TYPES | WATCH_GUIDE_TERMINAL_TYPES:
                if key not in offered_set:
                    offered_set.add(key)
                    offered_keys.append(key)
            offer_id = str(event.get("offer_id") or "").strip()
            if event_type == "offered":
                pending = {
                    "offer_id": offer_id or f"message:{message.get('id', index)}:{key}",
                    "anime": anime,
                    "source_answer": str(message.get("content") or "")[:12000],
                }
                pending_message_index = index
            elif event_type in WATCH_GUIDE_TERMINAL_TYPES and pending is not None:
                same_offer = offer_id and offer_id == pending.get("offer_id")
                same_anime = key == (pending.get("anime") or {}).get("key")
                if same_offer or same_anime:
                    pending = None
                    pending_message_index = -1

    if pending_message_index != len(history) - 1:
        pending = None
    return {
        "pending_offer": pending,
        "active_target": active_target,
        "offered_keys": offered_keys,
    }


_DECLINE_REPLIES = {
    "不",
    "不要",
    "不需要",
    "不用",
    "不用了",
    "不要了",
    "不加",
    "先不加",
    "暂时不加",
    "暂时不用",
    "不了",
    "算了",
    "下次吧",
    "不必了",
    "否",
}
_ACCEPT_REPLIES = {
    "是",
    "是的",
    "要",
    "需要",
    "需要的",
    "好",
    "好的",
    "可以",
    "行",
    "没问题",
    "当然",
    "当然可以",
    "加入",
    "加进去",
    "放进去",
    "保存",
    "生成吧",
    "帮我加入",
    "请加入",
    "麻烦加入",
}


def _normalize_reply(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text or "")).casefold().strip()
    normalized = re.sub(r"[\s，。！？!?、；;,.…~～]+", "", normalized)
    return normalized


def classify_offer_reply(text: str, pending_offer: dict | None) -> str:
    """仅把完整、简短的肯定或否定回答解释为上一条指南询问的回复。"""
    if not pending_offer or _anime_reference(pending_offer, source="pending") is None:
        return "other"
    normalized = _normalize_reply(text)
    if not normalized or len(normalized) > 40:
        return "other"
    if normalized in _DECLINE_REPLIES or re.fullmatch(
        r"(?:谢谢)?(?:我)?(?:暂时|先)?(?:不需要|不用|不要|不加|不保存|不生成)(?:了)?(?:谢谢)?",
        normalized,
    ):
        return "decline"
    if normalized in _ACCEPT_REPLIES or re.fullmatch(
        r"(?:好的?)?(?:请|麻烦|帮我)?(?:把(?:它|这部|该动画|这部动画)?)?"
        r"(?:加入|添加|放入|保存到)(?:我的)?(?:待看番剧指南|观看指南|待看列表|指南)(?:里|中)?(?:吧)?(?:谢谢)?",
        normalized,
    ):
        return "accept"
    return "other"


def _recommendation_items(context: dict | None) -> list[dict]:
    data = _as_dict(context)
    result = _as_dict(data.get("result"))
    nested = _as_dict(result.get("result"))
    candidate = nested or result or data
    return [item for item in (candidate.get("recommendations") or []) if isinstance(item, dict)]


def _candidate_references(recommendation_context: dict | None) -> tuple[list[dict], list[dict]]:
    recommendations: list[dict] = []
    by_key: dict[str, dict] = {}
    for item in _recommendation_items(recommendation_context):
        reference = _anime_reference(item, source="recommendation")
        if reference is None:
            continue
        recommendations.append(reference)
        by_key.setdefault(reference["key"], reference)
    try:
        with orm_session() as session:
            local_items = [
                dict(row)
                for row in session.execute(
                    select(Anime.id, Anime.name, Anime.platform).order_by(Anime.id)
                ).mappings()
            ]
    except Exception:
        local_items = []
    for item in local_items or []:
        reference = _anime_reference(item, source="local")
        if reference is None:
            continue
        existing = by_key.get(reference["key"])
        if existing is None:
            by_key[reference["key"]] = reference
        elif existing.get("anime_id") is None and reference.get("anime_id") is not None:
            existing["anime_id"] = reference["anime_id"]
    return recommendations, list(by_key.values())


_ORDINALS = {
    "一": 0,
    "二": 1,
    "两": 1,
    "三": 2,
    "1": 0,
    "2": 1,
    "3": 2,
}
_QUOTE_PATTERNS = (
    re.compile(r"《([^《》]{1,255})》"),
    re.compile(r"「([^「」]{1,255})」"),
    re.compile(r"『([^『』]{1,255})』"),
    re.compile(r"[“\"]([^“”\"]{1,255})[”\"]"),
)
_FOLLOWUP_TERMS = re.compile(
    r"介绍|讲讲|说说|详细|剧情|角色|人物|设定|几集|多少集|观看|顺序|"
    r"看点|好看|怎么样|适合|平台|在哪看|哪里看|结局|评价|值得|类似|是什么|了解|注意|建议"
)
_IMPLICIT_ACTIVE_TERMS = re.compile(
    r"几集|多少集|角色|人物|剧情|设定|观看顺序|在哪看|哪里看|播放平台|"
    r"注意什么|观看建议|有什么看点|结局"
)


def _short_title_allowed(query: str, title: str) -> bool:
    normalized = normalize_anime_title(title)
    if len(normalized) > 2:
        return True
    if not re.search(r"动漫|动画|番剧|作品|这部|这个番|该作", query):
        return False
    escaped = re.escape(unicodedata.normalize("NFKC", title))
    return bool(re.search(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", unicodedata.normalize("NFKC", query), re.I))


def _known_title_matches(query: str, candidates: list[dict]) -> list[dict]:
    normalized_query = normalize_anime_title(query)
    if not normalized_query:
        return []
    occurrences: list[tuple[dict, list[tuple[int, int]]]] = []
    for candidate in candidates:
        normalized_name = normalize_anime_title(candidate["name"])
        if not normalized_name or not _short_title_allowed(query, candidate["name"]):
            continue
        spans = [
            (match.start(), match.end())
            for match in re.finditer(re.escape(normalized_name), normalized_query)
        ]
        if spans:
            occurrences.append((candidate, spans))

    maximal: list[dict] = []
    for candidate, spans in occurrences:
        independent = False
        for span in spans:
            contained = any(
                other["key"] != candidate["key"]
                and len(normalize_anime_title(other["name"])) > len(normalize_anime_title(candidate["name"]))
                and any(other_span[0] <= span[0] and span[1] <= other_span[1] for other_span in other_spans)
                for other, other_spans in occurrences
            )
            if not contained:
                independent = True
                break
        if independent and all(item["key"] != candidate["key"] for item in maximal):
            maximal.append(candidate)
    return maximal


def resolve_anime_subject(
    query: str,
    recommendation_context: dict | None,
    active_target: dict | None,
) -> dict | None:
    """只在能唯一确定具体作品时返回规范化作品引用。"""
    text = str(query or "").strip()
    if not text:
        return None
    recommendations, candidates = _candidate_references(recommendation_context)
    resolved: dict[str, dict] = {}

    external_titles = {
        title.strip()
        for title in _QUOTE_PATTERNS[0].findall(text)
        if title.strip()
    }
    quoted_titles: list[str] = list(external_titles)
    for pattern in _QUOTE_PATTERNS[1:]:
        quoted_titles.extend(match.strip() for match in pattern.findall(text) if match.strip())
    quoted_by_key = {
        anime_title_key(title): title
        for title in quoted_titles
        if normalize_anime_title(title)
    }
    if len(quoted_by_key) > 1:
        return None
    for key, title in quoted_by_key.items():
        known = next((item for item in candidates if item["key"] == key), None)
        if known is not None:
            resolved[key] = known
        elif title in external_titles:
            resolved[key] = _anime_reference(
                {"name": title, "source": "explicit_title"},
                source="explicit_title",
            )

    ordinal_matches = re.findall(r"第\s*([一二两三123])\s*(?:部|个|项|部作品|个作品)", text)
    ordinal_indexes = {_ORDINALS[value] for value in ordinal_matches}
    if len(ordinal_indexes) > 1:
        return None
    if ordinal_indexes:
        index = next(iter(ordinal_indexes))
        if index >= len(recommendations):
            return None
        ordinal = recommendations[index]
        resolved[ordinal["key"]] = ordinal

    for matched in _known_title_matches(text, candidates):
        resolved[matched["key"]] = matched
    if len(resolved) > 1:
        return None
    if resolved:
        return next(iter(resolved.values()))

    active = _anime_reference(active_target, source="active_target")
    if re.search(r"这部|这一个|这番|该作|该动画|这个番|它", text):
        if active is not None:
            return active
        if len(recommendations) == 1:
            return recommendations[0]
    if active is not None and _IMPLICIT_ACTIVE_TERMS.search(text):
        return active
    return None


def watch_guide_exists(user_id: int, anime: dict | str) -> bool:
    key = anime if isinstance(anime, str) and len(anime) == 64 else None
    if key is None:
        reference = _anime_reference(anime, source="lookup") if isinstance(anime, dict) else None
        key = reference["key"] if reference else anime_title_key(str(anime))
    with orm_session() as session:
        return session.scalar(
            select(WatchGuide.id).where(
                WatchGuide.user_id == int(user_id),
                WatchGuide.anime_key == key,
            )
        ) is not None


def should_offer_watch_guide(
    query: str,
    anime: dict | None,
    state: dict | None,
    user_id: int,
) -> bool:
    """判断普通追问回答后是否应首次追加指南询问。"""
    reference = _anime_reference(anime, source="offer")
    if reference is None:
        return False
    text = str(query or "").strip()
    if not _FOLLOWUP_TERMS.search(text) and "?" not in text and "？" not in text:
        return False
    if re.search(r"已经看过|看过了|看完了|不想看|不喜欢|讨厌|别推荐|不要推荐|避开|排除", text):
        return False
    offered = set((_as_dict(state).get("offered_keys") or []))
    if reference["key"] in offered:
        return False
    return not watch_guide_exists(user_id, reference)


def _response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = "\n".join(
            str(item.get("text") or "") if isinstance(item, dict) else str(item)
            for item in content
        )
    text = re.sub(r"<think>.*?</think>", "", str(content or ""), flags=re.DOTALL).strip()
    if text.startswith("```") and text.endswith("```"):
        text = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", text, flags=re.I).strip()
    return text[:12000]


def _fallback_guide(anime: dict) -> str:
    name = anime.get("name") or "这部作品"
    return f"""**📺 观看前，你需要知道的**

**作品**：《{name}》

**基本档案**：当前本地资料不足以可靠确认总集数、单集时长、特别篇和版本差异。开始观看前，建议在所选平台核对作品年份、季度、副标题和集数，避免误入续作或剪辑版。

**在哪里看**：版权与片库会随地区和时间变化，请优先在你所在地区的正版平台搜索完整标题，并以平台当前页面为准。暂不列出未经实时核验的播放渠道。

**💡 一些观看建议**

**先保持无剧透体验**：首次观看建议关闭弹幕，并避免搜索角色结局、关键事件和最终评价。

**给作品留出进入状态的时间**：先完整观看开篇阶段，再决定是否继续，不必只凭第一集的节奏下结论。

**记录感兴趣的线索**：遇到人物关系、世界观名词或反复出现的意象时，可以简单记下，后续理解会更连贯。

**按自己的情绪调整节奏**：如果作品带来明显压抑、紧张或悲伤，可以暂停观看，不需要勉强连续追完。

**📅 分阶段观看计划**

**第一阶段：熟悉世界与人物（约前四分之一）**

先辨认主要角色、故事目标与基本规则，暂时不要急于查设定解释。

**第二阶段：观察关系与冲突（约中间二分之一）**

关注人物选择如何推动冲突，以及早期细节是否在后续得到回应。篇幅较长时，可分成数次观看并在阶段间稍作休息。

**第三阶段：集中完成收束（约最后四分之一）**

尽量预留一段不被打断的时间完成结尾；正片结束后，再按官方顺序确认 OVA、特别篇或剧场版是否属于补充内容。

**特别提醒**：由于当前无法可靠核验具体集数和衍生篇顺序，这份降级指南使用比例规划，不会编造精确分集信息。"""


def generate_watch_guide(
    anime: dict,
    source_answer: str,
    history: list[dict] | None,
    recommendation_context: dict | None,
) -> dict:
    """生成详细观看指南；模型不可用时返回不臆造事实的安全指南。"""
    reference = _anime_reference(anime, source="generation")
    if reference is None:
        raise ValueError("watch guide generation requires a valid anime")
    prompt_template = get_prompt("watch_guide")
    anime_check = inspect_untrusted_text(
        json.dumps({**reference, **_as_dict(anime)}, ensure_ascii=False),
        source="anime_context",
        max_chars=3000,
    )
    answer_check = inspect_untrusted_text(source_answer, source="assistant_context", max_chars=5000)
    history_check = inspect_untrusted_text(
        json.dumps(compact_history(history), ensure_ascii=False),
        source="conversation_history",
        max_chars=4000,
    )
    recommendation_check = inspect_untrusted_text(
        json.dumps(recommendation_context or {}, ensure_ascii=False),
        source="recommendation_context",
        max_chars=5000,
    )
    inspections = (anime_check, answer_check, history_check, recommendation_check)
    trace = prompt_trace("watch_guide", LLM_MODEL, 0, False, prompt=prompt_template)
    trace["security"] = {
        "risk": max(inspections, key=lambda item: item["score"])["risk"],
        "flags": sorted({flag for inspection in inspections for flag in inspection["flags"]}),
    }
    model = get_chat_model(
        0.2,
        timeout=RECOMMEND_LLM_TIMEOUT,
        max_tokens=RECOMMEND_FOLLOWUP_MAX_TOKENS,
    )
    if model is None:
        trace["fallback"] = True
        return {"content": _fallback_guide(reference), "prompt_trace": trace, "fallback": True}

    prompt = prompt_template.render(
        anime=anime_check["sanitized_text"],
        source_answer=answer_check["sanitized_text"],
        history=history_check["sanitized_text"],
        recommendations=recommendation_check["sanitized_text"],
    )
    try:
        content = _response_text(
            model.invoke(
                [
                    ("system", prompt_template.render_system()),
                    ("human", prompt),
                ]
            )
        )
        if len(content) < 200:
            raise ValueError("LLM returned an incomplete watch guide")
        return {"content": content, "prompt_trace": trace, "fallback": False}
    except Exception as exc:
        trace["fallback"] = True
        return {
            "content": _fallback_guide(reference),
            "prompt_trace": trace,
            "fallback": True,
            "fallback_reason": f"{type(exc).__name__}: {exc}",
        }


def _insert_guide_if_absent(session, values: dict) -> bool | None:
    if session.bind.dialect.name == "mysql":
        existing = session.scalar(
            select(WatchGuide.id).where(
                WatchGuide.user_id == values["user_id"],
                WatchGuide.anime_key == values["anime_key"],
            )
        )
        if existing is not None:
            return False
        statement = mysql_insert(WatchGuide).values(**values)
        session.execute(statement.on_duplicate_key_update(id=WatchGuide.id))
        # MySQL 的 CLIENT_FOUND_ROWS 会让并发输家的 no-op UPDATE 也报告命中；
        # 此时无法可靠区分“本请求插入”与“另一请求刚插入”，因此不猜测。
        return None
    if session.bind.dialect.name == "sqlite":
        result = session.execute(
            sqlite_insert(WatchGuide)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["user_id", "anime_key"])
        )
        return bool(result.rowcount)
    existing = session.scalar(
        select(WatchGuide.id).where(
            WatchGuide.user_id == values["user_id"],
            WatchGuide.anime_key == values["anime_key"],
        )
    )
    if existing is not None:
        return False
    session.add(WatchGuide(**values))
    session.flush()
    return True


def _created_offer_message(session, session_id: int, offer_id: str) -> AgentMessage | None:
    if not offer_id:
        return None
    messages = session.scalars(
        select(AgentMessage)
        .where(AgentMessage.session_id == session_id)
        .order_by(AgentMessage.id.desc())
    ).all()
    for message in messages:
        for event in _message_events({"metadata": message.message_metadata}):
            if (
                str(event.get("type") or event.get("status") or "") == "created"
                and str(event.get("offer_id") or "") == offer_id
            ):
                return message
    return None


def _guide_summary(guide: WatchGuide) -> dict:
    created_at = guide.created_at
    if isinstance(created_at, datetime):
        created_at = created_at.strftime("%Y-%m-%d %H:%M:%S")
    return {
        "id": guide.id,
        "anime_name": guide.anime_name,
        "anime_key": guide.anime_key,
        "created_at": created_at,
    }


def _complete_task_in_transaction(
    session,
    task_id: int | None,
    user_id: int,
    session_id: int,
    payload: dict,
) -> None:
    if task_id is None:
        return
    task = session.scalar(
        select(AgentTask)
        .where(
            AgentTask.id == int(task_id),
            AgentTask.user_id == int(user_id),
            AgentTask.session_id == int(session_id),
        )
        .with_for_update()
    )
    if task is None:
        raise LookupError("watch-guide task does not exist or is not owned by user")
    now = datetime.now()
    task.status = "succeeded"
    task.result = dict(payload)
    task.error = None
    task.progress = 100
    task.current_step = "completed"
    task.finished_at = now
    task.updated_at = now
    task.worker_id = None
    task.lease_until = None
    task.heartbeat_at = None


def save_watch_guide_with_message(
    user_id: int,
    session_id: int,
    anime: dict,
    guide_result: dict,
    payload: dict,
    *,
    task_id: int | None = None,
) -> dict:
    """原子保存指南、确认消息与会话时间；相同用户/作品不覆盖已有内容。"""
    reference = _anime_reference(anime, source="persistence")
    content = str(_as_dict(guide_result).get("content") or "").strip()
    if reference is None or not content:
        raise ValueError("valid anime and non-empty guide content are required")
    stored_payload = dict(payload or {})
    stored_payload.setdefault("response_mode", "conversation")
    stored_payload.setdefault("answer", f"已将《{reference['name']}》加入“待看番剧指南”。")
    offer_id = str(stored_payload.pop("offer_id", "") or "")[:128]

    with orm_session() as session:
        owner = session.scalar(
            select(AgentSession)
            .where(
                AgentSession.id == int(session_id),
                AgentSession.user_id == int(user_id),
                AgentSession.agent_type == "recommendation",
            )
            .with_for_update()
        )
        if owner is None:
            raise LookupError("recommendation session does not exist or is not owned by user")

        duplicate_message = None
        if task_id is not None:
            duplicate_message = session.scalar(
                select(AgentMessage).where(
                    AgentMessage.source_task_id == int(task_id)
                )
            )
        if duplicate_message is None:
            duplicate_message = _created_offer_message(session, int(session_id), offer_id)
        if duplicate_message is not None:
            duplicate_payload = _as_dict(duplicate_message.message_metadata)
            result_payload = duplicate_payload or stored_payload
            _complete_task_in_transaction(
                session,
                task_id,
                int(user_id),
                int(session_id),
                result_payload,
            )
            return result_payload

        created = _insert_guide_if_absent(
            session,
            {
                "user_id": int(user_id),
                "source_session_id": int(session_id),
                "anime_name": reference["name"],
                "anime_key": reference["key"],
                "guide_content": content,
            },
        )
        guide = session.scalar(
            select(WatchGuide).where(
                WatchGuide.user_id == int(user_id),
                WatchGuide.anime_key == reference["key"],
            )
        )
        if guide is None:  # pragma: no cover - defensive database invariant
            raise RuntimeError("watch guide insert did not produce a readable row")

        summary = _guide_summary(guide)
        event = build_watch_guide_event(
            "created",
            reference,
            offer_id=offer_id or None,
            guide_id=guide.id,
            inserted=created,
        )
        existing_events = stored_payload.get(WATCH_GUIDE_EVENT_FIELD)
        if not isinstance(existing_events, list):
            existing_events = []
        stored_payload[WATCH_GUIDE_EVENT_FIELD] = [*existing_events, event]
        stored_payload["watch_guide"] = summary
        if created is not None:
            stored_payload["watch_guide_created"] = created
        stored_payload.setdefault("prompt_trace", guide_result.get("prompt_trace"))
        stored_payload.setdefault("fallback", bool(guide_result.get("fallback")))

        message = AgentMessage(
            session_id=int(session_id),
            role="agent",
            content=str(stored_payload["answer"]),
            message_metadata=stored_payload,
            source_task_id=task_id,
        )
        session.add(message)
        session.execute(
            update(AgentSession)
            .where(AgentSession.id == int(session_id))
            .values(updated_at=datetime.now())
        )
        session.flush()
        stored_payload["message_id"] = message.id
        _complete_task_in_transaction(
            session,
            task_id,
            int(user_id),
            int(session_id),
            stored_payload,
        )
        return stored_payload
