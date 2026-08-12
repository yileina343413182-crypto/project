# -*- coding: utf-8 -*-
"""Agent 任务的轻量进程内线程池队列。

提交接口立即返回任务 ID；工作线程负责推进任务状态。进程重启后的恢复依赖
数据库任务记录和 LangGraph Checkpoint，而不是本线程池本身。
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Any

from backend.agents.memory import get_agent_task_by_id, mark_task_failed, mark_task_running, mark_task_succeeded, save_agent_message

logger = logging.getLogger(__name__)
# 限制并发，避免多个 LLM/检索任务同时耗尽数据库连接和外部服务额度。
_MAX_WORKERS = int(os.environ.get("AGENT_TASK_WORKERS", "2"))
_executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="agent-task")


def submit_agent_task(task_id: int, fn: Callable[..., dict], *args: Any, **kwargs: Any) -> int:
    """Submit one Agent task and return the task id immediately."""
    _executor.submit(_run_task, task_id, fn, args, kwargs)
    return task_id


def _run_task(task_id: int, fn: Callable[..., dict], args: tuple, kwargs: dict) -> None:
    """在线程中执行任务，并保证成功或异常都会落库为终态。"""
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
