# -*- coding: utf-8 -*-
"""Celery 应用：Redis 负责投递，SQL 任务表负责用户可见状态与幂等。"""

from celery import Celery
from kombu import Queue

from backend.config import (
    AGENT_REDIS_KEY_PREFIX,
    AGENT_TASK_RECOVERY_INTERVAL_SECONDS,
    CELERY_BROKER_POOL_LIMIT,
    CELERY_BROKER_SOCKET_TIMEOUT,
    CELERY_BROKER_URL,
    CELERY_REDIS_MAX_CONNECTIONS,
    CELERY_RESULT_BACKEND,
    CELERY_RESULT_EXPIRES,
    CELERY_STORE_RESULTS,
    CELERY_VISIBILITY_TIMEOUT,
)


def _redis_key_prefix(component: str) -> str:
    if not AGENT_REDIS_KEY_PREFIX:
        return ""
    return f"{AGENT_REDIS_KEY_PREFIX}:{component}:"


broker_transport_options = {
    "visibility_timeout": CELERY_VISIBILITY_TIMEOUT,
    "socket_connect_timeout": CELERY_BROKER_SOCKET_TIMEOUT,
    "socket_timeout": CELERY_BROKER_SOCKET_TIMEOUT,
    "max_connections": CELERY_BROKER_POOL_LIMIT,
    "health_check_interval": 30,
}
result_backend_transport_options = {
    "visibility_timeout": CELERY_VISIBILITY_TIMEOUT,
    "socket_connect_timeout": CELERY_BROKER_SOCKET_TIMEOUT,
    "socket_timeout": CELERY_BROKER_SOCKET_TIMEOUT,
}
if AGENT_REDIS_KEY_PREFIX:
    broker_transport_options["global_keyprefix"] = _redis_key_prefix("broker")
    result_backend_transport_options["global_keyprefix"] = _redis_key_prefix("result")


celery_app = Celery(
    "anime_agent_tasks",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=("backend.agents.task_queue",),
)
celery_app.conf.update(
    accept_content=("json",),
    task_serializer="json",
    result_serializer="json",
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=CELERY_BROKER_SOCKET_TIMEOUT,
    broker_pool_limit=CELERY_BROKER_POOL_LIMIT,
    broker_transport_options=broker_transport_options,
    result_backend_transport_options=result_backend_transport_options,
    redis_max_connections=CELERY_REDIS_MAX_CONNECTIONS,
    visibility_timeout=CELERY_VISIBILITY_TIMEOUT,
    result_expires=CELERY_RESULT_EXPIRES,
    task_ignore_result=not CELERY_STORE_RESULTS,
    worker_soft_shutdown_timeout=30.0,
    task_default_queue="agent.control",
    task_queues=(
        Queue("agent.recommendation"),
        Queue("agent.opinion"),
        Queue("agent.control"),
    ),
    task_routes={
        "backend.agents.recover_stale_agent_tasks": {"queue": "agent.control"},
    },
    beat_schedule={
        "recover-stale-agent-tasks": {
            "task": "backend.agents.recover_stale_agent_tasks",
            "schedule": AGENT_TASK_RECOVERY_INTERVAL_SECONDS,
            "options": {"queue": "agent.control"},
        },
    },
)
