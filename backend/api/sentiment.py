# -*- coding: utf-8 -*-
"""
情感分析 API

端点：
    GET  /api/sentiment/stats/<anime_id>  → 情感统计（饼图数据）
    GET  /api/sentiment/trend/<anime_id>  → 情感趋势（折线图数据）
    POST /api/sentiment/predict           → 实时情感分析预测
"""

import logging

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.common import error_response, ok
from backend.db.async_repository import get_sentiment_stats, get_sentiment_trend, get_sentiment_scatter
from backend.db.session import get_async_session
from backend.config import DEFAULT_MODEL, TEXTCNN_MODEL_DIR, BERT_MODEL_DIR

logger = logging.getLogger(__name__)

router = APIRouter()

# 模型体积较大，首次预测时延迟加载，之后按模型名复用进程内实例。
_model_cache = {}


def _get_model(model_name=None):
    """按需加载 BERT/TextCNN，并缓存成功创建的预测器。"""
    if model_name is None:
        model_name = DEFAULT_MODEL

    if model_name in _model_cache:
        return _model_cache[model_name], model_name

    try:
        if model_name == "textcnn":
            from models.textcnn_classifier import TextCNNClassifier
            classifier = TextCNNClassifier()
            classifier.load(TEXTCNN_MODEL_DIR)
            _model_cache[model_name] = classifier
            logger.info("TextCNN模型已加载")
        elif model_name == "bert":
            from models.bert_classifier import BertClassifier
            classifier = BertClassifier()
            classifier.load(BERT_MODEL_DIR)
            _model_cache[model_name] = classifier
            logger.info("BERT模型已加载")
        else:
            return None, model_name

        return _model_cache[model_name], model_name
    except Exception as e:
        logger.error("模型加载失败 (%s): %s", model_name, e)
        return None, model_name


@router.get("/api/sentiment/stats/{anime_id}")
async def sentiment_stats(anime_id: int, session: AsyncSession = Depends(get_async_session)):
    """返回饼图使用的三类情感汇总。"""
    stats = await get_sentiment_stats(session, anime_id)
    return ok(stats)


@router.get("/api/sentiment/trend/{anime_id}")
async def sentiment_trend(anime_id: int, session: AsyncSession = Depends(get_async_session)):
    """返回按日期聚合的三类情感趋势。"""
    trend = await get_sentiment_trend(session, anime_id)
    return ok(trend)


@router.get("/api/sentiment/scatter/{anime_id}")
async def sentiment_scatter(
    anime_id: int,
    limit: int = Query(default=600),
    session: AsyncSession = Depends(get_async_session),
):
    """返回带文本和置信度坐标的情感散点样本。"""
    data = await get_sentiment_scatter(session, anime_id, limit=min(limit, 1000))
    return ok(data)


@router.post("/api/sentiment/predict")
def sentiment_predict(data: dict | None = Body(default=None)):
    """
    实时情感分析预测

    请求体:
        {"text": "这部动漫太好看了", "model": "textcnn"}  # model可选，默认textcnn
    """
    if not data or "text" not in data:
        return error_response("缺少 text 参数")

    text = str(data["text"]).strip()
    if not text:
        return error_response("text 不能为空")

    model_name = data.get("model", DEFAULT_MODEL)
    classifier, model_name = _get_model(model_name)

    if classifier is None:
        return error_response("模型加载失败: %s" % model_name, 500)

    try:
        results = classifier.predict(text)
        result = results[0]
        result["model"] = model_name
        result["text"] = text
        return ok(result)
    except Exception as e:
        logger.error("预测失败: %s", e)
        return error_response("预测失败: %s" % str(e), 500)
