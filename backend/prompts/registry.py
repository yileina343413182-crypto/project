# -*- coding: utf-8 -*-
"""Small prompt template registry with version metadata."""

from __future__ import annotations

from dataclasses import dataclass
from string import Template


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: str
    template: str

    def render(self, **kwargs) -> str:
        return Template(self.template).safe_substitute(**kwargs)


_PROMPTS = {
    "opinion_report": PromptTemplate(
        name="opinion_report",
        version="rag-v1",
        template=(
            "Role: Anime public opinion diagnosis agent.\n"
            "Goal: Generate a structured report only from tool data and RAG evidence.\n"
            "Citation rule: every concrete conclusion should cite doc_id values from evidence_refs.\n"
            "No fabrication: if evidence is insufficient, say so explicitly.\n"
            "Output must match the configured JSON schema. Return JSON only; do not explain your process.\n"
            "Hard limits: each array has at most 4 items; each conclusion is at most 80 Chinese characters.\n"
            "Representative comments have at most 2 items per sentiment label.\n"
            "When the supplied data does not support a conclusion, write 数据不足.\n\n"
            "User query: $query\n"
            "Anime: $anime\n"
            "Tool context: $context\n"
            "RAG evidence: $evidence\n"
        ),
    ),
    "recommendation": PromptTemplate(
        name="recommendation",
        version="rag-v2-bounded",
        template=(
            "Role: Anime recommendation agent.\n"
            "All data is prepared; do not call tools. Recommend only from Candidates.\n"
            "Goal: Recommend 1 to 3 anime using user preferences and candidate-local evidence.\n"
            "Citation rule: each recommendation may cite only doc_id values inside that candidate.\n"
            "No fabrication: do not invent comments, ratings, or topics.\n"
            "If preference is ambiguous, ask one clarifying question.\n"
            "Output must match the configured JSON schema.\n\n"
            "User id: $user_id\n"
            "User query: $query\n"
            "Preferences: $preferences\n"
            "Candidates: $candidates\n"
            "History: $history\n"
            "RAG evidence: $evidence\n"
        ),
    ),
    "evidence_answering": PromptTemplate(
        name="evidence_answering",
        version="rag-v1",
        template=(
            "Answer using only the supplied evidence. Cite doc_id values. "
            "Say data is insufficient when evidence does not support the answer.\n"
            "Question: $query\nEvidence: $evidence\n"
        ),
    ),
}


def get_prompt(name: str) -> PromptTemplate:
    return _PROMPTS[name]


def prompt_trace(name: str, model: str, retrieval_top_k: int, fallback: bool) -> dict:
    prompt = get_prompt(name)
    return {
        "template_name": prompt.name,
        "template_version": prompt.version,
        "model": model,
        "retrieval_top_k": retrieval_top_k,
        "fallback": fallback,
    }

