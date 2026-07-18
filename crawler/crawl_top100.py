# -*- coding: utf-8 -*-
"""
Bangumi评分排名Top100番剧评论批量采集脚本

功能：
    1. 从Bangumi排行榜页面抓取评分排名前100的番剧信息（subject_id、名称、排名、评分）
    2. 逐个采集每部番剧的吐槽箱（短评）数据
    3. 每部番剧的评论保存为单独的CSV文件
    4. 生成一份汇总CSV（番剧列表 + 采集统计）
    5. 支持断点续采（已采集的番剧自动跳过）

用法：
    python crawl_top100.py
    python crawl_top100.py --max_comment_pages 20    # 每部番剧最多采集20页评论
    python crawl_top100.py --start_rank 51            # 从第51名开始采集（断点续采）

依赖：
    pip install requests beautifulsoup4 pandas lxml
"""

import os
import re
import sys
import time
import json
import random
import argparse
import logging
from datetime import datetime

import requests
from bs4 import BeautifulSoup
import pandas as pd

# 将项目根目录加入路径，以便导入crawler模块
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from crawler.bangumi_crawler import crawl_comments, get_subject_info, save_to_csv, get_headers

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ===================== 常量配置 =====================

# Bangumi排行榜浏览页面（按排名排序）
RANKING_URL = "https://bgm.tv/anime/browser"
# 输出目录
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "data", "raw", "bangumi_top100")
# 汇总文件
SUMMARY_FILE = os.path.join(OUTPUT_DIR, "_summary_top100.csv")
# 采集进度文件
PROGRESS_FILE = os.path.join(OUTPUT_DIR, "_progress.json")


def fetch_ranking_page(page=1):
    """
    抓取Bangumi排行榜的单页数据，解析出番剧列表。

    Args:
        page: 页码（从1开始，每页24个条目）

    Returns:
        list: 包含番剧信息的字典列表，每项含 subject_id, name_cn, name, rank, score, vote_count
    """
    params = {
        "sort": "rank",
        "page": page,
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://bgm.tv/",
    }

    try:
        response = requests.get(RANKING_URL, params=params, headers=headers, timeout=15)
        response.raise_for_status()
        response.encoding = "utf-8"
    except requests.exceptions.RequestException as e:
        logger.error("抓取排行榜第%d页失败: %s", page, e)
        return []

    soup = BeautifulSoup(response.text, "lxml")
    anime_list = []

    # 解析每个条目（<li> 或 <div> 中包含 subject 链接）
    items = soup.select("#browserItemList > li")
    if not items:
        logger.warning("第%d页未找到条目，可能页面结构变更", page)
        return []

    for item in items:
        try:
            # 提取subject_id和标题
            title_link = item.select_one("h3 a.l")
            if not title_link:
                continue

            href = title_link.get("href", "")
            subject_id_match = re.search(r"/subject/(\d+)", href)
            if not subject_id_match:
                continue

            subject_id = int(subject_id_match.group(1))
            name = title_link.get_text(strip=True)

            # 提取中文名（如果有small标签）
            name_cn_tag = item.select_one("h3 small.grey")
            name_cn = name_cn_tag.get_text(strip=True) if name_cn_tag else ""

            # 如果h3的a标签是中文名，small是日文名，需要调换
            # 通常h3>a是中文名（如果有），small是原名
            # 实际Bangumi排行榜：h3>a 显示中文名，small.grey 显示原名
            # 如果没有中文名，h3>a 就是原名
            display_name = name  # h3中的名称（通常是中文名）

            # 提取排名
            rank_tag = item.select_one("span.rank")
            rank = 0
            if rank_tag:
                rank_text = rank_tag.get_text(strip=True).replace("Rank", "").strip()
                if rank_text.isdigit():
                    rank = int(rank_text)

            # 提取评分（small.fade 中为评分数值）
            score = 0.0
            vote_count = 0
            score_tag = item.select_one("small.fade")
            if score_tag:
                score_text = score_tag.get_text(strip=True)
                try:
                    score = float(score_text)
                except ValueError:
                    pass

            # 提取评分人数（span.tip_j 中格式如 "(9709人评分)"）
            vote_tag = item.select_one("span.tip_j")
            if vote_tag:
                vote_text = vote_tag.get_text(strip=True)
                vote_match = re.search(r"(\d+)", vote_text)
                if vote_match:
                    vote_count = int(vote_match.group(1))

            anime_list.append({
                "subject_id": subject_id,
                "name": display_name,
                "name_original": name_cn,  # small标签中的原名
                "rank": rank,
                "score": score,
                "vote_count": vote_count,
            })

        except (AttributeError, ValueError) as e:
            logger.debug("解析单个条目时出错: %s", e)
            continue

    logger.info("排行榜第%d页解析完成，获取 %d 个条目", page, len(anime_list))
    return anime_list


