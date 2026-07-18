# -*- coding: utf-8 -*-
"""
情感分析 API Blueprint

端点：
    GET  /api/sentiment/stats/<anime_id>  → 情感统计（饼图数据）
    GET  /api/sentiment/trend/<anime_id>  → 情感趋势（折线图数据）
    POST /api/sentiment/predict           → 实时情感分析预测
"""

import logging

from flask import Blueprint, request

from backend.database import get_sentiment_stats, get_sentiment_trend, get_sentiment_scatter
from backend.config import DEFAULT_MODEL, TEXTCNN_MODEL_DIR, BERT_MODEL_DIR

logger = logging.getLogger(__name__)

sentiment_bp = Blueprint("sentiment", __name__)

# 模型缓存（延迟加载）
_model_cache = {}


def _get_model(model_name=None):
    """延迟加载情感分析模型"""
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


def _success(data, msg="success"):
    return {"code": 200, "msg": msg, "data": data}


def _error(msg, code=400):
    return {"code": code, "msg": msg, "data": None}, code


@sentiment_bp.route("/api/sentiment/stats/<int:anime_id>", methods=["GET"])
def sentiment_stats(anime_id):
    """获取情感统计"""
    stats = get_sentiment_stats(anime_id)
    return _success(stats)


@sentiment_bp.route("/api/sentiment/trend/<int:anime_id>", methods=["GET"])
def sentiment_trend(anime_id):
    """获取情感趋势"""
    trend = get_sentiment_trend(anime_id)
    return _success(trend)


@sentiment_bp.route("/api/sentiment/scatter/<int:anime_id>", methods=["GET"])
def sentiment_scatter(anime_id):
    """获取逐条情感值（折线散点图数据）"""
    limit = request.args.get("limit", 600, type=int)
    data = get_sentiment_scatter(anime_id, limit=min(limit, 1000))
    return _success(data)


@sentiment_bp.route("/api/sentiment/predict", methods=["POST"])
def sentiment_predict():
    """
    实时情感分析预测

    请求体:
        {"text": "这部动漫太好看了", "model": "textcnn"}  # model可选，默认textcnn
    """
    data = request.get_json(silent=True)
    if not data or "text" not in data:
        return _error("缺少 text 参数")

    text = data["text"].strip()
    if not text:
        return _error("text 不能为空")

    model_name = data.get("model", DEFAULT_MODEL)
    classifier, model_name = _get_model(model_name)

    if classifier is None:
        return _error("模型加载失败: %s" % model_name, 500)

    try:
        results = classifier.predict(text)
        result = results[0]
        result["model"] = model_name
        result["text"] = text
        return _success(result)
    except Exception as e:
        logger.error("预测失败: %s", e)
        return _error("预测失败: %s" % str(e), 500)
