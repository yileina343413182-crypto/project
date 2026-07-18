# -*- coding: utf-8 -*-
"""
主题挖掘 API Blueprint

端点：
    GET /api/topics/<anime_id>    → LDA主题列表及关键词
    GET /api/wordcloud/<anime_id> → 词云数据
"""

from flask import Blueprint

from backend.database import get_topics, get_wordcloud_data

topic_bp = Blueprint("topic", __name__)


def _success(data, msg="success"):
    return {"code": 200, "msg": msg, "data": data}


@topic_bp.route("/api/topics/<int:anime_id>", methods=["GET"])
def topics(anime_id):
    """获取LDA主题列表"""
    data = get_topics(anime_id)
    return _success(data)


@topic_bp.route("/api/wordcloud/<int:anime_id>", methods=["GET"])
def wordcloud(anime_id):
    """获取词云数据"""
    data = get_wordcloud_data(anime_id)
    return _success(data)
