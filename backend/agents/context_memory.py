# -*- coding: utf-8 -*-
"""Recommendation context memory backed by the business database.

Raw messages remain the audit source of truth.  This module only creates a
session-scoped compressed view and a small set of typed, user-scoped facts.
LangGraph checkpoints and process globals are deliberately not used as memory.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy import case, or_, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.agents.model_factory import get_chat_model
from backend.agents.prompt_security import inspect_untrusted_text
from backend.agents.schemas import LongTermMemoryExtraction, SessionMemorySummary
from backend.config import (
    RECOMMEND_LLM_TIMEOUT,
    RECOMMEND_MEMORY_CONTEXT_MAX_CHARS,
    RECOMMEND_MEMORY_SUMMARY_MIN_CHARS,
    RECOMMEND_MEMORY_SUMMARY_MIN_MESSAGES,
)
from backend.database import orm_session
from backend.db.models import (
    AgentMessage,
    AgentSession,
    AgentSessionMemory,
    UserMemoryFact,
    UserPreference,
)
from backend.prompts.registry import get_prompt

logger = logging.getLogger(__name__)

_MEMORY_CUES = (
    "喜欢", "偏好", "更爱", "不喜欢", "讨厌", "不能接受", "不想看", "以后",
    "一直", "平时", "通常", "只看", "不看", "记住", "忘记", "忘掉", "不要记",
    "题材", "氛围", "节奏", "制作公司", "工作室", "画风", "看重",
)
_TEMPORARY_MARKERS = ("这次", "今天", "最近", "现在", "本轮", "暂时", "今晚")
_STABLE_MARKERS = ("记住", "一直", "平时", "通常", "以后", "总是", "长期")
_MULTI_VALUE_TYPES = {
    "genre_preference",
    "mood_preference",
    "content_dislike",
    "studio_preference",
    "recommendation_feedback",
}
_SCALAR_KEYS = {
    "pacing_preference": "pacing",
    "viewing_habit": "viewing_habit",
}
_PREFERENCE_FIELD = {
    "genre_preference": "preferred_genres",
    "mood_preference": "preferred_moods",
    "content_dislike": "dislikes",
    "studio_preference": "likes",
}
_PREFERENCE_LIMITS = {
    "likes": 20,
    "dislikes": 20,
    "preferred_moods": 20,
    "preferred_genres": 20,
    "feedback": 30,
}


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default
    return value


def _model_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return dict(value)
    return dict(getattr(value, "__dict__", {}))


def _normalize(value: Any, limit: int = 191) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n，。；;、")
    return text.casefold()[:limit]


def _memory_hash(memory_type: str, memory_key: str, normalized: str) -> str:
    raw = f"{memory_type}|{memory_key}|{normalized}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _ensure_session_memory(session, session_id: int) -> AgentSessionMemory:
    values = {
        "session_id": int(session_id),
        "summary": "",
        "working_state": {},
        "version": 0,
    }
    if session.bind.dialect.name == "mysql":
        statement = mysql_insert(AgentSessionMemory).values(**values)
        session.execute(
            statement.on_duplicate_key_update(session_id=statement.inserted.session_id)
        )
    else:
        statement = sqlite_insert(AgentSessionMemory).values(**values)
        session.execute(
            statement.on_conflict_do_nothing(
                index_elements=[AgentSessionMemory.session_id]
            )
        )
    return session.scalar(
        select(AgentSessionMemory)
        .where(AgentSessionMemory.session_id == int(session_id))
        .with_for_update()
    )


def _sanitize_summary(payload: dict[str, Any]) -> dict[str, Any] | None:
    summary_check = inspect_untrusted_text(
        payload.get("summary", ""),
        source="model_session_memory_summary",
        max_chars=6000,
    )
    if summary_check["risk"] == "high" or not summary_check["sanitized_text"].strip():
        return None

    def clean_list(key: str, limit: int = 12) -> list[str]:
        result = []
        for item in (payload.get(key) or [])[:limit]:
            check = inspect_untrusted_text(
                item,
                source=f"model_session_memory_{key}",
                max_chars=160,
            )
            text = check["sanitized_text"].strip()
            if check["risk"] != "high" and text and text not in result:
                result.append(text)
        return result

    temporary = {}
    for key, values in (payload.get("temporary_preferences") or {}).items():
        cleaned = []
        for value in (values if isinstance(values, list) else [values])[:10]:
            check = inspect_untrusted_text(
                value,
                source="model_session_memory_temporary_preference",
                max_chars=80,
            )
            text = check["sanitized_text"].strip()
            if check["risk"] != "high" and text and text not in cleaned:
                cleaned.append(text)
        if cleaned:
            temporary[str(key)[:40]] = cleaned

    goal_check = inspect_untrusted_text(
        payload.get("current_goal", ""),
        source="model_session_memory_goal",
        max_chars=400,
    )
    return {
        "summary": summary_check["sanitized_text"].strip(),
        "working_state": {
            "current_goal": goal_check["sanitized_text"].strip(),
            "temporary_preferences": temporary,
            "current_constraints": clean_list("current_constraints"),
            "referenced_anime": clean_list("referenced_anime"),
            "unresolved_questions": clean_list("unresolved_questions"),
        },
    }


def _generate_summary(
    previous_summary: str,
    messages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    model = get_chat_model(0, timeout=RECOMMEND_LLM_TIMEOUT, max_tokens=1600)
    if model is None:
        return None
    prompt_template = get_prompt("recommendation_memory_summary")
    history_check = inspect_untrusted_text(
        json.dumps(messages, ensure_ascii=False),
        source="conversation_history_for_summary",
        max_chars=60000,
    )
    previous_check = inspect_untrusted_text(
        previous_summary,
        source="previous_session_summary",
        max_chars=8000,
    )
    prompt = prompt_template.render(
        previous_summary=previous_check["sanitized_text"],
        messages=history_check["sanitized_text"],
    )
    try:
        response = model.with_structured_output(SessionMemorySummary).invoke([
            ("system", prompt_template.render_system()),
            ("human", prompt),
        ])
        return _sanitize_summary(_model_dict(response))
    except Exception:
        logger.exception("Failed to summarize recommendation session memory")
        return None


def _preference_proposals(
    result: dict[str, Any] | None,
    source_message_id: int,
) -> list[dict[str, Any]]:
    response = (result or {}).get("result") or {}
    updates = response.get("preference_updates") or {}
    applied = updates.get("applied") if isinstance(updates, dict) else {}
    if not isinstance(applied, dict):
        return []
    mapping = {
        "preferred_genres": ("genre_preference", "positive"),
        "preferred_moods": ("mood_preference", "positive"),
        "dislikes": ("content_dislike", "negative"),
        "likes": ("recommendation_feedback", "positive"),
    }
    proposals = []
    for field, (memory_type, polarity) in mapping.items():
        values = applied.get(field) or []
        for value in (values if isinstance(values, list) else [values])[:10]:
            proposals.append({
                "action": "remember",
                "source_message_id": int(source_message_id),
                "memory_type": memory_type,
                "memory_key": "",
                "value": str(value),
                "polarity": polarity,
                "strength": "soft",
                "explicit": True,
                "confidence": 0.95,
            })
    return proposals


def _extract_long_term_memories(
    messages: list[dict[str, Any]],
    deterministic: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool, str | None]:
    eligible = [
        message
        for message in messages
        if any(cue in str(message.get("content") or "") for cue in _MEMORY_CUES)
    ]
    if not eligible:
        return deterministic, True, None
    model = get_chat_model(0, timeout=RECOMMEND_LLM_TIMEOUT, max_tokens=1000)
    if model is None:
        logger.warning("Recommendation long-term memory model is unavailable")
        return deterministic, False, "model_unavailable"
    prompt_template = get_prompt("recommendation_memory_extract")
    safe_messages = []
    for message in eligible:
        check = inspect_untrusted_text(
            message.get("content", ""),
            source="user_message_for_memory",
            max_chars=1600,
        )
        if check["risk"] != "high":
            safe_messages.append({
                "source_message_id": int(message["id"]),
                "content": check["sanitized_text"],
            })
    if not safe_messages:
        return deterministic, True, None
    try:
        prompt = prompt_template.render(
            messages=json.dumps(safe_messages, ensure_ascii=False),
        )
        response = model.with_structured_output(LongTermMemoryExtraction).invoke([
            ("system", prompt_template.render_system()),
            ("human", prompt),
        ])
        payload = _model_dict(response)
        if response is None or "memories" not in payload or not isinstance(payload["memories"], list):
            raise ValueError("invalid long-term memory extraction payload")
        allowed_ids = {item["source_message_id"] for item in safe_messages}
        extracted = [
            item.model_dump() if hasattr(item, "model_dump") else dict(item)
            for item in payload["memories"][:20]
            if int(
                getattr(item, "source_message_id", 0)
                if not isinstance(item, dict)
                else item.get("source_message_id", 0)
            ) in allowed_ids
        ]
        return [*deterministic, *extracted], True, None
    except Exception as exc:
        logger.exception("Failed to extract recommendation long-term memory")
        return deterministic, False, type(exc).__name__


def _validated_proposal(
    proposal: dict[str, Any],
    source_messages: dict[int, str],
) -> dict[str, Any] | None:
    try:
        source_message_id = int(proposal.get("source_message_id") or 0)
    except (TypeError, ValueError):
        return None
    if source_message_id not in source_messages:
        return None
    source_text = source_messages[source_message_id]
    if (
        proposal.get("action") != "forget"
        and any(marker in source_text for marker in _TEMPORARY_MARKERS)
        and not any(marker in source_text for marker in _STABLE_MARKERS)
    ):
        return None
    memory_type = str(proposal.get("memory_type") or "")
    if memory_type not in _MULTI_VALUE_TYPES | set(_SCALAR_KEYS):
        return None
    inspection = inspect_untrusted_text(
        proposal.get("value", ""),
        source="model_long_term_memory",
        max_chars=120,
    )
    value = inspection["sanitized_text"].strip()
    normalized = _normalize(value)
    if inspection["risk"] == "high" or not normalized:
        return None
    memory_key = (
        normalized[:64]
        if memory_type in _MULTI_VALUE_TYPES
        else _SCALAR_KEYS[memory_type]
    )
    explicit = bool(proposal.get("explicit"))
    try:
        confidence = float(proposal.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    confidence = max(0.0, min(confidence, 1.0 if explicit else 0.5))
    if confidence < 0.35:
        return None
    strength = "hard" if explicit and proposal.get("strength") == "hard" else "soft"
    polarity = str(proposal.get("polarity") or "neutral")
    if polarity not in {"positive", "negative", "neutral"}:
        polarity = "neutral"
    action = "forget" if proposal.get("action") == "forget" else "remember"
    return {
        "action": action,
        "source_message_id": source_message_id,
        "memory_type": memory_type,
        "memory_key": memory_key,
        "memory_value": {"text": value},
        "normalized_value": normalized,
        "memory_hash": _memory_hash(memory_type, memory_key, normalized),
        "polarity": polarity,
        "strength": strength,
        "confidence": confidence,
        "source_type": "explicit" if explicit else "inferred",
    }


def _preference_values(record: UserPreference, field: str) -> list[Any]:
    value = _json_value(getattr(record, field), [])
    return list(value) if isinstance(value, list) else []


def _sync_preference_projection(
    session,
    user_id: int,
    fact: dict[str, Any],
) -> None:
    polarity = str(fact.get("polarity") or "neutral")
    field = _PREFERENCE_FIELD.get(fact["memory_type"])
    if fact["memory_type"] in _MULTI_VALUE_TYPES:
        if polarity == "negative":
            field = "dislikes"
        elif fact["memory_type"] == "content_dislike":
            field = None
    opposite_fields: tuple[str, ...] = ()
    if fact.get("action") == "remember" and fact["memory_type"] in _MULTI_VALUE_TYPES:
        if polarity == "negative":
            opposite_fields = ("likes", "preferred_moods", "preferred_genres")
        elif polarity == "positive":
            opposite_fields = ("dislikes",)
    if field is None and not opposite_fields:
        return
    record = session.scalar(
        select(UserPreference)
        .where(UserPreference.user_id == int(user_id))
        .with_for_update()
    )
    if record is None:
        record = UserPreference(
            user_id=int(user_id),
            likes=[],
            dislikes=[],
            preferred_moods=[],
            preferred_genres=[],
            feedback=[],
        )
        session.add(record)
        session.flush()
    normalized = fact["normalized_value"]
    for opposite_field in opposite_fields:
        values = [
            value
            for value in _preference_values(record, opposite_field)
            if _normalize(value) != normalized
        ]
        setattr(record, opposite_field, values[-_PREFERENCE_LIMITS[opposite_field]:])
    if field is not None:
        values = _preference_values(record, field)
        if fact["action"] == "forget":
            values = [value for value in values if _normalize(value) != normalized]
        elif all(_normalize(value) != normalized for value in values):
            values.append(fact["memory_value"]["text"])
        setattr(record, field, values[-_PREFERENCE_LIMITS[field]:])
    record.updated_at = datetime.now()


def _record_as_fact(record: UserMemoryFact, *, action: str) -> dict[str, Any]:
    return {
        "action": action,
        "memory_type": record.memory_type,
        "normalized_value": record.normalized_value,
        "memory_value": _json_value(record.memory_value, {}),
        "polarity": record.polarity,
    }


def _incoming_conflict_wins(fact: dict[str, Any], record: UserMemoryFact) -> bool:
    incoming_id = int(fact.get("source_message_id") or 0)
    existing_id = int(record.source_message_id or 0)
    if incoming_id != existing_id:
        return incoming_id > existing_id
    return fact.get("polarity") == "negative" and record.polarity != "negative"


def _resolve_opposing_facts(
    session,
    user_id: int,
    fact: dict[str, Any],
    *,
    force: bool = False,
) -> bool:
    polarity = str(fact.get("polarity") or "neutral")
    if fact["memory_type"] not in _MULTI_VALUE_TYPES or polarity not in {"positive", "negative"}:
        return True
    opposite = "negative" if polarity == "positive" else "positive"
    conflicts = session.scalars(
        select(UserMemoryFact)
        .where(
            UserMemoryFact.user_id == int(user_id),
            UserMemoryFact.normalized_value == fact["normalized_value"],
            UserMemoryFact.polarity == opposite,
            UserMemoryFact.status == "active",
        )
        .with_for_update()
    ).all()
    blockers = [
        record
        for record in conflicts
        if not force and not _incoming_conflict_wins(fact, record)
    ]
    if blockers:
        session.execute(
            update(UserMemoryFact)
            .where(
                UserMemoryFact.user_id == int(user_id),
                UserMemoryFact.memory_hash == fact.get("memory_hash"),
                UserMemoryFact.status == "active",
            )
            .values(status="superseded", last_seen_at=datetime.now())
        )
        _sync_preference_projection(
            session,
            user_id,
            {**fact, "action": "forget"},
        )
        for blocker in blockers:
            _sync_preference_projection(
                session,
                user_id,
                _record_as_fact(blocker, action="remember"),
            )
        return False

    now = datetime.now()
    for conflict in conflicts:
        conflict.status = "superseded"
        conflict.last_seen_at = now
        conflict.version = int(conflict.version or 0) + 1
    return True


def _upsert_fact(session, user_id: int, session_id: int, fact: dict[str, Any]) -> bool:
    if fact["action"] == "forget":
        session.execute(
            update(UserMemoryFact)
            .where(
                UserMemoryFact.user_id == int(user_id),
                UserMemoryFact.memory_hash == fact["memory_hash"],
                UserMemoryFact.status == "active",
            )
            .values(
                status="deleted",
                source_session_id=int(session_id),
                source_message_id=fact["source_message_id"],
                last_seen_at=datetime.now(),
                version=UserMemoryFact.version + 1,
            )
        )
        _sync_preference_projection(session, user_id, fact)
        return True

    existing = session.scalar(
        select(UserMemoryFact)
        .where(
            UserMemoryFact.user_id == int(user_id),
            UserMemoryFact.memory_hash == fact["memory_hash"],
        )
        .with_for_update()
    )
    if (
        existing is not None
        and int(existing.source_message_id or 0) >= int(fact.get("source_message_id") or 0)
    ):
        if existing.status == "active":
            _sync_preference_projection(
                session,
                user_id,
                _record_as_fact(existing, action="remember"),
            )
        return False
    if not _resolve_opposing_facts(session, user_id, fact):
        return False

    if fact["memory_type"] in _SCALAR_KEYS:
        session.execute(
            update(UserMemoryFact)
            .where(
                UserMemoryFact.user_id == int(user_id),
                UserMemoryFact.memory_type == fact["memory_type"],
                UserMemoryFact.memory_key == fact["memory_key"],
                UserMemoryFact.memory_hash != fact["memory_hash"],
                UserMemoryFact.status == "active",
            )
            .values(status="superseded", last_seen_at=datetime.now())
        )

    now = datetime.now()
    values = {
        key: fact[key]
        for key in (
            "memory_type",
            "memory_key",
            "memory_value",
            "normalized_value",
            "memory_hash",
            "polarity",
            "strength",
            "confidence",
            "source_type",
            "source_message_id",
        )
    } | {
        "user_id": int(user_id),
        "source_session_id": int(session_id),
        "occurrence_count": 1,
        "status": "active",
        "first_seen_at": now,
        "last_seen_at": now,
        "version": 0,
    }
    if session.bind.dialect.name == "mysql":
        statement = mysql_insert(UserMemoryFact).values(**values)
        inserted = statement.inserted
        statement = statement.on_duplicate_key_update(
            memory_value=inserted.memory_value,
            polarity=inserted.polarity,
            strength=case(
                (UserMemoryFact.strength == "hard", "hard"),
                else_=inserted.strength,
            ),
            confidence=case(
                (
                    UserMemoryFact.confidence >= inserted.confidence,
                    UserMemoryFact.confidence,
                ),
                else_=inserted.confidence,
            ),
            source_type=case(
                (UserMemoryFact.source_type == "explicit", "explicit"),
                else_=inserted.source_type,
            ),
            source_session_id=inserted.source_session_id,
            source_message_id=inserted.source_message_id,
            occurrence_count=UserMemoryFact.occurrence_count + 1,
            status="active",
            last_seen_at=now,
            version=UserMemoryFact.version + 1,
        )
    else:
        statement = sqlite_insert(UserMemoryFact).values(**values)
        excluded = statement.excluded
        statement = statement.on_conflict_do_update(
            index_elements=[UserMemoryFact.user_id, UserMemoryFact.memory_hash],
            set_={
                "memory_value": excluded.memory_value,
                "polarity": excluded.polarity,
                "strength": case(
                    (UserMemoryFact.strength == "hard", "hard"),
                    else_=excluded.strength,
                ),
                "confidence": case(
                    (
                        UserMemoryFact.confidence >= excluded.confidence,
                        UserMemoryFact.confidence,
                    ),
                    else_=excluded.confidence,
                ),
                "source_type": case(
                    (UserMemoryFact.source_type == "explicit", "explicit"),
                    else_=excluded.source_type,
                ),
                "source_session_id": excluded.source_session_id,
                "source_message_id": excluded.source_message_id,
                "occurrence_count": UserMemoryFact.occurrence_count + 1,
                "status": "active",
                "last_seen_at": now,
                "version": UserMemoryFact.version + 1,
            },
        )
    session.execute(statement)
    record = session.scalar(
        select(UserMemoryFact)
        .where(
            UserMemoryFact.user_id == int(user_id),
            UserMemoryFact.memory_hash == fact["memory_hash"],
        )
        .with_for_update()
    )
    if (
        record is not None
        and record.source_type == "inferred"
        and int(record.occurrence_count or 0) >= 2
        and float(record.confidence or 0) < 0.75
    ):
        record.confidence = 0.75
    _sync_preference_projection(session, user_id, fact)
    return True


def _load_maintenance_snapshot(
    user_id: int,
    session_id: int,
    current_user_message_id: int,
) -> dict[str, Any] | None:
    with orm_session() as session:
        owner = session.scalar(
            select(AgentSession).where(
                AgentSession.id == int(session_id),
                AgentSession.user_id == int(user_id),
                AgentSession.agent_type == "recommendation",
            )
        )
        if owner is None:
            return None
        memory = session.get(AgentSessionMemory, int(session_id))
        last_summary_id = int(memory.last_processed_message_id or 0) if memory else 0
        last_memory_id = int(memory.last_memory_message_id or 0) if memory else 0
        pending_summary = session.scalars(
            select(AgentMessage)
            .where(
                AgentMessage.session_id == int(session_id),
                AgentMessage.id > last_summary_id,
            )
            .order_by(AgentMessage.id)
        ).all()
        pending_user = session.scalars(
            select(AgentMessage)
            .where(
                AgentMessage.session_id == int(session_id),
                AgentMessage.role == "user",
                AgentMessage.id > last_memory_id,
                AgentMessage.id <= int(current_user_message_id),
            )
            .order_by(AgentMessage.id)
        ).all()
        return {
            "summary": str(memory.summary or "") if memory else "",
            "last_memory_id": last_memory_id,
            "pending_summary": [
                {"id": row.id, "role": row.role, "content": row.content}
                for row in pending_summary
            ],
            "pending_user": [
                {"id": row.id, "content": row.content}
                for row in pending_user
            ],
        }


def maintain_context_memory(
    user_id: int,
    session_id: int,
    current_user_message_id: int | None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Best-effort post-answer maintenance; failures never change task outcome."""
    if not current_user_message_id:
        return {"status": "skipped", "reason": "missing user message id"}
    snapshot = _load_maintenance_snapshot(
        user_id,
        session_id,
        int(current_user_message_id),
    )
    if snapshot is None:
        return {"status": "skipped", "reason": "session not found"}
    if int(current_user_message_id) <= snapshot["last_memory_id"]:
        return {"status": "skipped", "reason": "already processed"}

    pending_summary = snapshot["pending_summary"]
    summary_chars = sum(len(str(item.get("content") or "")) for item in pending_summary)
    should_summarize = (
        len(pending_summary) >= RECOMMEND_MEMORY_SUMMARY_MIN_MESSAGES
        and summary_chars >= RECOMMEND_MEMORY_SUMMARY_MIN_CHARS
    )
    summary_update = (
        _generate_summary(snapshot["summary"], pending_summary)
        if should_summarize
        else None
    )
    deterministic = _preference_proposals(result, int(current_user_message_id))
    proposals, extraction_completed, extraction_error = _extract_long_term_memories(
        snapshot["pending_user"],
        deterministic,
    )
    source_messages = {
        int(item["id"]): str(item.get("content") or "")
        for item in snapshot["pending_user"]
    }
    validated = []
    seen = set()
    if extraction_completed:
        for proposal in proposals:
            fact = _validated_proposal(proposal, source_messages)
            if fact is None:
                continue
            dedupe = (fact["action"], fact["memory_hash"], fact["source_message_id"])
            if dedupe not in seen:
                seen.add(dedupe)
                validated.append(fact)

    applied_count = 0
    with orm_session() as session:
        memory = _ensure_session_memory(session, session_id)
        if int(current_user_message_id) <= int(memory.last_memory_message_id or 0):
            return {"status": "skipped", "reason": "already processed"}
        if extraction_completed:
            for fact in validated:
                if _upsert_fact(session, user_id, session_id, fact):
                    applied_count += 1
            memory.last_memory_message_id = int(current_user_message_id)
        if summary_update is not None and pending_summary:
            memory.summary = summary_update["summary"]
            memory.working_state = summary_update["working_state"]
            memory.last_processed_message_id = int(pending_summary[-1]["id"])
        if extraction_completed or summary_update is not None:
            memory.version = int(memory.version or 0) + 1
            memory.updated_at = datetime.now()
    status = "updated" if extraction_completed else (
        "partial" if summary_update is not None else "retryable"
    )
    return {
        "status": status,
        "summary_triggered": should_summarize,
        "summary_updated": summary_update is not None,
        "memory_fact_count": applied_count,
        "memory_retryable": not extraction_completed,
        "memory_error": extraction_error,
    }


