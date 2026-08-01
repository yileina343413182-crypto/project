# -*- coding: utf-8 -*-
"""
动漫数据采集验证脚本（B站 & Bangumi）

修改下方「目标动漫常量」区域的四个变量即可切换目标动漫：
  ANIME_NAME         — 动漫中文名（用于数据库记录和文件命名）
  ANIME_ID           — 数据库 anime 表中的 id（需为整数，不同动漫请使用不同值）
  BANGUMI_SUBJECT_ID — bgm.tv 对应条目 id（为 None 则跳过 Bangumi 爬取）
  BILI_KEYWORD       — B站搜索关键词（尽量精确，避免命中错误番剧）

"""

import os
import sys
import argparse
import logging

from sqlalchemy import case, func, select

# Windows UTF-8 输出
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─── 路径配置 ───────────────────────────────────────────────────────────────

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

RAW_DIR    = os.path.join(PROJECT_ROOT, "data", "raw")
PROC_DIR   = os.path.join(PROJECT_ROOT, "data", "processed")

from backend.db.models import Anime, Comment
from backend.db.session import get_sessionmaker, session_scope

# ─── 目标动漫常量（修改这里即可切换目标动漫）────────────────────────────────

ANIME_NAME         = "mygo"         # 动漫中文名，用于数据库记录和文件命名
ANIME_ID           = 2                 # 数据库 anime 表 id，不同动漫请用不同值
BANGUMI_SUBJECT_ID = 428735            # bgm.tv 条目 id；设为 None 则跳过 Bangumi
BILI_KEYWORD       = "mygo"         # B站搜索关键词
BILI_SEASON_ID     = "73077"             # B站番剧 season_id；设为 None 则由关键词搜索自动匹配
# ─── 输出样式 ────────────────────────────────────────────────────────────────

GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"


def step(msg):
    print(f"\n{CYAN}{BOLD}{'─'*55}{RESET}")
    print(f"{CYAN}>> {msg}{RESET}")
    print(f"{CYAN}{'─'*55}{RESET}")


def ok(msg):   print(f"{GREEN}  [OK]  {msg}{RESET}")
def warn(msg): print(f"{YELLOW}  [!!]  {msg}{RESET}")
def err(msg):  print(f"{RED}  [ERR] {msg}{RESET}")


# ─── 步骤 0：初始化数据库 ────────────────────────────────────────────────────

def init_db():
    """初始化数据库表，并预插入 anime_id=0 的魔女之旅记录。"""
    step("初始化数据库")
    from crawler.cleaner import init_database
    conn = init_database()

    anime = conn.get(Anime, ANIME_ID)
    if anime:
        warn(f"anime 表中已有 id={ANIME_ID} 的记录: {anime.name}，跳过插入")
    else:
        conn.add(Anime(
            id=ANIME_ID,
            name=ANIME_NAME,
            platform="bilibili+bangumi",
            url="https://bgm.tv/subject/292970",
        ))
        conn.commit()
        ok(f"已插入 anime 记录: id={ANIME_ID}, name={ANIME_NAME}")

    conn.close()


# ─── 步骤 1：爬取 Bangumi ────────────────────────────────────────────────────

def crawl_bangumi(max_pages=10, dry_run=False):
    """爬取 Bangumi 吐槽箱，保存 CSV 到 data/raw/。"""
    step(f"爬取 Bangumi《{ANIME_NAME}》吐槽箱 (subject_id={BANGUMI_SUBJECT_ID}, max_pages={max_pages})")

    if dry_run:
        warn("dry-run 模式，跳过实际网络请求")
        return None

    try:
        from crawler.bangumi_crawler import crawl_comments, save_to_csv, get_subject_info

        # 获取条目信息
        info = get_subject_info(BANGUMI_SUBJECT_ID)
        if info:
            ok(f"条目信息: {info.get('name_cn') or info.get('name')} "
               f"| 评分: {info.get('score')} | 排名: #{info.get('rank')}")
        else:
            warn("无法获取条目详情，继续爬取评论")

        # 爬取评论
        comments = crawl_comments(BANGUMI_SUBJECT_ID, max_pages=max_pages)
        if not comments:
            err("Bangumi 未爬取到评论")
            return None

        # 添加元信息
        for c in comments:
            c["anime_title"] = ANIME_NAME

        # 保存 CSV
        os.makedirs(RAW_DIR, exist_ok=True)
        out_path = os.path.join(RAW_DIR, f"bangumi_{ANIME_NAME}.csv")
        save_to_csv(comments, out_path)
        ok(f"Bangumi 爬取完成：{len(comments)} 条，已保存至 {out_path}")
        return out_path

    except Exception as e:
        err(f"Bangumi 爬取失败: {e}")
        logger.exception(e)
        return None


