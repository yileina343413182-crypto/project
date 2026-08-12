# -*- coding: utf-8 -*-
"""轻量 RAG 回归评估：运行固定查询并持久化命中与证据引用结果。"""

from __future__ import annotations

from backend.database import get_all_anime
from backend.rag.retriever import search_evidence
from backend.rag.storage import create_eval_run, finish_eval_run, save_eval_item


def run_builtin_eval(top_k: int = 5) -> dict:
    """执行内置用例，统计命中率并保存每条用例的检索模式和引用。"""
    run_id = create_eval_run()
    cases = _default_cases()
    passed = 0
    total = len(cases)

    for case in cases:
        result = search_evidence(case["query"], anime_id=case.get("anime_id"), top_k=top_k)
        evidence = result["evidence"]
        hit = any(
            (item.get("metadata") or {}).get("anime_id") == case.get("anime_id")
            for item in evidence
        ) if case.get("anime_id") is not None else bool(evidence)
        has_refs = all(item.get("doc_id") for item in evidence)
        item_passed = bool(hit and has_refs)
        passed += 1 if item_passed else 0
        save_eval_item(
            run_id,
            case["query"],
            item_passed,
            {
                "retrieval_hit": hit,
                "reference_coverage": 1.0 if has_refs and evidence else 0.0,
                "fallback": result["fallback"],
                "mode": result["mode"],
            },
            evidence,
        )

    metrics = {
        "total": total,
        "passed": passed,
        "retrieval_hit_rate": round(passed / total, 4) if total else 0,
        "reference_coverage": 1.0 if passed == total and total else 0,
        "hallucination_risk": 0 if passed == total else round((total - passed) / total, 4) if total else 0,
        "fallback_available": True,
    }
    finish_eval_run(run_id, metrics)
    return {"run_id": run_id, "metrics": metrics}


def _default_cases() -> list[dict]:
    """从现有动漫中构造少量可重复的冒烟用例，不宣称完整质量评估。"""
    items = get_all_anime()[:5]
    cases = []
    for item in items:
        cases.append({
            "query": f"{item['name']} 观众评论 口碑 情感",
            "anime_id": item["id"],
        })
    if not cases:
        cases.append({"query": "动漫 评论 情感 推荐", "anime_id": None})
    return cases
