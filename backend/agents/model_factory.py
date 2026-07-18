# -*- coding: utf-8 -*-
"""LangChain model factory for OpenAI-compatible providers."""

from __future__ import annotations

import logging

from backend.config import LLM_API_KEY, LLM_BASE_URL, LLM_MAX_RETRIES, LLM_MODEL, LLM_PROVIDER, LLM_TIMEOUT

logger = logging.getLogger(__name__)


def langchain_available() -> bool:
    try:
        import langchain  # noqa: F401
        import langchain_openai  # noqa: F401
        return True
    except Exception:
        return False


def get_chat_model(temperature: float = 0.2, timeout: int | None = None, max_tokens: int | None = None):
    """Return a ChatOpenAI model or None when LangChain/credentials are unavailable."""
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

