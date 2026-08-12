# -*- coding: utf-8 -*-
"""Bangumi 公开 API 的容错封装。

用于补充动漫简介、评分等非核心元数据。结果使用进程内字典缓存，重启后
失效；超时、限流或响应异常统一返回 ``None``，不阻断本地业务主流程。
"""

import logging
import os
import requests
from urllib.parse import quote

logger = logging.getLogger(__name__)

# 搜索结果和详情分开缓存，避免同一主题重复发起两种请求。
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
    按名称搜索最相关的 Bangumi 动漫主题。

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
    读取单个 Bangumi 主题详情，并规范化常用字段。

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
