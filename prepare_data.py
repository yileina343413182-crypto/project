# -*- coding: utf-8 -*-
"""
完整数据处理流水线

流程：
    爬取 B站评论 → 数据清洗入库 → 情感批量预测 → LDA 主题挖掘

用法：
    # 爬取单部动漫（B站）
    python prepare_data.py --anime "进击的巨人" --platform bilibili --max_pages 30

    # 爬取并指定使用 BERT 模型预测
    python prepare_data.py --anime "鬼灭之刃" --model bert

    # 只运行情感预测和主题挖掘（跳过爬虫）
    python prepare_data.py --anime_id 1 --skip-crawl

    # 对所有动漫重新计算主题
    python prepare_data.py --topics-only

    # 查看数据库中已有的动漫列表
    python prepare_data.py --list
"""

import os
import sys
import logging
import argparse

from sqlalchemy import case, func, select

# Windows 终端 UTF-8 输出
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.db.models import Anime, Comment
from backend.db.session import session_scope

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"


def step(msg):
    print(f"\n{CYAN}{BOLD}{'-'*55}{RESET}")
    print(f"{CYAN}>> {msg}{RESET}")
    print(f"{CYAN}{'-'*55}{RESET}")


def ok(msg):
    print(f"{GREEN}  [OK] {msg}{RESET}")


def warn(msg):
    print(f"{YELLOW}  [!!] {msg}{RESET}")


def err(msg):
    print(f"{RED}  [ERR] {msg}{RESET}")


# ───────────────────────── 工具函数 ─────────────────────────

def ensure_db():
    """确保数据库存在并建表"""
    from crawler.cleaner import init_database
    session = init_database()
    session.close()


def get_anime_id_by_name(name):
    """根据动漫名称查询 ID"""
    with session_scope() as session:
        return session.scalar(select(Anime.id).where(Anime.name == name).order_by(Anime.id))


def list_anime():
    """列出数据库中所有动漫"""
    statement = (
        select(
            Anime.id,
            Anime.name,
            Anime.platform,
            func.count(Comment.id),
            func.coalesce(func.sum(case((Comment.sentiment_label.is_not(None), 1), else_=0)), 0),
        )
        .outerjoin(Comment, Comment.anime_id == Anime.id)
        .group_by(Anime.id, Anime.name, Anime.platform)
        .order_by(Anime.id)
    )
    with session_scope() as session:
        rows = session.execute(statement).all()

    if not rows:
        warn("数据库中暂无动漫数据")
        return

    print(f"\n{'ID':<6}{'名称':<20}{'平台':<12}{'评论数':<10}{'已标注':<10}")
    print("─" * 58)
    for r in rows:
        print(f"{r[0]:<6}{r[1]:<20}{r[2]:<12}{r[3]:<10}{r[4]:<10}")


# ───────────────────────── 步骤1：爬取数据 ─────────────────────────

def crawl_bilibili(anime_name, max_pages=30, sessdata=""):
    """调用 B站爬虫爬取评论，保存到 data/raw/"""
    step(f"[1/4] 爬取 B站弹幕与评论: 《{anime_name}》")
    try:
        from crawler.bilibili_crawler import BiliSession, search_anime, crawl_anime
        import os

        session = BiliSession()
        out_dir = os.path.join(PROJECT_ROOT, "data", "raw")

        # 搜索动漫条目
        logger.info("搜索动漫: %s", anime_name)
        results = search_anime(session, anime_name)
        if not results:
            err("未搜索到相关动漫，请检查名称或网络连接")
            return None

        # 取第一个结果
        anime_info = results[0]
        season_id = anime_info.get("season_id")
        title = anime_info.get("title", anime_name)
        logger.info("命中: %s (season_id=%s)", title, season_id)

        if not season_id:
            err("未能获取 season_id，无法爬取")
            return None

        # 爬取弹幕与评论（max_pages 近似转为 max_comments）
        stats = crawl_anime(session, season_id, title,
                            max_episodes=50,
                            max_comments=max_pages * 20,
                            output_dir=out_dir)
        if stats and (stats["danmaku_count"] + stats["comment_count"]) > 0:
            ok(f"爬取完成：{stats['episodes']} 集，"
               f"弹幕 {stats['danmaku_count']} 条，评论 {stats['comment_count']} 条")
            return out_dir
        else:
            err("爬取失败，未获得有效数据")
            return None

    except ImportError as e:
        err(f"导入爬虫模块失败: {e}")
        return None
    except Exception as e:
        err(f"爬取过程出错: {e}")
        logger.exception(e)
        return None


