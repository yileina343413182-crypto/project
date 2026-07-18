# -*- coding: utf-8 -*-
"""
数据查询 API Blueprint

端点：
    GET /api/anime/list                  → 所有动漫列表
    GET /api/comments/<anime_id>         → 分页查询评论
"""

from flask import Blueprint, request

from backend.database import get_all_anime, get_comments

data_bp = Blueprint("data", __name__)


def _success(data, msg="success"):
    return {"code": 200, "msg": msg, "data": data}


@data_bp.route("/api/anime/list", methods=["GET"])
def anime_list():
    """获取所有动漫列表"""
    data = get_all_anime()
    return _success(data)


@data_bp.route("/api/comments/<int:anime_id>", methods=["GET"])
def comments(anime_id):
    """
    分页查询评论

    查询参数：
        sentiment: 情感标签过滤 (positive/negative/neutral)
        page: 页码，默认1
        size: 每页大小，默认20
    """
    sentiment = request.args.get("sentiment", None)
    page = request.args.get("page", 1, type=int)
    size = request.args.get("size", 20, type=int)

    # 参数校验
    if page < 1:
        page = 1
    if size < 1 or size > 100:
        size = 20
    if sentiment and sentiment not in ("positive", "negative", "neutral"):
        sentiment = None

    data = get_comments(anime_id, sentiment=sentiment, page=page, page_size=size)
    return _success(data)
