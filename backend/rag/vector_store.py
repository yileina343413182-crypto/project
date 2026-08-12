# -*- coding: utf-8 -*-
"""Chroma 向量库的薄封装，统一延迟导入、批量写入和查询结果格式。"""

from __future__ import annotations

import logging
import os
import time
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

from backend.config import PROJECT_ROOT
from backend.rag.embeddings import EmbeddingClient

logger = logging.getLogger(__name__)

CHROMA_PATH = os.environ.get("CHROMA_PATH", os.path.join(PROJECT_ROOT, "data", "chroma"))


def chroma_available() -> bool:
    """检查 Chroma 依赖是否可导入，不创建客户端或集合。"""
    try:
        import chromadb  # noqa: F401
        return True
    except Exception:
        return False


class ChromaVectorStore:
    """按需创建持久化客户端，并封装集合级 upsert/query。"""
    def __init__(self, persist_path: str = CHROMA_PATH):
        self.persist_path = persist_path
        self._client = None
        self.last_embedding_dimension = 0

    @property
    def available(self) -> bool:
        return chroma_available()

    def _get_client(self):
        """延迟创建客户端，避免不使用 RAG 时仍初始化 Chroma。"""
        if self._client is not None:
            return self._client
        if not self.available:
            return None
        import chromadb

        os.makedirs(self.persist_path, exist_ok=True)
        from chromadb.config import Settings
        self._client = chromadb.PersistentClient(path=self.persist_path, settings=Settings(anonymized_telemetry=False))
        return self._client

    def get_collection(self, collection_name: str):
        """取得或创建使用余弦距离的命名集合。"""
        client = self._get_client()
        if client is None:
            return None
        return client.get_or_create_collection(name=collection_name)

    def upsert(self, collection_name: str, docs: list[dict], embedding_client: EmbeddingClient | None = None, batch_size: int = 10) -> int:
        """分批生成向量并幂等写入；Embedding 失败时返回已写数量。"""
        if not docs:
            return 0
        embedding_client = embedding_client or EmbeddingClient()
        if not self.available or not embedding_client.available:
            return 0
        collection = self.get_collection(collection_name)
        if collection is None:
            return 0
        indexed = 0
        for start in range(0, len(docs), max(1, batch_size)):
            batch = docs[start:start + max(1, batch_size)]
            embeddings = None
            for attempt in range(4):
                embeddings = embedding_client.embed_texts([doc["content"] for doc in batch])
                if embeddings:
                    break
                if attempt < 3:
                    time.sleep(5 * (attempt + 1))
            if not embeddings or len(embeddings) != len(batch):
                logger.warning("Embedding batch failed at offset %s; indexed=%s", start, indexed)
                break
            dimension = len(embeddings[0]) if embeddings[0] else 0
            if self.last_embedding_dimension and dimension != self.last_embedding_dimension:
                logger.error("Embedding dimension changed from %s to %s", self.last_embedding_dimension, dimension)
                break
            self.last_embedding_dimension = dimension
            collection.upsert(ids=[doc["doc_id"] for doc in batch], documents=[doc["content"] for doc in batch],
                metadatas=[_clean_metadata(doc["metadata"]) for doc in batch], embeddings=embeddings)
            indexed += len(batch)
            if start + len(batch) < len(docs):
                time.sleep(2.1)
        return indexed

    def query(
        self,
        collection_name: str,
        query: str,
        top_k: int = 6,
        anime_id: int | None = None,
        embedding_client: EmbeddingClient | None = None,
    ) -> list[dict]:
        """查询相似文档，可按 anime_id 过滤，并转换距离为相似度。"""
        embedding_client = embedding_client or EmbeddingClient()
        if not self.available or not embedding_client.available:
            return []
        embeddings = embedding_client.embed_texts([query])
        if not embeddings:
            return []
        collection = self.get_collection(collection_name)
        if collection is None:
            return []
        where = {"anime_id": anime_id} if anime_id is not None else None
        result = collection.query(
            query_embeddings=embeddings,
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        docs = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        output = []
        for idx, content in enumerate(docs):
            metadata = metadatas[idx] or {}
            distance = distances[idx] if idx < len(distances) else 1.0
            similarity = max(0.0, min(1.0, 1.0 - float(distance)))
            output.append({
                "content": content,
                "metadata": metadata,
                "similarity": round(similarity, 4),
                "rank": idx + 1,
                "source_label": _source_label(metadata),
            })
        return output


def _clean_metadata(metadata: dict) -> dict:
    """把 Chroma 不支持的复杂元数据转成字符串或基础标量。"""
    cleaned = {}
    for key, value in (metadata or {}).items():
        if value is None:
            cleaned[key] = ""
        elif isinstance(value, (str, int, float, bool)):
            cleaned[key] = value
        else:
            cleaned[key] = str(value)
    return cleaned


def _source_label(metadata: dict) -> str:
    source_type = metadata.get("source_type", "")
    anime_name = metadata.get("anime_name", "")
    comment_id = metadata.get("comment_id")
    if source_type == "comment" and comment_id:
        return f"{anime_name} comment #{comment_id}"
    return f"{anime_name} {source_type}".strip()


