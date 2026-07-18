# -*- coding: utf-8 -*-
"""
B站追番人数Top100番剧弹幕与评论批量采集

功能：
    1. 通过B站番剧索引API获取追番人数排名前100的番剧
    2. 获取每部番剧的所有正片剧集信息（aid/cid）
    3. 批量采集每集的评论和弹幕
    4. 支持断点续传

用法：
    # 采集Top100番剧（每集最多10页评论）
    python bilibili_top100.py --top_n 100 --max_comment_pages 10

    # 每部只采集前3集
    python bilibili_top100.py --top_n 100 --max_episodes 3

    # 从第20名开始续传
    python bilibili_top100.py --top_n 100 --start_rank 20

    # 使用cookie提高成功率
    python bilibili_top100.py --top_n 100 --cookie "SESSDATA=xxx; bili_jct=xxx"

依赖：
    pip install requests pandas
"""

import os
import sys
import json
import time
import random
import re
import zlib
import argparse
import logging
import xml.etree.ElementTree as ET
from datetime import datetime

import requests
import pandas as pd

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ===================== 路径配置 =====================

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "data", "raw", "bilibili_top100")

# ===================== API配置 =====================

# 番剧索引API（按追番人数排序）
INDEX_API = "https://api.bilibili.com/pgc/season/index/result"
# 番剧详情API（获取剧集列表）
SEASON_API = "https://api.bilibili.com/pgc/view/web/season"
# 评论API
COMMENT_API = "https://api.bilibili.com/x/v2/reply/main"
# 弹幕API
DANMAKU_API = "https://api.bilibili.com/x/v1/dm/list.so"

# 随机User-Agent池
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


def get_headers(cookie=None):
    """获取带有随机User-Agent的请求头"""
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": "https://www.bilibili.com",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if cookie:
        headers["Cookie"] = cookie
    return headers


def parse_count_str(count_str):
    """
    解析B站数量字符串，如 '3354.3万追番' -> 33543000
    """
    if not count_str:
        return 0
    # 去除非数字和单位的文字
    count_str = re.sub(r'[追番播放]', '', count_str).strip()
    try:
        if "亿" in count_str:
            return int(float(count_str.replace("亿", "")) * 100_000_000)
        elif "万" in count_str:
            return int(float(count_str.replace("万", "")) * 10_000)
        else:
            return int(float(count_str))
    except (ValueError, TypeError):
        return 0


def safe_filename(name):
    """生成安全的文件名，替换非法字符"""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip()


# ==================== 排行榜获取 ====================

