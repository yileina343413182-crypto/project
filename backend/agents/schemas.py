# -*- coding: utf-8 -*-
"""Agent 中心的结构化输入/输出契约。

正常环境使用 Pydantic 校验；依赖尚未安装时提供最小兼容类，使确定性降级
路径仍能导入并返回字典。
"""

from __future__ import annotations

from typing import Any

try:
    from pydantic import BaseModel, Field
except Exception:  # pragma: no cover - keeps fallback mode importable before deps install
    # 这里只实现降级路径需要的构造和 model_dump，不模拟完整 Pydantic。
    class BaseModel:  # type: ignore
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

        def model_dump(self, *args, **kwargs):
            return dict(self.__dict__)

    def Field(default=None, **kwargs):  # type: ignore
        return default


# ===== 通用执行轨迹与检索证据 =====

class AgentStep(BaseModel):
    name: str = Field(default="", description="Step or tool name")
    status: str = Field(default="success", description="success, skipped, fallback, or error")
    detail: str = Field(default="", description="Short human-readable detail")
    elapsed_ms: int = Field(default=0, description="Elapsed time in milliseconds")


class CommentEvidence(BaseModel):
    content: str = ""
    sentiment_label: str = ""
    sentiment_score: float = 0.0
    likes: int = 0
    platform: str = ""
    publish_time: str = ""



class RetrievalEvidence(BaseModel):
    doc_id: str = ""
    source_type: str = ""
    content: str = ""
    similarity: float = 0.0
    rank: int = 0
    source_label: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class PromptTrace(BaseModel):
    template_name: str = ""
    template_version: str = ""
    template_hash: str = ""
    model: str = ""
    retrieval_top_k: int = 0
    fallback: bool = False
    security: dict[str, Any] = Field(default_factory=dict)


# ===== 舆情 Agent 输出 =====

class OpinionReportSchema(BaseModel):
    summary: str = Field(default="", description="Overall public opinion conclusion")
    sentiment_overview: str = Field(default="", description="Sentiment distribution summary")
    positive_points: list[str] = Field(default_factory=list)
    negative_points: list[str] = Field(default_factory=list)
    topic_insights: list[str] = Field(default_factory=list)
    risk_points: list[str] = Field(default_factory=list)
    audience_profile: str = ""
    operation_suggestions: list[str] = Field(default_factory=list)
    representative_comments: dict[str, list[CommentEvidence]] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    retrieval_evidence: list[RetrievalEvidence] = Field(default_factory=list)
    prompt_trace: PromptTrace = Field(default_factory=PromptTrace)
    agent_steps: list[AgentStep] = Field(default_factory=list)
    fallback: bool = False


class LLMOpinionReportSchema(BaseModel):
    """Small model-owned opinion report; backend adds diagnostics."""
    summary: str = Field(default="", description="Overall public opinion conclusion")
    sentiment_overview: str = Field(default="", description="Sentiment distribution summary")
    positive_points: list[str] = Field(default_factory=list)
    negative_points: list[str] = Field(default_factory=list)
    topic_insights: list[str] = Field(default_factory=list)
    risk_points: list[str] = Field(default_factory=list)
    audience_profile: str = ""
    operation_suggestions: list[str] = Field(default_factory=list)
    representative_comments: dict[str, list[CommentEvidence]] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)


# ===== 推荐 Agent 输出 =====

class RecommendationEvidence(BaseModel):
    sentiment: dict[str, Any] = Field(default_factory=dict)
    topics: list[str] = Field(default_factory=list)
    comments: list[CommentEvidence] = Field(default_factory=list)


class AnimeRecommendation(BaseModel):
    anime_id: int | None = None
    name: str = ""
    platform: str = ""
    comment_count: int = 0
    reason: str = ""
    match_tags: list[str] = Field(default_factory=list)
    evidence: RecommendationEvidence = Field(default_factory=RecommendationEvidence)
    evidence_refs: list[str] = Field(default_factory=list)
    retrieval_evidence: list[RetrievalEvidence] = Field(default_factory=list)


class LLMAnimeRecommendation(BaseModel):
    """Small model-owned recommendation decision; backend adds diagnostics."""
    anime_id: int
    reason: str
    match_tags: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class LLMRecommendationResponse(BaseModel):
    """Bounded output requested from the LLM only."""
    need_clarification: bool = False
    clarifying_question: str = ""
    recommendations: list[LLMAnimeRecommendation] = Field(default_factory=list)
    preference_updates: dict[str, list[Any]] = Field(default_factory=dict)


class RecommendationResponseSchema(BaseModel):
    need_clarification: bool = False
    clarifying_question: str = ""
    preference_stage: str = ""
    preference_progress: dict[str, Any] = Field(default_factory=dict)
    recommendations: list[AnimeRecommendation] = Field(default_factory=list)
    preference_updates: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    retrieval_evidence: list[RetrievalEvidence] = Field(default_factory=list)
    prompt_trace: PromptTrace = Field(default_factory=PromptTrace)
    agent_steps: list[AgentStep] = Field(default_factory=list)
    fallback: bool = False
    retrieval_mode: str = ""
    fallback_reason: str = ""
    evidence_coverage: dict[str, Any] = Field(default_factory=dict)
    context_budget: dict[str, Any] = Field(default_factory=dict)
    validation_warnings: list[str] = Field(default_factory=list)
    tool_rounds: int = 0


# ===== 可持久化的用户偏好 =====

class PreferenceSchema(BaseModel):
    likes: list[str] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)
    preferred_moods: list[str] = Field(default_factory=list)
    preferred_genres: list[str] = Field(default_factory=list)
    feedback: list[dict[str, Any]] = Field(default_factory=list)



