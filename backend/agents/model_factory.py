# -*- coding: utf-8 -*-
"""为 OpenAI 兼容服务创建 LangChain 聊天模型，并统一处理不可用降级。"""

from __future__ import annotations

import logging

from backend.config import LLM_API_KEY, LLM_BASE_URL, LLM_MAX_RETRIES, LLM_MODEL, LLM_PROVIDER, LLM_TIMEOUT

logger = logging.getLogger(__name__)


def langchain_available() -> bool:
    """检查 LangChain 核心依赖是否可导入，不触发模型网络请求。"""
    try:
        import langchain  # noqa: F401
        import langchain_openai  # noqa: F401
        return True
    except Exception:
        return False


def get_chat_model(temperature: float = 0.2, timeout: int | None = None, max_tokens: int | None = None):
    """返回 ChatOpenAI 实例；缺少依赖或密钥时返回 None 触发本地降级。"""
    if not LLM_API_KEY:
        logger.info("LLM_API_KEY is not configured; Agent Center will use fallback mode")
        return None

    try:
        from langchain_openai import ChatOpenAI
    except Exception as exc:
        logger.warning("langchain-openai is unavailable: %s", exc)
        return None

    try:
        kwargs = {
            "api_key": LLM_API_KEY,
            "base_url": LLM_BASE_URL,
            "model": LLM_MODEL,
            "temperature": temperature,
            "timeout": timeout if timeout is not None else LLM_TIMEOUT,
            "max_retries": LLM_MAX_RETRIES,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if LLM_PROVIDER == "qwen":
            kwargs["extra_body"] = {"enable_thinking": False}
        return ChatOpenAI(**kwargs)
    except Exception as exc:
        logger.warning("Failed to create LangChain chat model: %s", exc)
        return None

