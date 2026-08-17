# -*- coding: utf-8 -*-
"""Redis + Celery Agent 任务队列，并以 SQL 租约和消息幂等承接重复投递。"""

from __future__ import annotations

import logging
from threading import Event, Thread
from uuid import uuid4

from celery.signals import worker_ready

from backend.agents.memory import (
    claim_agent_task,
    get_agent_message_by_task_id,
    get_agent_task_by_id,
    heartbeat_agent_task,
    mark_task_failed,
    mark_task_succeeded,
    register_agent_task_delivery,
    save_agent_message,
    take_recoverable_agent_tasks,
)
from backend.celery_app import celery_app
from backend.config import (
    AGENT_TASK_HEARTBEAT_SECONDS,
    AGENT_TASK_LEASE_SECONDS,
    AGENT_TASK_QUEUE_STALE_SECONDS,
)

logger = logging.getLogger(__name__)

_AGENT_QUEUES = {
    "recommendation": "agent.recommendation",
    "opinion": "agent.opinion",
}


def submit_agent_task(task_id: int, agent_type: str) -> int:
    """将已提交到 SQL 的任务投递给对应 Celery 队列，并立即返回任务 ID。"""
    if agent_type not in _AGENT_QUEUES:
        error = ValueError(f"unsupported agent type: {agent_type}")
        mark_task_failed(task_id, str(error))
        raise error
    _enqueue_agent_task(task_id, agent_type, fail_task_on_error=True)
    return task_id


def _enqueue_agent_task(
    task_id: int,
    agent_type: str,
    *,
    fail_task_on_error: bool,
) -> bool:
    delivery_id = uuid4().hex
    if not register_agent_task_delivery(task_id, delivery_id):
        return False
    try:
        execute_agent_task.apply_async(
            args=(int(task_id),),
            task_id=delivery_id,
            queue=_AGENT_QUEUES[agent_type],
        )
    except Exception as exc:
        message = f"task submission failed: {type(exc).__name__}: {exc}"
        if fail_task_on_error:
            mark_task_failed(task_id, message)
        logger.exception("Failed to submit Agent task %s", task_id)
        raise
    return True


def _dispatch_agent_task(task: dict) -> dict:
    """只按持久化 agent_type/input 白名单分发，不反序列化任意可调用对象。"""
    task_id = int(task["id"])
    session_id = int(task["session_id"])
    input_data = task.get("input") or {}
    agent_type = task.get("agent_type")

    # 延迟导入避免 API router 导入 task_queue 时形成模块循环；Worker 只调用白名单函数。
    from backend.api import agent as agent_api

    if agent_type == "opinion":
        return agent_api._run_opinion_task(
            session_id,
            input_data.get("anime_id"),
            input_data.get("name"),
            str(input_data.get("query") or ""),
            task_id=task_id,
        )
    if agent_type == "recommendation":
        history = input_data.get("history") or []
        watch_turn = input_data.get("watch_guide_turn") or {}
        return agent_api._run_recommendation_task(
            task_id,
            int(task["user_id"]),
            session_id,
            str(input_data.get("message") or input_data.get("query") or ""),
            history,
            input_data.get("recommendation_context"),
            watch_turn.get("state"),
            str(watch_turn.get("action") or "normal"),
        )
    raise ValueError(f"unsupported agent type: {agent_type}")


def _restore_terminal_from_message(task_id: int, message: dict) -> dict:
    """兼容崩溃窗口：若最终消息已存在，直接恢复 SQL 任务终态而不再调用模型。"""
    payload = message.get("metadata") or {}
    if payload.get("error"):
        mark_task_failed(task_id, str(payload["error"]), payload)
    else:
        mark_task_succeeded(task_id, payload)
    return payload


def _start_heartbeat(task_id: int, worker_id: str) -> tuple[Event, Thread]:
    stopped = Event()

    def heartbeat_loop() -> None:
        while not stopped.wait(AGENT_TASK_HEARTBEAT_SECONDS):
            try:
                if not heartbeat_agent_task(
                    task_id,
                    worker_id,
                    AGENT_TASK_LEASE_SECONDS,
                ):
                    return
            except Exception:  # pragma: no cover - defensive worker telemetry guard
                logger.exception("Agent task %s heartbeat failed", task_id)

    thread = Thread(
        target=heartbeat_loop,
        name=f"agent-task-heartbeat-{task_id}",
        daemon=True,
    )
    thread.start()
    return stopped, thread


