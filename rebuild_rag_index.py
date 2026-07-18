# -*- coding: utf-8 -*-
"""Run a safe full RAG rebuild and preserve the previous active collection on failure."""
from __future__ import annotations
import json
from backend.rag.indexer import make_collection_name, run_index_job
from backend.rag.storage import create_index_job

def main():
    collection = make_collection_name("rag_qwen_v4")
    job_id = create_index_job("rebuild", collection)
    print(json.dumps({"job_id": job_id, "collection_name": collection}, ensure_ascii=False), flush=True)
    result = run_index_job(job_id, collection, activate=True)
    print(json.dumps(result, ensure_ascii=False), flush=True)

if __name__ == "__main__":
    main()
