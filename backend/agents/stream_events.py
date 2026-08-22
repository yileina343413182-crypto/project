# -*- coding: utf-8 -*-
"""Agent 短期流式事件通道；SQL 任务与最终消息仍是持久化真相。"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Any, AsyncIterator

from backend.config import (
    AGENT_REDIS_KEY_PREFIX,
    AGENT_STREAM_BLOCK_MS,
    AGENT_STREAM_ENABLED,
    AGENT_STREAM_MAX_CONNECTIONS,
    AGENT_STREAM_MAX_EVENTS,
    AGENT_STREAM_TTL_SECONDS,
    REDIS_URL,
)

logger = logging.getLogger(__name__)

_sync_client = None
_async_client = None
_publish_backoff_until = 0.0
_publish_backoff_lock = Lock()


def _stream_key(task_id: int) -> str:
    prefix = f"{AGENT_REDIS_KEY_PREFIX}:" if AGENT_REDIS_KEY_PREFIX else ""
    return f"{prefix}stream:task:{int(task_id)}"


def _get_sync_client():
    global _sync_client
    if _sync_client is None:
        import redis

        _sync_client = redis.Redis.from_url(
            REDIS_URL,
            decode_responses=True,
            max_connections=AGENT_STREAM_MAX_CONNECTIONS,
            socket_connect_timeout=3,
            socket_timeout=5,
            health_check_interval=30,
        )
    return _sync_client


def _get_async_client():
    global _async_client
    if _async_client is None:
        import redis.asyncio as redis_async

        _async_client = redis_async.Redis.from_url(
            REDIS_URL,
            decode_responses=True,
            max_connections=AGENT_STREAM_MAX_CONNECTIONS,
            socket_connect_timeout=3,
            socket_timeout=max(5, AGENT_STREAM_BLOCK_MS // 1000 + 2),
            health_check_interval=30,
        )
    return _async_client


def emit_agent_event(
    task_id: int,
    event_type: str,
    *,
    attempt: int = 1,
    **payload: Any,
) -> bool:
    """尽力发布事件；Redis流失败不能改变Agent任务的业务终态。"""
    global _publish_backoff_until
    if not AGENT_STREAM_ENABLED or time.monotonic() < _publish_backoff_until:
        return False
    event = {
        "task_id": int(task_id),
        "attempt": max(1, int(attempt or 1)),
        "type": str(event_type),
        "created_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    try:
        client = _get_sync_client()
        key = _stream_key(task_id)
        client.xadd(
            key,
            {"event": json.dumps(event, ensure_ascii=False, separators=(",", ":"))},
            maxlen=AGENT_STREAM_MAX_EVENTS,
            approximate=True,
        )
        client.expire(key, AGENT_STREAM_TTL_SECONDS)
        return True
    except Exception as exc:  # pragma: no cover - depends on external Redis state
        with _publish_backoff_lock:
            _publish_backoff_until = max(_publish_backoff_until, time.monotonic() + 30)
        logger.warning("Agent stream event publish failed for task %s: %s", task_id, exc)
        return False


class AgentStreamEmitter:
    """为一次Worker执行固定task/attempt，避免事件调用方重复传递标识。"""

    def __init__(self, task_id: int, attempt: int = 1):
        self.task_id = int(task_id)
        self.attempt = max(1, int(attempt or 1))
        self._text_parts: list[str] = []
        self._text_chars = 0
        self._last_text_flush = time.monotonic()

    def emit(self, event_type: str, **payload: Any) -> bool:
        if event_type == "text_delta":
            delta = str(payload.get("delta") or "")
            if not delta:
                return True
            self._text_parts.append(delta)
            self._text_chars += len(delta)
            if self._text_chars < 32 and time.monotonic() - self._last_text_flush < 0.12:
                return True
            return self._flush_text()
        self._flush_text()
        return emit_agent_event(
            self.task_id,
            event_type,
            attempt=self.attempt,
            **payload,
        )

    def _flush_text(self) -> bool:
        if not self._text_parts:
            return True
        delta = "".join(self._text_parts)
        self._text_parts = []
        self._text_chars = 0
        self._last_text_flush = time.monotonic()
        return emit_agent_event(
            self.task_id,
            "text_delta",
            attempt=self.attempt,
            delta=delta,
        )

    def phase(self, message: str, progress: int | None = None) -> bool:
        payload: dict[str, Any] = {"message": str(message)}
        if progress is not None:
            payload["progress"] = max(0, min(100, int(progress)))
        return self.emit("phase", **payload)


async def iter_agent_events(task_id: int, after: str = "0-0") -> AsyncIterator[dict]:
    """按Redis ID读取事件；无消息时返回心跳，调用方可安全保持HTTP连接。"""
    if not AGENT_STREAM_ENABLED:
        yield {"task_id": int(task_id), "type": "stream_unavailable"}
        return
    client = _get_async_client()
    key = _stream_key(task_id)
    last_id = after
    try:
        while True:
            records = await client.xread(
                {key: last_id},
                count=32,
                block=AGENT_STREAM_BLOCK_MS,
            )
            if not records:
                yield {"task_id": int(task_id), "type": "heartbeat"}
                continue
            for _key, entries in records:
                for event_id, fields in entries:
                    last_id = event_id
                    try:
                        event = json.loads(fields.get("event") or "{}")
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    event["event_id"] = event_id
                    yield event
                    if event.get("type") in {"task_completed", "task_failed"}:
                        return
    except Exception as exc:
        logger.warning("Agent stream event read failed for task %s: %s", task_id, exc)
        yield {
            "task_id": int(task_id),
            "type": "stream_unavailable",
            "message": "stream transport unavailable",
        }


async def close_agent_stream_client() -> None:
    """关闭API进程的异步Redis连接池。"""
    global _async_client
    client, _async_client = _async_client, None
    if client is not None:
        await client.aclose()