# ─── 步骤 2：爬取 B站 ────────────────────────────────────────────────────────

def crawl_bilibili(max_comments=200, dry_run=False):
    """搜索并爬取 B站《魔女之旅》弹幕与评论，保存 CSV 到 data/raw/。"""
    step(f"搜索并爬取 B站《{ANIME_NAME}》(max_comments={max_comments})")

    if dry_run:
        warn("dry-run 模式，跳过实际网络请求")
        return None

    try:
        from crawler.bilibili_crawler import BiliSession, search_anime, crawl_anime

        session = BiliSession()

        # 优先使用指定的 season_id，避免关键词搜索命中错误番剧
        if BILI_SEASON_ID is not None:
            season_id = BILI_SEASON_ID
            title = ANIME_NAME
            ok(f"使用指定 season_id={season_id}，跳过搜索")
        else:
            # 关键词搜索并匹配最相关的番剧
            results = search_anime(session, BILI_KEYWORD)
            if not results:
                err("B站未搜索到相关番剧，请检查网络或关键词")
                return None

            target = None
            for r in results:
                t = r.get("title", "")
                if ANIME_NAME in t or BILI_KEYWORD.strip() in t:
                    target = r
                    break
            if target is None:
                target = results[0]

            season_id = target.get("season_id")
            title = target.get("title", ANIME_NAME)
            ok(f"命中番剧: {title}  season_id={season_id}")

        # 爬取弹幕 + 评论
        os.makedirs(RAW_DIR, exist_ok=True)
        stats = crawl_anime(
            session, season_id, title,
            max_episodes=50,
            max_comments=max_comments,
            output_dir=RAW_DIR,
        )
        ok(f"B站爬取完成: {stats['episodes']} 集，"
           f"弹幕 {stats['danmaku_count']} 条，评论 {stats['comment_count']} 条")
        return title   # 返回实际命中的 B站 title，供 CSV 过滤使用

    except Exception as e:
        err(f"B站爬取失败: {e}")
        logger.exception(e)
        return None


# ─── 步骤 3：清洗并入库（绑定 anime_id=0）────────────────────────────────────

def clean_and_import_all(dry_run=False, bili_title=None):
    """读取 data/raw/ 下目标动漫相关 CSV，清洗后写入数据库，anime_id 使用 ANIME_ID 常量。"""
    step(f"清洗数据并写入数据库 (anime_id={ANIME_ID})")

    if dry_run:
        warn("dry-run 模式，跳过清洗入库")
        return

    try:
        import pandas as pd
        from crawler.cleaner import (
            load_stopwords, clean_dataframe, save_to_database,
        )

        stopwords = load_stopwords()
        conn = get_sessionmaker()()

        # 构建搜索名称集合：常量名 + B站实际命中 title
        search_names = {ANIME_NAME, BILI_KEYWORD}
        if bili_title:
            search_names.add(bili_title)
        # 找到 data/raw/ 下所有相关 CSV
        csv_files = [
            f for f in os.listdir(RAW_DIR)
            if f.endswith(".csv") and any(name in f for name in search_names)
        ]
        if not csv_files:
            warn("data/raw/ 下未找到魔女之旅相关 CSV 文件")
            conn.close()
            return

        total_imported = 0
        for csv_file in csv_files:
            platform = "bangumi" if csv_file.startswith("bangumi") else "bilibili"
            input_path = os.path.join(RAW_DIR, csv_file)

            df = pd.read_csv(input_path, encoding="utf-8-sig")
            logger.info("读取 %s: %d 条", csv_file, len(df))

            min_len = 2 if "_dm_" in csv_file else 5
            df_clean = clean_dataframe(df, stopwords, platform=platform, min_length=min_len)

            if df_clean.empty:
                warn(f"{csv_file} 清洗后无有效数据")
                continue

            # 保存清洗后 CSV
            os.makedirs(PROC_DIR, exist_ok=True)
            out_path = os.path.join(PROC_DIR, f"cleaned_{csv_file}")
            df_clean.to_csv(out_path, index=False, encoding="utf-8-sig")
            ok(f"清洗后保存: {out_path} ({len(df_clean)} 条)")

            # 写入数据库，强制使用 anime_id=0
            n = save_to_database(conn, df_clean, ANIME_ID, platform)
            total_imported += n

        conn.close()
        ok(f"入库完成，共写入 {total_imported} 条评论（anime_id={ANIME_ID}）")

    except Exception as e:
        err(f"清洗/入库失败: {e}")
        logger.exception(e)


