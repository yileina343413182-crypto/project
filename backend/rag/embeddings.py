# -*- coding: utf-8 -*-
"""OpenAI-compatible embedding client with safe fallback."""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Iterable

import requests

logger = logging.getLogger(__name__)


# 默认跟随聊天模型服务商
EMBEDDING_PROVIDER = os.environ.get(
    "EMBEDDING_PROVIDER",
    os.environ.get("LLM_PROVIDER", "qwen"),
).strip().lower()


_EMBEDDING_PRESETS = {
    "qwen": {
        "base_url": (
            "https://dashscope.aliyuncs.com/"
            "compatible-mode/v1"
        ),
        "model": "text-embedding-v4",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "text-embedding-3-small",
    },
}

_embedding_preset = _EMBEDDING_PRESETS.get(
    EMBEDDING_PROVIDER,
    _EMBEDDING_PRESETS["qwen"],
)


# 根据 provider 选择密钥，避免 Qwen 误用 OpenAI Key
_EMBEDDING_KEY_NAMES = {
    "qwen": (
        "EMBEDDING_API_KEY",
        "DASHSCOPE_API_KEY",
        "QWEN_API_KEY",
        "LLM_API_KEY",
    ),
    "openai": (
        "EMBEDDING_API_KEY",
        "OPENAI_API_KEY",
        "LLM_API_KEY",
    ),
}

EMBEDDING_API_KEY = next(
    (
        os.environ.get(name, "").strip()
        for name in _EMBEDDING_KEY_NAMES.get(
            EMBEDDING_PROVIDER,
            ("EMBEDDING_API_KEY", "LLM_API_KEY"),
        )
        if os.environ.get(name, "").strip()
    ),
    "",
)

EMBEDDING_BASE_URL = os.environ.get(
    "EMBEDDING_BASE_URL",
    _embedding_preset["base_url"],
).strip().rstrip("/")

EMBEDDING_MODEL = os.environ.get(
    "EMBEDDING_MODEL",
    _embedding_preset["model"],
).strip()

try:
    EMBEDDING_TIMEOUT = int(
        os.environ.get("EMBEDDING_TIMEOUT", "20")
    )
except ValueError:
    EMBEDDING_TIMEOUT = 20


class EmbeddingClient:
    def __init__(
        self,
        api_key: str = EMBEDDING_API_KEY,
        base_url: str = EMBEDDING_BASE_URL,
        model: str = EMBEDDING_MODEL,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def embed_texts(
        self,
        texts: Iterable[str],
    ) -> list[list[float]] | None:
        items = list(texts)

        if not items or not self.available:
            return None

        try:
            response = requests.post(
                f"{self.base_url}/embeddings",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "input": items,
                },
                timeout=EMBEDDING_TIMEOUT,
            )

            if not response.ok:
                logger.warning(
                    "Embedding HTTP %s: %s",
                    response.status_code,
                    response.text[:300],
                )
                return None

            payload = response.json()
            data = payload.get("data", [])

            embeddings = [
                item["embedding"]
                for item in data
                if item.get("embedding") is not None
            ]

            if len(embeddings) != len(items):
                logger.warning(
                    "Embedding response count mismatch: "
                    "expected=%s, actual=%s",
                    len(items),
                    len(embeddings),
                )
                return None

            return embeddings

        except requests.Timeout:
            logger.warning(
                "Embedding request timed out after %s seconds",
                EMBEDDING_TIMEOUT,
            )
            return None
        except Exception as exc:
            logger.warning(
                "Embedding request failed: %s",
                exc,
            )
            return None


def stable_content_hash(content: str) -> str:
    return hashlib.sha256(
        (content or "").encode("utf-8")
    ).hexdigest()


def embedding_status() -> dict:
    return {
        "provider": EMBEDDING_PROVIDER,
        "model": EMBEDDING_MODEL,
        "base_url": EMBEDDING_BASE_URL,
        "configured": bool(EMBEDDING_API_KEY),
    }