def _fact_dict(record: UserMemoryFact) -> dict[str, Any]:
    return {
        "id": record.id,
        "memory_type": record.memory_type,
        "memory_key": record.memory_key,
        "value": _json_value(record.memory_value, {}),
        "polarity": record.polarity,
        "strength": record.strength,
        "confidence": float(record.confidence or 0),
        "source_type": record.source_type,
        "occurrence_count": int(record.occurrence_count or 0),
        "status": record.status,
        "source_session_id": record.source_session_id,
        "source_message_id": record.source_message_id,
        "last_seen_at": (
            record.last_seen_at.strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(record.last_seen_at, datetime)
            else str(record.last_seen_at or "")
        ),
    }


def list_user_memories(user_id: int, *, include_inactive: bool = False) -> list[dict[str, Any]]:
    with orm_session() as session:
        statement = select(UserMemoryFact).where(UserMemoryFact.user_id == int(user_id))
        if not include_inactive:
            statement = statement.where(UserMemoryFact.status == "active")
        rows = session.scalars(
            statement.order_by(UserMemoryFact.last_seen_at.desc(), UserMemoryFact.id.desc())
        ).all()
        return [_fact_dict(row) for row in rows]


def forget_user_memory(user_id: int, memory_id: int) -> bool:
    with orm_session() as session:
        record = session.scalar(
            select(UserMemoryFact)
            .where(
                UserMemoryFact.id == int(memory_id),
                UserMemoryFact.user_id == int(user_id),
                UserMemoryFact.status == "active",
            )
            .with_for_update()
        )
        if record is None:
            return False
        fact = {
            "action": "forget",
            "memory_type": record.memory_type,
            "normalized_value": record.normalized_value,
            "memory_value": _json_value(record.memory_value, {}),
            "polarity": record.polarity,
        }
        record.status = "deleted"
        record.last_seen_at = datetime.now()
        record.version = int(record.version or 0) + 1
        _sync_preference_projection(session, user_id, fact)
        return True