# ─── 步骤 4：汇总统计 ────────────────────────────────────────────────────────

def show_summary():
    """查询并打印 anime_id=0 的数据库统计。"""
    step("数据统计汇总")
    with session_scope() as conn:
        anime = conn.get(Anime, ANIME_ID)
        if anime:
            ok(f"anime 记录: id={anime.id}, name={anime.name}, platform={anime.platform}")
        else:
            warn(f"anime 表中不存在 id={ANIME_ID} 的记录")

        rows = conn.execute(
            select(
                Comment.platform,
                func.count(Comment.id),
                func.coalesce(
                    func.sum(case((Comment.sentiment_label.is_not(None), 1), else_=0)), 0
                ),
            )
            .where(Comment.anime_id == ANIME_ID)
            .group_by(Comment.platform)
            .order_by(Comment.platform)
        ).all()

    if rows:
        print(f"\n  {'平台':<12}{'评论总数':<12}{'已标注':<12}")
        print("  " + "─" * 36)
        for r in rows:
            print(f"  {r[0]:<12}{r[1]:<12}{r[2]:<12}")
    else:
        warn(f"comments 表中暂无 anime_id={ANIME_ID} 的数据")



# ─── 主入口 ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="魔女之旅项目验证脚本")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅初始化数据库，不实际爬取")
    parser.add_argument("--platform", choices=["bilibili", "bangumi", "both"],
                        default="both", help="爬取平台（默认 both）")
    parser.add_argument("--max_pages", type=int, default=10,
                        help="Bangumi 最大爬取页数（默认 10，每页约20条）")
    parser.add_argument("--max_comments", type=int, default=200,
                        help="B站每部最大评论数（默认 200）")
    args = parser.parse_args()

    print(f"\n{BOLD}{CYAN}{'='*55}")
    print(f"  魔女之旅 项目验证脚本")
    print(f"  Bangumi subject_id={BANGUMI_SUBJECT_ID}  |  anime_id={ANIME_ID}")
    print(f"{'='*55}{RESET}\n")

    # 步骤 0：初始化 DB + 插入 id=0 记录
    init_db()

    # 步骤 1：爬取 Bangumi
    if args.platform in ("bangumi", "both"):
        crawl_bangumi(max_pages=args.max_pages, dry_run=args.dry_run)

    # 步骤 2：爬取 B站
    bili_title = None
    if args.platform in ("bilibili", "both"):
        bili_title = crawl_bilibili(max_comments=args.max_comments, dry_run=args.dry_run)

    # 步骤 3：清洗 + 入库
    clean_and_import_all(dry_run=args.dry_run, bili_title=bili_title)

    # 步骤 4：汇总
    show_summary()

    print(f"\n{GREEN}{BOLD}✓ 验证完成！《{ANIME_NAME}》anime_id={ANIME_ID}{RESET}\n")


if __name__ == "__main__":
    main()
