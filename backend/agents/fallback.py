# -*- coding: utf-8 -*-
"""Fallback report generators used when LangChain or LLM calls fail."""

from __future__ import annotations

from backend.agents.schemas import (
    AgentStep,
    AnimeRecommendation,
    OpinionReportSchema,
    RecommendationEvidence,
    RecommendationResponseSchema,
)


def _sentiment_line(stats: dict) -> str:
    total = stats.get("total") or 0
    if not total:
        return "当前样本中缺少可用的情感预测结果，建议先执行批量情感预测。"
    pos = stats.get("positive", 0)
    neu = stats.get("neutral", 0)
    neg = stats.get("negative", 0)
    return f"共分析 {total} 条已标注评论，正向 {pos} 条，中性 {neu} 条，负向 {neg} 条。"


def build_opinion_fallback(anime: dict, stats: dict, topics: list, comments: dict, aspect: dict) -> OpinionReportSchema:
    topic_text = []
    for topic in topics[:5]:
        words = topic.get("keywords") or []
        labels = [w.get("word", "") for w in words[:5] if isinstance(w, dict)]
        if labels:
            topic_text.append("、".join(labels))

    negative_total = stats.get("negative", 0)
    positive_total = stats.get("positive", 0)
    total = stats.get("total", 0) or 1
    negative_rate = negative_total / total
    positive_rate = positive_total / total

    positives = ["评论中的正向反馈占比较高，说明作品基础口碑稳定。"] if positive_rate >= 0.45 else ["正向反馈存在，但优势点需要结合主题和代表评论进一步解释。"]
    negatives = ["负面评论占比偏高，需要关注观众集中吐槽点。"] if negative_rate >= 0.3 else ["负面评论未形成明显压倒性风险，但仍建议持续观察。"]

    if aspect:
        for name, value in aspect.items():
            if value.get("total", 0):
                positives.append(f"{name} 相关评论中正向 {value.get('positive', 0)} 条，负向 {value.get('negative', 0)} 条。")

    return OpinionReportSchema(
        summary=f"《{anime.get('name', '未知作品')}》舆情诊断已基于本地统计完成。",
        sentiment_overview=_sentiment_line(stats),
        positive_points=positives[:4],
        negative_points=negatives[:4],
        topic_insights=topic_text or ["暂无稳定主题结果，可先运行 LDA 主题建模。"],
        risk_points=["建议重点关注负面评论高频主题和趋势突增日期。"],
        audience_profile="当前报告基于评论文本、情感标签和主题关键词推断，适合用作初步舆情判断。",
        operation_suggestions=["结合代表性负评优化作品介绍或推荐话术。", "对正向主题进行内容运营放大。"],
        representative_comments=comments,
        agent_steps=[AgentStep(name="fallback_opinion_report", status="fallback", detail="使用本地统计模板生成舆情报告")],
        fallback=True,
    )


def build_recommendation_fallback(query: str, candidates: list, preferences: dict) -> RecommendationResponseSchema:
    normalized = "".join(query.strip().lower().split()).strip("，。！？!? ")
    if normalized in {"", "推荐", "推荐一下", "推荐动漫", "想看动漫", "有没有推荐", "随便推荐", "不知道看什么"}:
        return RecommendationResponseSchema(
            need_clarification=True,
            clarifying_question="想看轻松治愈、剧情强冲突，还是口碑稳定的经典作品？",
            recommendations=[],
            preference_updates={},
            agent_steps=[AgentStep(name="fallback_clarify", status="fallback", detail="输入偏好不足，先追问")],
            fallback=True,
        )

    recs = []
    for item in candidates[:3]:
        evidence = RecommendationEvidence(
            sentiment=item.get("sentiment", {}),
            topics=item.get("topics", []),
            comments=item.get("comments", []),
        )
        recs.append(AnimeRecommendation(
            anime_id=item.get("id"),
            name=item.get("name", ""),
            platform=item.get("platform", ""),
            comment_count=item.get("comment_count", 0),
            reason=f"根据你的描述和本地评论数据，{item.get('name', '这部作品')} 与当前偏好较匹配，且样本评论量较高。",
            match_tags=item.get("match_tags", ["本地匹配", "评论证据"]),
            evidence=evidence,
        ))

    return RecommendationResponseSchema(
        need_clarification=False,
        clarifying_question="",
        recommendations=recs,
        preference_updates={"last_query": query, "preferences": preferences},
        agent_steps=[AgentStep(name="fallback_recommendation", status="fallback", detail="使用本地排序生成推荐")],
        fallback=True,
    )