# ───────────────────────── 步骤2：清洗数据 ─────────────────────────

def clean_and_import(raw_csv_path, anime_name, platform="bilibili"):
    """清洗CSV并导入数据库，返回 anime_id"""
    step(f"[2/4] 清洗数据并导入数据库")
    try:
        from crawler.cleaner import (
            load_stopwords, clean_dataframe, init_database, get_or_create_anime,
            save_to_database
        )

        import pandas as pd
        df = pd.read_csv(raw_csv_path, encoding="utf-8-sig")
        logger.info("读取原始数据 %d 条", len(df))

        stopwords = load_stopwords()
        df_clean = clean_dataframe(df, stopwords, platform=platform)
        logger.info("清洗后剩余 %d 条", len(df_clean))

        conn = init_database()
        anime_id = get_or_create_anime(conn, anime_name, platform)
        inserted = save_to_database(conn, df_clean, anime_id, platform)
        conn.close()

        ok(f"导入完成: anime_id={anime_id}，新增 {inserted} 条评论")
        return anime_id

    except Exception as e:
        err(f"清洗/导入失败: {e}")
        logger.exception(e)
        return None


# ───────────────────────── 步骤3：情感预测 ─────────────────────────

def run_sentiment_predict(anime_id, model="textcnn", overwrite=False):
    """批量情感预测"""
    step(f"[3/4] 情感批量预测 (model={model}, anime_id={anime_id})")
    try:
        from batch_predict import load_model, fetch_comments, update_predictions

        clf = load_model(model)
        if clf is None:
            warn(f"模型 {model} 加载失败，跳过情感预测")
            return False

        comments = fetch_comments(anime_id=anime_id, overwrite=overwrite)
        if not comments:
            ok("没有待预测的评论（可能已全部标注）")
            return True

        logger.info("待预测评论: %d 条", len(comments))
        results = []
        batch_size = 64
        for i in range(0, len(comments), batch_size):
            batch = comments[i:i + batch_size]
            texts = [comment[1] for comment in batch]
            preds = clf.predict(texts)
            for comment, prediction in zip(batch, preds):
                results.append((
                    prediction["label"],
                    prediction["confidence"],
                    model,
                    comment[0],
                ))
            logger.info("进度: %d/%d", min(i + batch_size, len(comments)), len(comments))

        update_predictions(updates=results)
        ok(f"情感预测完成，共预测 {len(results)} 条")
        return True

    except Exception as e:
        err(f"情感预测失败: {e}")
        logger.exception(e)
        return False


# ───────────────────────── 步骤4：LDA 主题挖掘 ─────────────────────────

def run_lda_topics(anime_id, num_topics=8):
    """执行 LDA 主题挖掘"""
    step(f"[4/4] LDA 主题挖掘 (anime_id={anime_id}, num_topics={num_topics})")
    try:
        from topic.lda_model import run_lda_for_anime
        topics = run_lda_for_anime(anime_id, num_topics=num_topics, save_db=True)
        if topics:
            ok(f"主题挖掘完成，共 {len(topics)} 个主题")
        else:
            warn("主题挖掘未返回结果（评论数可能不足）")
        return True
    except Exception as e:
        err(f"LDA 主题挖掘失败: {e}")
        logger.exception(e)
        return False


