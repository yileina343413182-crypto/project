# -*- coding: utf-8 -*-
"""推荐 Agent 图片附件的安全存储、读取与视觉上下文提取。"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import warnings

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select

from backend.agents.model_factory import get_chat_model
from backend.agents.prompt_security import inspect_untrusted_text
from backend.config import (
    LLM_MODEL,
    RECOMMEND_LLM_TIMEOUT,
)
from backend.database import orm_session
from backend.db.models import AgentAttachment
from backend.prompts.registry import get_prompt, prompt_trace


_FORMATS = {
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
    "WEBP": ("image/webp", "webp"),
}
_ALLOWED_MIME_TYPES = {mime for mime, _extension in _FORMATS.values()}
AGENT_IMAGE_MAX_BYTES = 5 * 1024 * 1024
AGENT_IMAGE_MAX_PIXELS = 16_000_000


class AttachmentError(ValueError):
    """附件格式、大小或归属不符合要求。"""


def _clean_image(raw: bytes, declared_mime_type: str) -> tuple[bytes, str, int, int]:
    if declared_mime_type not in _ALLOWED_MIME_TYPES:
        raise AttachmentError("仅支持 JPEG、PNG 或 WebP 图片")
    if not raw:
        raise AttachmentError("图片内容为空")
    if len(raw) > AGENT_IMAGE_MAX_BYTES:
        raise AttachmentError("图片不能超过 5 MB")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as probe:
                image_format = str(probe.format or "").upper()
                if image_format not in _FORMATS:
                    raise AttachmentError("图片实际格式不是 JPEG、PNG 或 WebP")
                actual_mime, _extension = _FORMATS[image_format]
                if actual_mime != declared_mime_type:
                    raise AttachmentError("图片声明格式与实际内容不一致")
                if getattr(probe, "is_animated", False) or getattr(probe, "n_frames", 1) != 1:
                    raise AttachmentError("不支持动态图片")
                width, height = probe.size
                if width <= 0 or height <= 0 or width * height > AGENT_IMAGE_MAX_PIXELS:
                    raise AttachmentError("图片像素不能超过 1600 万")
                probe.load()
                has_alpha = "A" in probe.getbands() or "transparency" in probe.info
                mode = "RGB" if image_format == "JPEG" else ("RGBA" if has_alpha else "RGB")
                converted = probe.convert(mode)
                clean = Image.new(mode, converted.size)
                clean.paste(converted)

        output = io.BytesIO()
        save_options = {
            "JPEG": {"format": "JPEG", "quality": 90, "optimize": True},
            "PNG": {"format": "PNG", "optimize": True},
            "WEBP": {"format": "WEBP", "quality": 90, "method": 4},
        }[image_format]
        clean.save(output, **save_options)
        cleaned = output.getvalue()
    except AttachmentError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError, OSError, ValueError) as exc:
        raise AttachmentError("图片文件已损坏或无法解析") from exc

    if len(cleaned) > AGENT_IMAGE_MAX_BYTES:
        raise AttachmentError("图片安全处理后仍超过 5 MB，请压缩后重试")
    return cleaned, actual_mime, width, height


def prepare_image(raw: bytes, declared_mime_type: str) -> dict:
    """校验并重编码一张图片，返回可原子写入数据库的字段。"""
    cleaned, mime_type, width, height = _clean_image(raw, declared_mime_type)
    return {
        "content": cleaned,
        "mime_type": mime_type,
        "byte_size": len(cleaned),
        "width": width,
        "height": height,
        "sha256": hashlib.sha256(cleaned).hexdigest(),
    }


def get_bound_attachment(user_id: int, session_id: int, attachment_id: int) -> dict:
    """Worker 侧按用户和会话同时校验附件归属。"""
    with orm_session() as session:
        record = session.scalar(
            select(AgentAttachment).where(
                AgentAttachment.id == int(attachment_id),
                AgentAttachment.user_id == int(user_id),
                AgentAttachment.session_id == int(session_id),
            )
        )
        if record is None:
            raise AttachmentError("图片附件不存在或不属于当前会话")
        return {
            "id": record.id,
            "content": bytes(record.content),
            "mime_type": record.mime_type,
            "byte_size": record.byte_size,
            "width": record.width,
            "height": record.height,
            "sha256": record.sha256,
        }


def _response_text(response) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = "\n".join(
            str(item.get("text") or "") if isinstance(item, dict) else str(item)
            for item in content
        )
    return re.sub(r"<think>.*?</think>", "", str(content or ""), flags=re.DOTALL).strip()


def _parse_json_object(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("视觉模型没有返回 JSON 对象")
    value = json.loads(cleaned[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("视觉模型返回格式无效")
    return value


def _safe_image_context(data: dict) -> str:
    fields = []
    for key, label, limit in (
        ("description", "画面描述", 800),
        ("possible_anime_names", "可能涉及的动画", 300),
        ("visual_tags", "视觉标签", 300),
        ("mood", "氛围", 200),
        ("ocr_text", "可见文字", 500),
    ):
        value = data.get(key)
        if isinstance(value, list):
            value = "、".join(str(item) for item in value[:10])
        inspection = inspect_untrusted_text(value, source=f"image_{key}", max_chars=limit)
        text = inspection["sanitized_text"].strip()
        if text and inspection["risk"] != "high":
            fields.append(f"{label}：{text}")
    confidence = str(data.get("confidence") or "low").lower()
    fields.append(f"识别置信度：{confidence if confidence in {'high', 'medium', 'low'} else 'low'}")
    return "\n".join(fields)


def analyze_recommendation_image(
    user_id: int,
    session_id: int,
    attachment_id: int,
    query: str,
) -> dict:
    """使用当前统一 LLM_MODEL 把图片转成受控文本上下文。"""
    attachment = get_bound_attachment(user_id, session_id, attachment_id)
    raw = attachment.pop("content")
    if len(raw) != attachment["byte_size"]:
        raise AttachmentError("图片附件大小校验失败")
    if hashlib.sha256(raw).hexdigest() != attachment["sha256"]:
        raise AttachmentError("图片附件完整性校验失败")

    model = get_chat_model(0.1, timeout=RECOMMEND_LLM_TIMEOUT, max_tokens=900)
    if model is None:
        raise RuntimeError("图片理解模型暂不可用，请检查当前 LLM 配置后重试")
    prompt_template = get_prompt("recommendation_image")
    prompt = prompt_template.render(query=query[:1200])
    data_url = (
        f"data:{attachment['mime_type']};base64,"
        f"{base64.b64encode(raw).decode('ascii')}"
    )
    try:
        response = model.invoke([
            (
                "human",
                [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": prompt},
                ],
            )
        ])
        parsed = _parse_json_object(_response_text(response))
        context = _safe_image_context(parsed)
        if not context.strip():
            raise ValueError("视觉模型没有提供可用图片信息")
    except Exception as exc:
        raise RuntimeError("图片理解失败，请稍后重试或换一张更清晰的图片") from exc

    return {
        "context": context,
        "prompt_trace": prompt_trace(
            "recommendation_image",
            LLM_MODEL,
            0,
            False,
            prompt=prompt_template,
        ),
    }