def fetch_top_anime(top_n=100, cookie=None):
    """
    获取B站追番人数排名前top_n的番剧列表。

    Args:
        top_n: 获取前N部番剧
        cookie: B站cookie（可选）

    Returns:
        list[dict]: 番剧信息列表
    """
    all_anime = []
    pagesize = 20
    total_pages = (top_n + pagesize - 1) // pagesize

    logger.info("开始获取追番人数排名前%d的番剧，预计需要%d页", top_n, total_pages)

    for page in range(1, total_pages + 1):
        params = {
            "season_type": 1,       # 番剧
            "area": -1,             # 全部地区
            "style_id": -1,         # 全部风格
            "release_date": -1,     # 全部时间
            "season_status": -1,    # 全部状态
            "order": 2,             # 追番人数排序
            "st": 1,
            "sort": 0,              # 降序
            "page": page,
            "pagesize": pagesize,
            "season_month": -1,
            "year": -1,
        }

        try:
            resp = requests.get(
                INDEX_API, params=params,
                headers=get_headers(cookie), timeout=15
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 0:
                logger.error("索引API返回错误: code=%s, message=%s",
                             data.get("code"), data.get("message"))
                break

            items = data.get("data", {}).get("list", [])
            if not items:
                logger.info("第%d页无数据，停止获取", page)
                break

            for item in items:
                order_info = item.get("order", {})
                anime_info = {
                    "rank": len(all_anime) + 1,
                    "season_id": item.get("season_id"),
                    "media_id": item.get("media_id"),
                    "title": item.get("title", ""),
                    "follow": order_info.get("follow", "0"),
                    "follow_count": parse_count_str(order_info.get("follow", "0")),
                    "play": order_info.get("play", "0"),
                    "score": order_info.get("score", "0"),
                    "badge": item.get("badge", ""),
                    "index_show": item.get("index_show", ""),
                    "is_finish": item.get("is_finish", 0),
                }
                all_anime.append(anime_info)

            logger.info("第%d页获取完成，累计 %d 部番剧", page, len(all_anime))

            if len(all_anime) >= top_n:
                break

            time.sleep(random.uniform(1, 2))

        except requests.RequestException as e:
            logger.error("获取第%d页排行榜失败: %s", page, e)
            time.sleep(3)
            continue

    result = all_anime[:top_n]
    logger.info("共获取 %d 部番剧信息", len(result))
    return result


# ==================== 剧集信息获取 ====================

def fetch_episodes(season_id, cookie=None):
    """
    获取番剧的所有正片剧集信息。

    Args:
        season_id: 番剧season_id

    Returns:
        list[dict]: 剧集列表，每项含 aid, cid, title, long_title, duration
    """
    params = {"season_id": season_id}
    try:
        resp = requests.get(
            SEASON_API, params=params,
            headers=get_headers(cookie), timeout=15
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            logger.warning("获取season %s 剧集失败: %s",
                           season_id, data.get("message"))
            return []

        episodes = []
        for ep in data.get("result", {}).get("episodes", []):
            episodes.append({
                "aid": ep.get("aid"),
                "cid": ep.get("cid"),
                "title": ep.get("title", ""),
                "long_title": ep.get("long_title", ""),
                "duration": ep.get("duration", 0),
            })

        logger.info("  season %s 共 %d 集", season_id, len(episodes))
        return episodes

    except requests.RequestException as e:
        logger.error("获取season %s 剧集请求失败: %s", season_id, e)
        return []


# ==================== 评论采集 ====================

def crawl_episode_comments(aid, max_pages=10, cookie=None):
    """
    采集单集视频的评论。

    Args:
        aid: 视频av号
        max_pages: 最大采集页数
        cookie: B站cookie

    Returns:
        list[dict]: 评论列表
    """
    all_comments = []
    next_cursor = 0

    for page in range(max_pages):
        params = {
            "type": 1,
            "oid": aid,
            "mode": 3,          # 热度排序
            "next": next_cursor,
        }
        try:
            resp = requests.get(
                COMMENT_API, params=params,
                headers=get_headers(cookie), timeout=15
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 0:
                if data.get("code") == -412:
                    logger.warning("    评论API被限流(aid=%s)，暂停10秒", aid)
                    time.sleep(10)
                break

            replies = data.get("data", {}).get("replies")
            if not replies:
                break

            for reply in replies:
                member = reply.get("member", {})
                all_comments.append({
                    "content": reply.get("content", {}).get("message", ""),
                    "ctime": datetime.fromtimestamp(
                        reply.get("ctime", 0)
                    ).strftime("%Y-%m-%d %H:%M:%S"),
                    "like": reply.get("like", 0),
                    "reply_count": reply.get("rcount", 0),
                    "user_name": member.get("uname", ""),
                    "user_level": member.get("level_info", {}).get("current_level", 0),
                    "aid": aid,
                })

            cursor = data.get("data", {}).get("cursor", {})
            next_cursor = cursor.get("next", 0)
            if cursor.get("is_end", True):
                break

            time.sleep(random.uniform(1, 2.5))

        except requests.exceptions.Timeout:
            logger.warning("    评论请求超时(aid=%s, page=%d)", aid, page + 1)
            time.sleep(3)
            continue
        except requests.RequestException as e:
            logger.warning("    评论请求失败(aid=%s): %s", aid, e)
            time.sleep(5)
            break

    return all_comments


# ==================== 弹幕采集 ====================

def crawl_episode_danmaku(cid, cookie=None):
    """
    采集单集视频的弹幕（XML格式）。

    Args:
        cid: 视频cid
        cookie: B站cookie

    Returns:
        list[dict]: 弹幕列表
    """
    params = {"oid": cid}
    danmaku_list = []

    try:
        resp = requests.get(
            DANMAKU_API, params=params,
            headers=get_headers(cookie), timeout=20
        )
        resp.raise_for_status()

        # 解析弹幕XML（可能是deflate压缩的）
        content = resp.content
        xml_text = None
        try:
            xml_text = content.decode('utf-8')
            if not xml_text.strip().startswith('<?xml') and not xml_text.strip().startswith('<i>'):
                raise ValueError("not xml")
        except (UnicodeDecodeError, ValueError):
            try:
                xml_text = zlib.decompress(content, -zlib.MAX_WBITS).decode('utf-8')
            except zlib.error:
                try:
                    xml_text = zlib.decompress(content).decode('utf-8')
                except zlib.error:
                    logger.warning("    弹幕数据解码失败(cid=%s)", cid)
                    return danmaku_list

        root = ET.fromstring(xml_text)

        for d in root.findall('.//d'):
            p_attr = d.get('p', '')
            text = d.text or ''
            if not text.strip():
                continue

            fields = p_attr.split(',')
            if len(fields) >= 8:
                try:
                    danmaku_list.append({
                        "content": text,
                        "time_in_video": float(fields[0]),
                        "mode": int(fields[1]),
                        "font_size": int(fields[2]),
                        "color": int(fields[3]),
                        "timestamp": datetime.fromtimestamp(
                            int(fields[4])
                        ).strftime("%Y-%m-%d %H:%M:%S"),
                        "pool": int(fields[5]),
                        "user_hash": fields[6],
                        "dmid": fields[7],
                        "cid": cid,
                    })
                except (ValueError, OSError):
                    continue

    except ET.ParseError as e:
        logger.warning("    弹幕XML解析失败(cid=%s): %s", cid, e)
    except requests.RequestException as e:
        logger.warning("    弹幕请求失败(cid=%s): %s", cid, e)

    return danmaku_list


# ==================== 批量采集 ====================

def batch_crawl(anime_list, max_comment_pages=10, max_episodes=0,
                start_rank=1, cookie=None):
    """
    批量采集番剧的评论和弹幕。

    Args:
        anime_list: 番剧列表
        max_comment_pages: 每集最大评论页数
        max_episodes: 每部最大采集集数（0=全部）
        start_rank: 从第几名开始采集
        cookie: B站cookie
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 加载/初始化进度文件
    progress_file = os.path.join(OUTPUT_DIR, "_progress.json")
    completed = set()
    if os.path.exists(progress_file):
        try:
            with open(progress_file, "r", encoding="utf-8") as f:
                completed = set(json.load(f).get("completed", []))
            logger.info("已加载进度：%d 部番剧已完成", len(completed))
        except (json.JSONDecodeError, KeyError):
            completed = set()

    total = len(anime_list)
    grand_comments = 0
    grand_danmaku = 0

    for anime in anime_list:
        rank = anime["rank"]
        if rank < start_rank:
            continue

        season_id = str(anime["season_id"])
        title = anime["title"]

        if season_id in completed:
            logger.info("[%d/%d] 跳过已完成: %s", rank, total, title)
            continue

        logger.info("=" * 60)
        logger.info("[%d/%d] 开始采集: %s (season_id=%s, 追番=%s)",
                    rank, total, title, season_id, anime["follow"])
        logger.info("=" * 60)

        # 获取剧集信息
        episodes = fetch_episodes(int(season_id), cookie)
        if not episodes:
            logger.warning("  未获取到剧集信息，跳过")
            # 仍然标记为完成，避免重复尝试
            completed.add(season_id)
            with open(progress_file, "w", encoding="utf-8") as f:
                json.dump({"completed": list(completed)}, f, ensure_ascii=False)
            time.sleep(random.uniform(2, 4))
            continue

        if max_episodes > 0:
            episodes = episodes[:max_episodes]

        logger.info("  共 %d 集待采集", len(episodes))

        all_comments = []
        all_danmaku = []

        for i, ep in enumerate(episodes, 1):
            ep_label = f"第{ep['title']}话" if ep['title'].isdigit() else ep['title']
            logger.info("  [%d/%d] %s %s (aid=%s, cid=%s)",
                        i, len(episodes), ep_label, ep['long_title'],
                        ep['aid'], ep['cid'])

            # 采集评论
            comments = crawl_episode_comments(ep["aid"], max_pages=max_comment_pages, cookie=cookie)
            for c in comments:
                c["anime_title"] = title
                c["season_id"] = season_id
                c["episode"] = ep["title"]
                c["episode_title"] = ep["long_title"]
                c["anime_rank"] = rank
                c["anime_follow"] = anime["follow"]
            all_comments.extend(comments)
            logger.info("    评论: %d 条", len(comments))

            time.sleep(random.uniform(1, 2))

            # 采集弹幕
            danmaku = crawl_episode_danmaku(ep["cid"], cookie)
            for d in danmaku:
                d["anime_title"] = title
                d["season_id"] = season_id
                d["episode"] = ep["title"]
                d["episode_title"] = ep["long_title"]
                d["anime_rank"] = rank
            all_danmaku.extend(danmaku)
            logger.info("    弹幕: %d 条", len(danmaku))

            # 集间延时
            time.sleep(random.uniform(1.5, 3))

        # 保存文件
        safe_title = safe_filename(title)
        prefix = f"{rank:03d}_{season_id}_{safe_title}"

        if all_comments:
            comment_path = os.path.join(OUTPUT_DIR, f"{prefix}_comments.csv")
            pd.DataFrame(all_comments).to_csv(
                comment_path, index=False, encoding="utf-8-sig")
            logger.info("  评论已保存: %s (%d条)", os.path.basename(comment_path), len(all_comments))

        if all_danmaku:
            danmaku_path = os.path.join(OUTPUT_DIR, f"{prefix}_danmaku.csv")
            pd.DataFrame(all_danmaku).to_csv(
                danmaku_path, index=False, encoding="utf-8-sig")
            logger.info("  弹幕已保存: %s (%d条)", os.path.basename(danmaku_path), len(all_danmaku))

        grand_comments += len(all_comments)
        grand_danmaku += len(all_danmaku)

        logger.info("  本部合计: 评论 %d 条, 弹幕 %d 条", len(all_comments), len(all_danmaku))

        # 更新进度
        completed.add(season_id)
        with open(progress_file, "w", encoding="utf-8") as f:
            json.dump({"completed": list(completed)}, f, ensure_ascii=False)

        # 番剧间延时
        time.sleep(random.uniform(3, 6))

    # 生成汇总
    logger.info("=" * 60)
    logger.info("全部采集完成！")
    logger.info("  总计评论: %d 条", grand_comments)
    logger.info("  总计弹幕: %d 条", grand_danmaku)
    logger.info("  数据目录: %s", OUTPUT_DIR)
    logger.info("=" * 60)


# ==================== 命令行入口 ====================

def main():
    parser = argparse.ArgumentParser(description="B站追番Top100评论弹幕批量采集")
    parser.add_argument("--top_n", type=int, default=100,
                        help="采集前N部番剧（默认100）")
    parser.add_argument("--max_comment_pages", type=int, default=10,
                        help="每集最大评论页数（默认10）")
    parser.add_argument("--max_episodes", type=int, default=0,
                        help="每部最大采集集数，0=全部（默认0）")
    parser.add_argument("--start_rank", type=int, default=1,
                        help="从第几名开始采集，用于断点续传（默认1）")
    parser.add_argument("--cookie", type=str, default=None,
                        help="B站cookie（可选，提高采集成功率）")
    args = parser.parse_args()

    logger.info("=" * 50)
    logger.info("B站追番Top%d 番剧评论弹幕批量采集", args.top_n)
    logger.info("=" * 50)
    logger.info("参数: 每集最大评论页数=%d, 每部最大集数=%s",
                args.max_comment_pages,
                "全部" if args.max_episodes == 0 else str(args.max_episodes))

    # 第一步：获取排行榜数据
    logger.info("步骤1: 获取追番人数排行榜...")
    anime_list = fetch_top_anime(args.top_n, cookie=args.cookie)
    if not anime_list:
        logger.error("获取排行榜失败，退出")
        return

    # 打印排行榜
    print("\n" + "=" * 70)
    print(f"  {'排名':>4}  {'Season ID':>10}  {'追番数':>12}  {'评分':>5}  名称")
    print("-" * 70)
    for a in anime_list:
        print(f"  #{a['rank']:<4d} {a['season_id']:>10}  {a['follow']:>12}  {a['score']:>5}  {a['title']}")
    print("=" * 70)
    print(f"共 {len(anime_list)} 部番剧\n")

    # 保存番剧列表
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    list_path = os.path.join(OUTPUT_DIR, "_anime_list.csv")
    pd.DataFrame(anime_list).to_csv(list_path, index=False, encoding="utf-8-sig")
    logger.info("番剧列表已保存到: %s", list_path)

    # 第二步：批量采集
    logger.info("步骤2: 开始批量采集评论和弹幕...")
    batch_crawl(
        anime_list,
        max_comment_pages=args.max_comment_pages,
        max_episodes=args.max_episodes,
        start_rank=args.start_rank,
        cookie=args.cookie,
    )


if __name__ == "__main__":
    main()