def update_user_memory(
    user_id: int,
    memory_id: int,
    updates: dict[str, Any],
) -> dict[str, Any] | None:
    """Edit one owned fact and keep the compatibility preference projection aligned."""
    allowed = {key: updates[key] for key in ("value", "polarity", "strength") if key in updates}
    if not allowed:
        raise ValueError("至少提供 value、polarity 或 strength 之一")
    polarity = allowed.get("polarity")
    if polarity is not None and polarity not in {"positive", "negative", "neutral"}:
        raise ValueError("polarity 必须是 positive、negative 或 neutral")
    strength = allowed.get("strength")
    if strength is not None and strength not in {"soft", "hard"}:
        raise ValueError("strength 必须是 soft 或 hard")

    with orm_session() as session:
        record = session.scalar(
            select(UserMemoryFact)
            .where(
                UserMemoryFact.id == int(memory_id),
                UserMemoryFact.user_id == int(user_id),
                UserMemoryFact.status == "active",
            )
            .with_for_update()
        )
        if record is None:
            return None
        old_fact = {
            "action": "forget",
            "memory_type": record.memory_type,
            "normalized_value": record.normalized_value,
            "memory_value": _json_value(record.memory_value, {}),
            "polarity": record.polarity,
        }
        new_value = str(
            allowed.get(
                "value",
                (_json_value(record.memory_value, {}) or {}).get("text", ""),
            )
        )
        inspection = inspect_untrusted_text(
            new_value,
            source="user_edited_long_term_memory",
            max_chars=120,
        )
        normalized = _normalize(inspection["sanitized_text"])
        if inspection["risk"] == "high" or not normalized:
            raise ValueError("记忆内容为空或包含不能持久化的指令")
        memory_key = (
            normalized[:64]
            if record.memory_type in _MULTI_VALUE_TYPES
            else _SCALAR_KEYS[record.memory_type]
        )
        new_hash = _memory_hash(record.memory_type, memory_key, normalized)
        target = None
        if new_hash != record.memory_hash:
            target = session.scalar(
                select(UserMemoryFact)
                .where(
                    UserMemoryFact.user_id == int(user_id),
                    UserMemoryFact.memory_hash == new_hash,
                    UserMemoryFact.id != record.id,
                )
                .with_for_update()
            )
            _sync_preference_projection(session, user_id, old_fact)
        if target is not None:
            record.status = "superseded"
            record.version = int(record.version or 0) + 1
            target.status = "active"
            target.memory_value = {"text": inspection["sanitized_text"].strip()}
            target.polarity = polarity or record.polarity
            target.strength = strength or record.strength
            target.confidence = 1.0
            target.source_type = "explicit"
            target.last_seen_at = datetime.now()
            target.version = int(target.version or 0) + 1
            record = target
        else:
            record.memory_key = memory_key
            record.memory_value = {"text": inspection["sanitized_text"].strip()}
            record.normalized_value = normalized
            record.memory_hash = new_hash
            record.polarity = polarity or record.polarity
            record.strength = strength or record.strength
            record.confidence = 1.0
            record.source_type = "explicit"
            record.last_seen_at = datetime.now()
            record.version = int(record.version or 0) + 1
        new_fact = {
            "action": "remember",
            "memory_type": record.memory_type,
            "normalized_value": record.normalized_value,
            "memory_value": _json_value(record.memory_value, {}),
            "memory_hash": record.memory_hash,
            "source_message_id": record.source_message_id,
            "polarity": record.polarity,
        }
        _resolve_opposing_facts(session, user_id, new_fact, force=True)
        _sync_preference_projection(session, user_id, new_fact)
        session.flush()
        return _fact_dict(record)


