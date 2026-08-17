# -*- coding: utf-8 -*-
"""LLM/LangChain 不可用时的确定性本地降级结果生成器。

降级结果仍遵守与正常路径相同的 Pydantic 响应结构，前端无需编写另一套
解析逻辑。
"""

from __future__ import annotations

from backend.agents.schemas import (
    AgentStep,
    AnimeRecommendation,
    OpinionReportSchema,
    RECOMMEND_REASON_MAX_CHARS,
    RECOMMEND_REASON_MIN_CHARS,
    RecommendationEvidence,
    RecommendationResponseSchema,
)


def _sentiment_line(stats: dict) -> str:
    """把三类情感计数组织成可直接展示的摘要句。"""
    total = stats.get("total") or 0
    if not total:
        return "当前样本中缺少可用的情感预测结果，建议先执行批量情感预测。"
    pos = stats.get("positive", 0)
    neu = stats.get("neutral", 0)
    neg = stats.get("negative", 0)
    return f"共分析 {total} 条已标注评论，正向 {pos} 条，中性 {neu} 条，负向 {neg} 条。"


def _limited_text(value: object, limit: int) -> str:
    """限制本地字段长度，防止异常元数据挤占推荐理由。"""
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _preference_line(preferences: dict) -> str:
    labels = (
        ("preferred_genres", "题材"),
        ("preferred_moods", "氛围"),
        ("likes", "看重"),
        ("dislikes", "避开"),
    )
    parts = []
    for key, label in labels:
        values = preferences.get(key) or []
        if not isinstance(values, list):
            values = [values]
        cleaned = [_limited_text(value, 12) for value in values[:3] if str(value or "").strip()]
        if cleaned:
            parts.append(f"{label}{'、'.join(cleaned)}")
    return _limited_text("；".join(parts), 40) or "当前没有可安全引用的结构化偏好"


def _fallback_recommendation_reason(item: dict, preferences: dict) -> str:
    """仅依据候选元数据生成满足长度契约的本地推荐说明。"""
    name = _limited_text(item.get("name") or "这部作品", 30)
    topics = [
        _limited_text(topic, 16)
        for topic in (item.get("topics") or [])[:3]
        if str(topic or "").strip()
    ]
    topic_line = _limited_text(
        f"现有主题关键词为{'、'.join(topics)}" if topics else "当前未形成稳定主题关键词",
        40,
    )

    stats = item.get("sentiment") or {}
    total = int(stats.get("total") or 0)
    if total > 0:
        positive = int(stats.get("positive") or 0)
        neutral = int(stats.get("neutral") or 0)
        negative = int(stats.get("negative") or 0)
        positive_rate = positive / total * 100
        sentiment_line = (
            f"现有{total}条情感样本，正向{positive}条、中性{neutral}条、"
            f"负向{negative}条，正向占比约{positive_rate:.1f}%"
        )
    else:
        sentiment_line = "当前没有可用的情感统计样本"
    sentiment_line = _limited_text(sentiment_line, 55)

    try:
        score = float(item.get("final_score", item.get("score", 0)) or 0)
        score_text = f"{score:.3f}"
    except (TypeError, ValueError):
        score_text = "未提供"
    platform = _limited_text(item.get("platform"), 24)
    platform_line = (
        f"本地资料记录为{platform}，实际版权与地区可用性请在播放前核对"
        if platform
        else "本地资料未记录明确平台，请通过正规版权渠道查询"
    )

    reason = (
        f"《{name}》进入本次推荐，是因为本地排序综合参考了需求匹配、已有偏好、"
        f"评论口碑与样本热度，综合得分为{score_text}。"
        f"偏好匹配：{_preference_line(preferences)}；{topic_line}。"
        "它们仅用于限定筛选方向，不代表对剧情的额外推断。"
        f"口碑依据：{sentiment_line}。统计只反映当前收录样本，不能替代完整观众评价。"
        "核心看点：可先观察上述主题、角色推进与整体节奏是否符合预期；"
        "降级模式不会补写数据库没有的设定。"
        "适合观看场景：建议先试看一至两集，再按实际节奏和情绪体验决定是否继续。"
        "相近作品类比：当前没有可靠映射，因此不编造片名。"
        f"观看平台建议：{platform_line}。"
        "劝退点：本次结论主要依据有限评论和结构化统计；若在意画风、结局、CP关系或"
        "特定雷点，现有证据无法保证，开看前应核对作品简介和近期评论。"
    ).strip()
    if len(reason) > RECOMMEND_REASON_MAX_CHARS:
        prefix = reason[:RECOMMEND_REASON_MAX_CHARS]
        boundary = max(prefix.rfind(mark) for mark in "。！？")
        reason = (
            prefix[: boundary + 1]
            if boundary + 1 >= RECOMMEND_REASON_MIN_CHARS
            else prefix[: RECOMMEND_REASON_MAX_CHARS - 1].rstrip("，；、 ") + "。"
        )
    return reason


def build_opinion_fallback(anime: dict, stats: dict, topics: list, comments: dict, aspect: dict) -> OpinionReportSchema:
    """仅依据本地统计数据组装舆情报告，不虚构缺失证据。"""
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
    """按预计算候选分数生成最多三条本地推荐。"""
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
            reason=_fallback_recommendation_reason(item, preferences),
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
