# -*- coding: utf-8 -*-
"""Lightweight in-process background queue for Agent Center tasks."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Any

from backend.agents.memory import get_agent_task_by_id, mark_task_failed, mark_task_running, mark_task_succeeded, save_agent_message

logger = logging.getLogger(__name__)
_MAX_WORKERS = int(os.environ.get("AGENT_TASK_WORKERS", "2"))
_executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="agent-task")


def submit_agent_task(task_id: int, fn: Callable[..., dict], *args: Any, **kwargs: Any) -> int:
    """Submit one Agent task and return the task id immediately."""
    _executor.submit(_run_task, task_id, fn, args, kwargs)
    return task_id


def _run_task(task_id: int, fn: Callable[..., dict], args: tuple, kwargs: dict) -> None:
    mark_task_running(task_id, "running_agent")
    try:
        result = fn(*args, **kwargs)
        if result.get("error"):
            mark_task_failed(task_id, str(result.get("error")), result)
            return
        mark_task_succeeded(task_id, result)
    except Exception as exc:  # pragma: no cover - defensive guard for worker threads
        logger.exception("Agent task %s failed", task_id)
        message = str(exc)
        mark_task_failed(task_id, message)
        task = get_agent_task_by_id(task_id)
        if task:
            save_agent_message(task["session_id"], "agent", message, {"task_id": task_id, "error": message})