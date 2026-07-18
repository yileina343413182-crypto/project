# -*- coding: utf-8 -*-
"""
Bangumi 公开 API 封装

用于获取动漫简介、评分等元数据。
缓存策略：进程级内存字典（重启后失效）。
"""

import logging
import os
import requests
from urllib.parse import quote

logger = logging.getLogger(__name__)

# 进程级缓存
_search_cache: dict = {}
_detail_cache: dict = {}

HEADERS = {
    "User-Agent": "anime-sentiment-system/1.0",
    "Accept": "application/json",
}
BASE_URL = "https://api.bgm.tv"
TIMEOUT = float(os.environ.get("BANGUMI_TIMEOUT", "3"))


def search_anime(name: str) -> dict | None:
    """
    通过动漫名在 Bangumi 搜索，返回第一条结果。

    Returns:
        dict: {bgm_id, name, summary, rating, image} 或 None
    """
    if name in _search_cache:
        return _search_cache[name]

    try:
        url = f"{BASE_URL}/search/subject/{quote(name)}?type=2&responseGroup=small&max_results=5"
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        items = data.get("list", [])
        if not items:
            _search_cache[name] = None
            return None

        item = items[0]
        result = {
            "bgm_id": item.get("id"),
            "name": item.get("name_cn") or item.get("name", ""),
            "summary": item.get("summary", ""),
            "rating": item.get("rating", {}).get("score", 0),
            "image": item.get("images", {}).get("common", ""),
        }
        _search_cache[name] = result
        return result

    except Exception as e:
        logger.warning("Bangumi 搜索失败 [%s]: %s", name, e)
        _search_cache[name] = None
        return None


def get_subject_detail(bgm_id: int) -> dict | None:
    """
    通过 Bangumi subject ID 获取详情（summary 更完整）。

    Returns:
        dict: {bgm_id, name, summary, rating, image} 或 None
    """
    if bgm_id in _detail_cache:
        return _detail_cache[bgm_id]

    try:
        url = f"{BASE_URL}/v0/subjects/{bgm_id}"
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        result = {
            "bgm_id": bgm_id,
            "name": data.get("name_cn") or data.get("name", ""),
            "summary": data.get("summary", ""),
            "rating": data.get("rating", {}).get("score", 0),
            "image": data.get("images", {}).get("common", ""),
        }
        _detail_cache[bgm_id] = result
        return result

    except Exception as e:
        logger.warning("Bangumi 详情获取失败 [id=%s]: %s", bgm_id, e)
        _detail_cache[bgm_id] = None
        return None