@celery_app.task(
    bind=True,
    name="backend.agents.execute_agent_task",
    acks_late=True,
    reject_on_worker_lost=True,
    ignore_result=True,
)
def execute_agent_task(self, task_id: int) -> dict:
    """执行一个可重投递任务；SQL 租约阻止并发重复执行，最终消息按 task_id 幂等。"""
    request_id = str(getattr(self.request, "id", "") or uuid4().hex)
    hostname = str(getattr(self.request, "hostname", "") or "celery-worker")
    worker_id = f"{hostname}:{request_id}"[:255]
    claim = claim_agent_task(task_id, worker_id, AGENT_TASK_LEASE_SECONDS)
    claim_state = claim.get("claim_state")
    if claim_state == "missing":
        return {"status": "missing", "task_id": task_id}
    if claim_state == "terminal":
        task = claim.get("task") or {}
        return task.get("result") or {"status": task.get("status"), "task_id": task_id}
    if claim_state == "busy":
        return {"status": "duplicate_ignored", "task_id": task_id}

    existing_message = get_agent_message_by_task_id(task_id)
    if existing_message is not None:
        return _restore_terminal_from_message(task_id, existing_message)

    stopped, heartbeat_thread = _start_heartbeat(task_id, worker_id)
    try:
        task = get_agent_task_by_id(task_id)
        if task is None:
            return {"status": "missing", "task_id": task_id}
        result = _dispatch_agent_task(task)
        terminal = get_agent_task_by_id(task_id) or {}
        if terminal.get("status") not in {"succeeded", "failed"}:
            if result.get("error"):
                mark_task_failed(task_id, str(result.get("error")), result)
            else:
                mark_task_succeeded(task_id, result)
        return result
    except Exception as exc:
        logger.exception("Agent task %s failed", task_id)
        message = str(exc)
        task = get_agent_task_by_id(task_id)
        if task:
            payload = {"task_id": task_id, "error": message}
            try:
                save_agent_message(
                    task["session_id"],
                    "agent",
                    message,
                    payload,
                    source_task_id=task_id,
                    task_outcome="failed",
                )
            except Exception:  # pragma: no cover - preserve the original worker failure
                logger.exception("Failed to persist Agent task %s error message", task_id)
                mark_task_failed(task_id, message)
        raise
    finally:
        stopped.set()
        heartbeat_thread.join(timeout=1)


def recover_stale_agent_tasks() -> int:
    """重投递 API/Worker 异常退出后遗留的 queued 或租约过期任务。"""
    recovered = take_recoverable_agent_tasks(AGENT_TASK_QUEUE_STALE_SECONDS)
    submitted = 0
    for task in recovered:
        try:
            if _enqueue_agent_task(
                task["id"],
                task["agent_type"],
                fail_task_on_error=False,
            ):
                submitted += 1
        except Exception:
            logger.exception("Failed to recover Agent task %s", task["id"])
    return submitted


@celery_app.task(
    name="backend.agents.recover_stale_agent_tasks",
    ignore_result=True,
)
def recover_stale_agent_tasks_periodically() -> int:
    """由 Celery Beat 周期触发，缩短孤儿任务只靠 Worker 重启恢复的窗口。"""
    return recover_stale_agent_tasks()


@worker_ready.connect
def _recover_tasks_when_worker_starts(**_kwargs) -> None:
    """Worker 重启时立即扫描一次；运行中崩溃仍由 Redis visibility timeout 重投递。"""
    try:
        recovered = recover_stale_agent_tasks()
        if recovered:
            logger.warning("Recovered %s stale Agent task(s)", recovered)
    except Exception:  # pragma: no cover - Worker must still be able to start
        logger.exception("Agent stale-task recovery failed during worker startup")
