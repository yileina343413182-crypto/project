# -*- coding: utf-8 -*-
"""
AI 推荐 API

"""

import logging
from flask import Blueprint, request, jsonify

from backend.database import get_all_anime, get_aspect_sentiment
from backend.services.bangumi import search_anime
from backend.services.llm import extract_recommendation_intent, generate_anime_description

logger = logging.getLogger(__name__)

recommend_bp = Blueprint("recommend", __name__)


@recommend_bp.route("/api/recommend", methods=["POST"])
def recommend():
    """
    AI 推荐接口

    Request:
        {"query": "用户输入"}

    Response:
        {
          "code": 200,
          "msg": "ok",
          "data": {
            "anime_id": int,
            "name": str,
            "platform": str,
            "comment_count": int,
            "description": str,
            "bangumi_rating": float,
            "aspect_sentiment": {
              "作画": {"positive": int, "neutral": int, "negative": int, "total": int},
              "剧情": {...},
              "声优": {...}
            },
            "llm_reply": str,
            "match_type": "exact" | "fuzzy" | "fallback"
          }
        }
    """
    body = request.get_json(silent=True) or {}
    query = (body.get("query") or "").strip()

    if not query:
        return jsonify({"code": 400, "msg": "query 不能为空", "data": None}), 400

    # 1. 获取数据库动漫列表
    anime_list = get_all_anime()
    if not anime_list:
        return jsonify({
            "code": 200, "msg": "ok",
            "data": {
                "anime_id": None, "name": None,
                "llm_reply": "数据库中暂无动漫数据，请先运行 generate_demo_data.py。",
                "description": "", "bangumi_rating": 0,
                "aspect_sentiment": {}, "match_type": "fallback",
                "platform": "", "comment_count": 0,
            },
        })

    anime_name_list = [a["name"] for a in anime_list]

    # 2. LLM 意图提取（失败时自动降级）
    intent = extract_recommendation_intent(query, anime_name_list)
    matched_name = intent["matched_name"]

    # 3. 在数据库中定位目标动漫
    target_anime = None
    match_type = "fallback"

    if matched_name:
        for a in anime_list:
            if a["name"] == matched_name:
                target_anime = a
                match_type = "fuzzy" if intent["fallback"] else "exact"
                break

    if target_anime is None:
        # 降级：选评论最多的动漫
        target_anime = max(anime_list, key=lambda x: x["comment_count"])
        match_type = "fallback"
        intent["reply"] = (
            f"未能找到完全匹配的动漫，为您推荐库中最热门的「{target_anime['name']}」："
        )

    # 4. 从 Bangumi 获取简介与评分
    description = ""
    bangumi_rating = 0.0
    bgm_info = search_anime(target_anime["name"])
    if bgm_info:
        summary = bgm_info.get("summary", "")
        description = summary[:200] + "……" if len(summary) > 200 else summary
        bangumi_rating = bgm_info.get("rating", 0.0) or 0.0
    if not description:
        # 当 Bangumi 无法获取简介时，调用 LLM 联网搜索或从评论归纳
        description = generate_anime_description(target_anime["name"], anime_id=target_anime["id"])

    # 5. 三维情感分析
    aspect_sentiment = get_aspect_sentiment(target_anime["id"])

    return jsonify({
        "code": 200,
        "msg": "ok",
        "data": {
            "anime_id": target_anime["id"],
            "name": target_anime["name"],
            "platform": target_anime["platform"],
            "comment_count": target_anime["comment_count"],
            "description": description,
            "bangumi_rating": bangumi_rating,
            "aspect_sentiment": aspect_sentiment,
            "llm_reply": intent["reply"],
            "match_type": match_type,
        },
    })