def _memory_relevance(record: UserMemoryFact, query: str) -> tuple[float, int]:
    value = str((_json_value(record.memory_value, {}) or {}).get("text") or "")
    normalized_query = _normalize(query, 1000)
    overlap = 1.5 if value and _normalize(value) in normalized_query else 0.0
    score = (
        overlap
        + float(record.confidence or 0)
        + (0.8 if record.strength == "hard" else 0.0)
        + (0.3 if record.polarity == "negative" else 0.0)
        + min(0.5, max(0, int(record.occurrence_count or 0) - 1) * 0.15)
    )
    return score, int(record.id)


def _pack_complete_messages(messages: list[AgentMessage], budget: int) -> list[dict[str, Any]]:
    selected = []
    remaining = max(0, budget)
    for message in reversed(messages):
        inspection = inspect_untrusted_text(
            message.content,
            source="older_uncompressed_context_memory",
            max_chars=4000,
        )
        if inspection["risk"] == "high":
            continue
        entry = {
            "role": "assistant" if message.role == "agent" else message.role,
            "content": inspection["sanitized_text"],
        }
        size = len(entry["content"]) + 32
        if size <= remaining:
            selected.append(entry)
            remaining -= size
    return list(reversed(selected))


def _complete_sentence_excerpt(text: str, budget: int) -> str:
    """Fit whole summary sentences into the context without mid-sentence cuts."""
    selected = []
    remaining = max(0, budget)
    for sentence in re.findall(r"[^。！？!?\n]+[。！？!?]?|\n", str(text or "")):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) <= remaining:
            selected.append(sentence)
            remaining -= len(sentence)
    return "".join(selected)


