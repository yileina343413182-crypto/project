# -*- coding: utf-8 -*-
"""读取可追溯的本地动漫知识源，不在索引阶段调用外部服务。"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from backend.config import PROJECT_ROOT


logger = logging.getLogger(__name__)
DEFAULT_KNOWLEDGE_PATH = os.path.join(
    PROJECT_ROOT,
    "data",
    "rag_knowledge.jsonl",
)


def load_knowledge_records(path: str | None = None) -> dict[int, dict]:
    """按本地 anime_id 读取 JSON 或 JSONL；无文件时保持现有索引流程。"""
    source_path = Path(
        path or os.environ.get("RAG_KNOWLEDGE_PATH", DEFAULT_KNOWLEDGE_PATH)
    )
    if not source_path.is_file():
        return {}

    try:
        if source_path.suffix.lower() == ".jsonl":
            records = [
                json.loads(line)
                for raw in source_path.read_text(encoding="utf-8").splitlines()
                if (line := raw.strip()) and not line.startswith("#")
            ]
        else:
            payload = json.loads(source_path.read_text(encoding="utf-8"))
            records = payload.get("items", []) if isinstance(payload, dict) else payload
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        logger.warning("RAG knowledge source is unavailable: %s", exc)
        return {}

    result: dict[int, dict] = {}
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict):
            continue
        try:
            anime_id = int(record.get("anime_id"))
        except (TypeError, ValueError):
            continue
        result[anime_id] = record
    return result