# ───────────────────────── 主入口 ─────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="动漫评论数据采集与处理流水线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 完整流程（爬取 → 清洗 → 预测 → 主题）
  python prepare_data.py --anime "进击的巨人" --platform bilibili --max_pages 30

  # 使用 BERT 模型预测
  python prepare_data.py --anime "鬼灭之刃" --model bert

  # 跳过爬虫，只对已有数据做预测和主题挖掘
  python prepare_data.py --anime_id 1 --skip-crawl

  # 仅重新计算全部动漫的主题
  python prepare_data.py --topics-only

  # 查看数据库中已有动漫
  python prepare_data.py --list
        """
    )
    parser.add_argument("--anime", type=str, help="动漫名称（用于搜索）")
    parser.add_argument("--anime_id", type=int, help="动漫ID（跳过爬虫时使用）")
    parser.add_argument("--platform", default="bilibili", choices=["bilibili", "bangumi"],
                        help="爬取平台 (默认 bilibili)")
    parser.add_argument("--max_pages", type=int, default=30,
                        help="最大爬取页数 (默认 30，每页约20条)")
    parser.add_argument("--model", default="textcnn", choices=["textcnn", "bert"],
                        help="情感分析模型 (默认 textcnn)")
    parser.add_argument("--num_topics", type=int, default=8,
                        help="LDA 主题数量 (默认 8)")
    parser.add_argument("--overwrite", action="store_true",
                        help="覆盖已有情感标注")
    parser.add_argument("--skip-crawl", action="store_true",
                        help="跳过爬虫，只运行预测和主题挖掘")
    parser.add_argument("--topics-only", action="store_true",
                        help="只重新计算全部动漫的主题")
    parser.add_argument("--list", action="store_true",
                        help="列出数据库中的动漫并退出")
    args = parser.parse_args()

    # 初始化数据库
    ensure_db()

    # --list
    if args.list:
        list_anime()
        return

    # --topics-only
    if args.topics_only:
        with session_scope() as session:
            anime_ids = list(session.scalars(select(Anime.id).order_by(Anime.id)))
        if not anime_ids:
            warn("数据库中没有动漫数据")
            return
        for aid in anime_ids:
            run_lda_topics(aid, num_topics=args.num_topics)
        return

    # --skip-crawl 需要提供 anime_id
    if args.skip_crawl:
        if args.anime_id is None and args.anime is None:
            err("使用 --skip-crawl 时请提供 --anime_id 或 --anime")
            sys.exit(1)
        anime_id = args.anime_id
        if anime_id is None and args.anime:
            anime_id = get_anime_id_by_name(args.anime)
            if anime_id is None:
                err(f"数据库中未找到《{args.anime}》，请先运行爬虫或指定 --anime_id")
                sys.exit(1)
        run_sentiment_predict(anime_id, model=args.model, overwrite=args.overwrite)
        run_lda_topics(anime_id, num_topics=args.num_topics)
        print(f"\n{GREEN}{BOLD}✓ 处理完成！anime_id={anime_id}{RESET}\n")
        return

    # 完整流程
    if not args.anime:
        err("请提供动漫名称: --anime \"动漫名称\"")
        parser.print_help()
        sys.exit(1)

    print(f"\n{BOLD}{CYAN}开始处理: 《{args.anime}》{RESET}")
    print(f"平台: {args.platform} | 最大页数: {args.max_pages} | 模型: {args.model}\n")

    # Step 1: 爬取
    raw_csv = crawl_bilibili(args.anime, max_pages=args.max_pages)
    if raw_csv is None:
        err("爬取失败，流程中止")
        sys.exit(1)

    # Step 2: 清洗入库
    anime_id = clean_and_import(raw_csv, args.anime, platform=args.platform)
    if anime_id is None:
        err("数据入库失败，流程中止")
        sys.exit(1)

    # Step 3: 情感预测
    run_sentiment_predict(anime_id, model=args.model, overwrite=args.overwrite)

    # Step 4: LDA 主题
    run_lda_topics(anime_id, num_topics=args.num_topics)

    print(f"""
{GREEN}{BOLD}{'='*55}
  处理完成！
  动漫: 《{args.anime}》  anime_id = {anime_id}
  平台: {args.platform}
{'='*55}{RESET}

  现在可以启动系统查看分析结果:
    python run.py

  或者在前端查看:
    cd frontend && npm run dev
""")


if __name__ == "__main__":
    main()
