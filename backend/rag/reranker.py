# -*- coding: utf-8 -*-
"""阿里云百炼文本 Rerank 客户端；失败时由上层保留 RRF 排序。"""

from __future__ import annotations

import logging

import requests

from backend import config

logger = logging.getLogger(__name__)


class BailianReranker:
    """调用百炼 qwen3-rerank，并把结果索引映射回 RRF 候选。"""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ):
        self.api_key = config.RERANK_API_KEY if api_key is None else api_key
        self.base_url = config.RERANK_BASE_URL if base_url is None else base_url
        self.model = config.RERANK_MODEL if model is None else model

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict] | None:
        """精排候选；配置缺失、超时或响应异常时返回 ``None``。"""
        if not candidates or not self.available:
            return None
        documents = [
            str(item.get("content", ""))[:config.RERANK_MAX_DOCUMENT_CHARS]
            for item in candidates
        ]
        try:
            response = requests.post(
                self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "query": query,
                    "documents": documents,
                    "top_n": min(top_k, len(documents)),
                    "instruct": "Given a web search query, retrieve relevant passages that answer the query.",
                },
                timeout=config.RERANK_TIMEOUT,
            )
            if not response.ok:
                logger.warning("Bailian rerank HTTP %s: %s", response.status_code, response.text[:300])
                return None
            payload = response.json()
            results = payload.get("results") or []
            ranked = []
            seen = set()
            for result in results:
                index = result.get("index")
                if not isinstance(index, int) or index < 0 or index >= len(candidates) or index in seen:
                    continue
                seen.add(index)
                item = dict(candidates[index])
                item["rerank_score"] = round(float(result.get("relevance_score", 0.0)), 8)
                item["rank"] = len(ranked) + 1
                ranked.append(item)
            return ranked or None
        except requests.Timeout:
            logger.warning("Bailian rerank timed out after %s seconds", config.RERANK_TIMEOUT)
            return None
        except Exception as exc:
            logger.warning("Bailian rerank failed: %s", exc)
            return None


def reranker_status() -> dict:
    """返回管理接口展示的百炼 Rerank 状态，不暴露 API Key。"""
    return {
        "provider": config.RERANK_PROVIDER,
        "model": config.RERANK_MODEL,
        "workspace_id": config.RERANK_WORKSPACE_ID,
        "base_url": config.RERANK_BASE_URL,
        "configured": bool(config.RERANK_API_KEY and config.RERANK_BASE_URL),
    }