def fetch_top_anime(top_n=100):
    """
    从排行榜获取评分排名前N的番剧列表。

    Args:
        top_n: 需要获取的番剧数量，默认100

    Returns:
        list: 排名前N的番剧信息列表
    """
    all_anime = []
    # 每页24个条目，计算需要的页数
    pages_needed = (top_n // 24) + (1 if top_n % 24 > 0 else 0)

    logger.info("开始获取排名前%d的番剧，预计需要%d页", top_n, pages_needed)

    for page in range(1, pages_needed + 1):
        anime_list = fetch_ranking_page(page)
        if not anime_list:
            logger.warning("第%d页获取失败，停止获取", page)
            break

        all_anime.extend(anime_list)
        logger.info("已获取 %d / %d 个番剧", len(all_anime), top_n)

        if len(all_anime) >= top_n:
            break

        # 翻页间隔
        time.sleep(random.uniform(2, 4))

    # 截取前top_n个
    all_anime = all_anime[:top_n]
    logger.info("共获取 %d 个番剧信息", len(all_anime))
    return all_anime


def load_progress():
    """加载采集进度（已完成的subject_id集合）"""
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("completed", []))
        except (json.JSONDecodeError, KeyError):
            pass
    return set()


def save_progress(completed_ids):
    """保存采集进度"""
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "completed": list(completed_ids),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }, f, ensure_ascii=False, indent=2)


