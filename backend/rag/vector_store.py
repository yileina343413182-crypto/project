# -*- coding: utf-8 -*-
"""Chroma 向量库的薄封装，统一延迟导入、批量写入和查询结果格式。"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

from backend.config import PROJECT_ROOT
from backend.rag.embeddings import EmbeddingClient

logger = logging.getLogger(__name__)


def _default_chroma_path(
    project_root: str = PROJECT_ROOT,
    platform: str = os.name,
    local_appdata: str | None = None,
) -> str:
    """避开 Windows 原生 hnswlib 无法持久化非 ASCII 路径的问题。"""
    project_path = os.path.join(project_root, "data", "chroma")
    if platform != "nt" or project_path.isascii():
        return project_path
    appdata_path = (local_appdata or os.environ.get("LOCALAPPDATA", "")).strip()
    if not appdata_path:
        return project_path
    return os.path.join(appdata_path, "bangumi-agent", "chroma")


CHROMA_PATH = os.environ.get("CHROMA_PATH", _default_chroma_path())
_PERSISTED_HNSW_FILES = {
    "header.bin",
    "data_level0.bin",
    "length.bin",
    "link_lists.bin",
    "index_metadata.pickle",
}


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

    def get_existing_collection(self, collection_name: str):
        """只打开已有集合；查询路径不得静默创建空集合。"""
        client = self._get_client()
        if client is None:
            return None
        return client.get_collection(name=collection_name)

    def persisted_index_status(self, collection_name: str | None) -> dict:
        """检查集合计数与本地 HNSW segment 文件，避免只相信 SQLite 元数据。"""
        result = {
            "collection_name": collection_name or "",
            "collection_exists": False,
            "chroma_count": 0,
            "vector_segment_id": "",
            "index_files_complete": False,
            "missing_files": sorted(_PERSISTED_HNSW_FILES),
            "error_type": "",
            "error": "",
        }
        if not collection_name or not self.available:
            return result
        try:
            collection = self.get_existing_collection(collection_name)
            if collection is None:
                return result
            result["collection_exists"] = True
            result["chroma_count"] = int(collection.count())

            sqlite_path = os.path.join(self.persist_path, "chroma.sqlite3")
            with sqlite3.connect(sqlite_path) as connection:
                row = connection.execute(
                    "SELECT id FROM segments "
                    "WHERE collection = ? AND scope = 'VECTOR' LIMIT 1",
                    (str(collection.id),),
                ).fetchone()
            if not row:
                result["error_type"] = "MissingVectorSegment"
                result["error"] = "collection has no persisted vector segment"
                return result

            segment_id = str(row[0])
            result["vector_segment_id"] = segment_id
            segment_path = os.path.join(self.persist_path, segment_id)
            existing = {
                entry.name
                for entry in os.scandir(segment_path)
                if entry.is_file()
            } if os.path.isdir(segment_path) else set()
            missing = sorted(_PERSISTED_HNSW_FILES - existing)
            result["missing_files"] = missing
            result["index_files_complete"] = not missing
            if missing:
                result["error_type"] = "IncompleteHnswSegment"
                result["error"] = "missing persisted HNSW files: " + ", ".join(missing)
        except Exception as exc:
            result["error_type"] = type(exc).__name__
            result["error"] = str(exc)[:300]
        return result

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
        collection = self.get_existing_collection(collection_name)
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