def load_context_memory(
    user_id: int,
    session_id: int,
    query: str,
    *,
    max_chars: int = RECOMMEND_MEMORY_CONTEXT_MAX_CHARS,
) -> dict[str, Any]:
    """Load bounded session memory and the most relevant durable user facts."""
    with orm_session() as session:
        owner = session.scalar(
            select(AgentSession).where(
                AgentSession.id == int(session_id),
                AgentSession.user_id == int(user_id),
                AgentSession.agent_type == "recommendation",
            )
        )
        if owner is None:
            return {}
        memory = session.get(AgentSessionMemory, int(session_id))
        last_processed = int(memory.last_processed_message_id or 0) if memory else 0
        pending = session.scalars(
            select(AgentMessage)
            .where(
                AgentMessage.session_id == int(session_id),
                AgentMessage.id > last_processed,
            )
            .order_by(AgentMessage.id)
        ).all()
        older_uncompressed = pending[:-8] if len(pending) > 8 else []
        facts = session.scalars(
            select(UserMemoryFact)
            .where(
                UserMemoryFact.user_id == int(user_id),
                UserMemoryFact.status == "active",
                or_(
                    UserMemoryFact.expires_at.is_(None),
                    UserMemoryFact.expires_at > datetime.now(),
                ),
            )
        ).all()

    ranked = sorted(facts, key=lambda row: _memory_relevance(row, query), reverse=True)[:8]
    summary = str(memory.summary or "") if memory else ""
    working_state = _json_value(memory.working_state, {}) if memory else {}
    context = {
        "session_summary": _complete_sentence_excerpt(
            summary,
            min(3000, max_chars // 2),
        ),
        "working_state": working_state,
        "older_uncompressed_messages": _pack_complete_messages(
            older_uncompressed,
            min(2000, max_chars // 3),
        ),
        "long_term_memories": [
            {
                "id": row.id,
                "type": row.memory_type,
                "value": (_json_value(row.memory_value, {}) or {}).get("text", ""),
                "polarity": row.polarity,
                "strength": row.strength,
                "confidence": round(float(row.confidence or 0), 3),
                "occurrences": int(row.occurrence_count or 0),
            }
            for row in ranked
        ],
    }
    while len(json.dumps(context, ensure_ascii=False)) > max_chars and context["long_term_memories"]:
        context["long_term_memories"].pop()
    while len(json.dumps(context, ensure_ascii=False)) > max_chars and context["older_uncompressed_messages"]:
        context["older_uncompressed_messages"].pop(0)
    return context
