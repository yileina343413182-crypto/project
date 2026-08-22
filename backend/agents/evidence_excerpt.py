# -*- coding: utf-8 -*-
"""为模型上下文选择完整段落或完整句子，不修改内部保存的证据正文。"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_PARAGRAPH_RE = re.compile(r"\n\s*\n+")
_SENTENCE_BOUNDARY_RE = re.compile(
    r"(?<=[。！？!?；;])|(?<=\.)(?=\s|$)"
)
_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+")


def _clean_paragraphs(content: str) -> list[str]:
    text = _URL_RE.sub("", unicodedata.normalize("NFKC", str(content or "")))
    paragraphs = []
    for part in _PARAGRAPH_RE.split(text.replace("\r\n", "\n")):
        cleaned = " ".join(part.split()).strip()
        if cleaned:
            paragraphs.append(cleaned)
    return paragraphs


def _sentences(paragraphs: list[str]) -> list[tuple[int, str]]:
    result = []
    index = 0
    for paragraph in paragraphs:
        for part in _SENTENCE_BOUNDARY_RE.split(paragraph):
            sentence = part.strip()
            if sentence:
                result.append((index, sentence))
                index += 1
    return result


def _topic_text(topics: Iterable | None) -> str:
    values = []
    for topic in topics or []:
        if isinstance(topic, dict):
            values.extend(str(value) for value in topic.values())
        else:
            values.append(str(topic))
    return " ".join(values)


def _terms(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    result = set()
    for token in _TOKEN_RE.findall(normalized):
        result.add(token)
        if re.fullmatch(r"[\u4e00-\u9fff]+", token) and len(token) > 2:
            result.update(token[index:index + 2] for index in range(len(token) - 1))
    return {term for term in result if term}


def _relevance_score(
    text: str,
    query: str,
    anime_name: str,
    topics: Iterable | None,
) -> int:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    weighted_terms = (
        [(_terms(query), 3), (_terms(anime_name), 4), (_terms(_topic_text(topics)), 2)]
    )
    return sum(
        weight * len(term) * normalized.count(term)
        for terms, weight in weighted_terms
        for term in terms
        if term in normalized
    )


def select_evidence_excerpt(
    full_content: str,
    *,
    query: str = "",
    anime_name: str = "",
    topics: Iterable | None = None,
    target_chars: int,
    remaining_chars: int,
) -> str:
    """在字符预算内优先保留完整段落，否则选择最相关的完整句子。"""
    if remaining_chars <= 0:
        return ""
    paragraphs = _clean_paragraphs(full_content)
    if not paragraphs:
        return ""
    full_text = "\n".join(paragraphs)
    soft_limit = min(max(1, target_chars), remaining_chars)
    if len(full_text) <= soft_limit:
        return full_text

    ranked_paragraphs = sorted(
        enumerate(paragraphs),
        key=lambda pair: (
            -_relevance_score(pair[1], query, anime_name, topics),
            pair[0],
        ),
    )
    if len(ranked_paragraphs[0][1]) <= soft_limit:
        selected = []
        used = 0
        for index, paragraph in ranked_paragraphs:
            extra = len(paragraph) + (1 if selected else 0)
            if used + extra <= soft_limit:
                selected.append((index, paragraph))
                used += extra
        return "\n".join(text for _, text in sorted(selected))

    ranked_sentences = sorted(
        _sentences(paragraphs),
        key=lambda pair: (
            -_relevance_score(pair[1], query, anime_name, topics),
            pair[0],
        ),
    )
    selected = []
    used = 0
    for index, sentence in ranked_sentences:
        extra = len(sentence) + (1 if selected else 0)
        if used + extra <= soft_limit:
            selected.append((index, sentence))
            used += extra
    if selected:
        return " ".join(text for _, text in sorted(selected))

    # 软目标不足以容纳任何完整句子时，可以使用剩余全局预算，但仍不截断句子。
    for _index, sentence in ranked_sentences:
        if len(sentence) <= remaining_chars:
            return sentence
    return ""
