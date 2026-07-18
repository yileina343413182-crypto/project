# -*- coding: utf-8 -*-
"""
B站追番排行前100番剧批量采集脚本

功能：
    1. 获取追番人数排行前100的番剧列表
    2. 逐个采集弹幕和评论
    3. 支持断点续爬（跳过已完成的番剧）
    4. 自动保存进度日志

用法：
    python crawl_bili_top100.py
    python crawl_bili_top100.py --top_n 50 --max_episodes 12
"""

import os
import sys
import json
import time
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bilibili_crawler import BiliSession, fetch_top_anime, crawl_anime, safe_filename

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "data", "raw", "bilibili_top100")
PROGRESS_FILE = os.path.join(OUTPUT_DIR, "_progress.json")


def load_progress():
    """加载采集进度"""
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"completed": [], "stats": {}}


def save_progress(progress):
    """保存采集进度"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="B站追番排行前100批量采集")
    parser.add_argument("--top_n", type=int, default=100, help="采集排行前N（默认100）")
    parser.add_argument("--max_episodes", type=int, default=50, help="每部番剧最大集数（默认50）")
    parser.add_argument("--max_comments", type=int, default=400, help="每部番剧最大评论数（默认400）")
    parser.add_argument("--start_from", type=int, default=1, help="从第几名开始（默认1）")
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 初始化会话
    bili = BiliSession()

    # 获取排行榜
    anime_list = fetch_top_anime(bili, top_n=args.top_n)
    if not anime_list:
        logger.error("获取排行榜失败！")
        return

    # 保存排行榜到文件
    ranking_path = os.path.join(OUTPUT_DIR, "_ranking.json")
    with open(ranking_path, "w", encoding="utf-8") as f:
        json.dump(anime_list, f, ensure_ascii=False, indent=2)
    logger.info("排行榜已保存: %s", ranking_path)

    # 加载进度
    progress = load_progress()
    completed_ids = set(progress["completed"])

    total = len(anime_list)
    total_dm = 0
    total_cm = 0
    failed = []

    print(f"\n{'='*60}")
    print(f"  B站追番排行前{total}番剧 - 批量采集")
    print(f"  弹幕 + 评论，每部最多{args.max_episodes}集，评论上限{args.max_comments}条")
    print(f"  输出目录: {OUTPUT_DIR}")
    print(f"  已完成: {len(completed_ids)}个")
    print(f"{'='*60}\n")

    for idx, anime in enumerate(anime_list, 1):
        if idx < args.start_from:
            continue

        sid = anime["season_id"]
        title = anime["title"]

        if str(sid) in completed_ids:
            logger.info("[%d/%d] 跳过（已完成）: %s", idx, total, title)
            continue

        print(f"\n{'─'*50}")
        logger.info("[%d/%d] 开始采集: %s (season_id=%s, %s)",
                     idx, total, title, sid, anime.get("order", ""))

        try:
            result = crawl_anime(
                bili, sid, anime_title=title,
                max_episodes=args.max_episodes,
                max_comments=args.max_comments,
                output_dir=OUTPUT_DIR
            )

            if result["danmaku_count"] > 0 or result["comment_count"] > 0:
                progress["completed"].append(str(sid))
                progress["stats"][str(sid)] = {
                    "title": title,
                    "danmaku": result["danmaku_count"],
                    "comments": result["comment_count"],
                    "episodes": result["episodes"],
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                save_progress(progress)
                completed_ids.add(str(sid))

                total_dm += result["danmaku_count"]
                total_cm += result["comment_count"]
                logger.info("  ✓ 完成: %d集, %d弹幕, %d评论",
                             result["episodes"], result["danmaku_count"], result["comment_count"])
            else:
                failed.append({"rank": idx, "title": title, "season_id": sid, "reason": "无数据"})
                logger.warning("  ✗ 无数据: %s", title)

        except Exception as e:
            failed.append({"rank": idx, "title": title, "season_id": sid, "reason": str(e)})
            logger.error("  ✗ 异常: %s - %s", title, e)

        # 番剧间隔
        bili.sleep(3, 6)

    # 汇总报告
    print(f"\n{'='*60}")
    print(f"  采集完成!")
    print(f"  总弹幕: {total_dm} 条")
    print(f"  总评论: {total_cm} 条")
    print(f"  成功: {len(progress['completed'])} 个")
    print(f"  失败: {len(failed)} 个")
    print(f"{'='*60}")

    if failed:
        print("\n失败列表:")
        for f in failed:
            print(f"  [{f['rank']}] {f['title']} (season_id={f['season_id']}): {f['reason']}")

        failed_path = os.path.join(OUTPUT_DIR, "_failed.json")
        with open(failed_path, "w", encoding="utf-8") as f_out:
            json.dump(failed, f_out, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