def batch_crawl_comments(anime_list, max_comment_pages=20, start_rank=1):
    """
    批量采集番剧评论数据。

    Args:
        anime_list: 番剧信息列表
        max_comment_pages: 每部番剧最大评论采集页数
        start_rank: 从第几名开始采集（用于断点续采）

    Returns:
        list: 采集统计结果列表
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 加载已完成的进度
    completed_ids = load_progress()
    stats = []

    total = len(anime_list)
    for i, anime in enumerate(anime_list):
        rank = anime.get("rank", i + 1)
        subject_id = anime["subject_id"]
        display_name = anime["name"]

        # 跳过排名在start_rank之前的
        if rank < start_rank:
            continue

        # 跳过已完成的
        if subject_id in completed_ids:
            logger.info("[%d/%d] 跳过已采集: #%d %s (subject_id=%d)",
                        i + 1, total, rank, display_name, subject_id)
            stats.append({
                "rank": rank,
                "subject_id": subject_id,
                "name": display_name,
                "name_original": anime.get("name_original", ""),
                "score": anime.get("score", 0),
                "vote_count": anime.get("vote_count", 0),
                "comment_count": "已跳过",
                "status": "已采集",
            })
            continue

        logger.info("=" * 60)
        logger.info("[%d/%d] 开始采集: #%d %s (subject_id=%d)",
                    i + 1, total, rank, display_name, subject_id)
        logger.info("=" * 60)

        # 采集评论
        comments = crawl_comments(subject_id, max_pages=max_comment_pages)

        # 给每条评论添加番剧元信息
        for c in comments:
            c["anime_title"] = display_name
            c["anime_rank"] = rank
            c["anime_score"] = anime.get("score", 0)

        # 保存单部番剧的CSV
        comment_count = len(comments)
        if comments:
            safe_name = "".join(
                c if c.isalnum() or c in "_-" else "_" for c in display_name
            )
            # 文件名格式: rank_subjectid_名称.csv
            filename = f"{rank:03d}_{subject_id}_{safe_name}.csv"
            output_path = os.path.join(OUTPUT_DIR, filename)
            save_to_csv(comments, output_path)
            status = "完成"
        else:
            status = "无评论"
            logger.warning("番剧 %s 没有采集到评论", display_name)

        # 记录统计
        stats.append({
            "rank": rank,
            "subject_id": subject_id,
            "name": display_name,
            "name_original": anime.get("name_original", ""),
            "score": anime.get("score", 0),
            "vote_count": anime.get("vote_count", 0),
            "comment_count": comment_count,
            "status": status,
        })

        # 更新进度
        completed_ids.add(subject_id)
        save_progress(completed_ids)

        # 保存中间汇总（防止中途中断丢失统计数据）
        pd.DataFrame(stats).to_csv(SUMMARY_FILE, index=False, encoding="utf-8-sig")

        # 番剧间间隔，避免过于频繁
        if i < total - 1:
            sleep_time = random.uniform(3, 6)
            logger.info("等待 %.1f 秒后继续...", sleep_time)
            time.sleep(sleep_time)

    return stats


def main():
    """命令行入口函数"""
    parser = argparse.ArgumentParser(description="Bangumi排名Top100番剧评论批量采集")
    parser.add_argument("--top_n", type=int, default=100,
                        help="采集排名前N的番剧（默认100）")
    parser.add_argument("--max_comment_pages", type=int, default=20,
                        help="每部番剧最大评论采集页数（默认20，每页约20条）")
    parser.add_argument("--start_rank", type=int, default=1,
                        help="从第几名开始采集（用于断点续采，默认1）")
    parser.add_argument("--list_only", action="store_true",
                        help="仅获取番剧列表，不采集评论")
    args = parser.parse_args()

    logger.info("========================================")
    logger.info("Bangumi Top%d 番剧评论批量采集", args.top_n)
    logger.info("========================================")

    # 第一步：获取排名前N的番剧列表
    logger.info("步骤1: 获取排行榜数据...")
    anime_list = fetch_top_anime(top_n=args.top_n)

    if not anime_list:
        logger.error("获取排行榜失败，退出")
        return

    # 打印番剧列表
    print(f"\n{'='*70}")
    print(f"{'排名':>4}  {'Subject ID':>10}  {'评分':>5}  {'名称'}")
    print(f"{'-'*70}")
    for anime in anime_list:
        print(f"  #{anime['rank']:<3}  {anime['subject_id']:>10}  "
              f"{anime['score']:>5.1f}  {anime['name']}")
    print(f"{'='*70}")
    print(f"共 {len(anime_list)} 部番剧\n")

    # 保存番剧列表
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    list_path = os.path.join(OUTPUT_DIR, "_anime_list_top100.csv")
    pd.DataFrame(anime_list).to_csv(list_path, index=False, encoding="utf-8-sig")
    logger.info("番剧列表已保存到: %s", list_path)

    if args.list_only:
        logger.info("仅列表模式，不采集评论")
        return

    # 第二步：批量采集评论
    logger.info("步骤2: 开始批量采集评论（每部最多%d页）...", args.max_comment_pages)
    stats = batch_crawl_comments(
        anime_list,
        max_comment_pages=args.max_comment_pages,
        start_rank=args.start_rank
    )

    # 第三步：保存最终汇总
    if stats:
        df_stats = pd.DataFrame(stats)
        df_stats.to_csv(SUMMARY_FILE, index=False, encoding="utf-8-sig")

        total_comments = sum(
            s["comment_count"] for s in stats
            if isinstance(s["comment_count"], int)
        )
        completed = sum(1 for s in stats if s["status"] in ("完成", "已采集"))
        no_comments = sum(1 for s in stats if s["status"] == "无评论")

        logger.info("========================================")
        logger.info("采集任务完成!")
        logger.info("  番剧总数: %d", len(stats))
        logger.info("  成功采集: %d", completed)
        logger.info("  无评论:   %d", no_comments)
        logger.info("  评论总数: %d", total_comments)
        logger.info("  汇总文件: %s", SUMMARY_FILE)
        logger.info("  数据目录: %s", OUTPUT_DIR)
        logger.info("========================================")


if __name__ == "__main__":
    main()
