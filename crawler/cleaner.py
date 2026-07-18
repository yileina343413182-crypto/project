# -*- coding: utf-8 -*-
"""
评论数据清洗模块

功能：
    1. 读取 data/raw/ 目录下的CSV评论文件
    2. 执行数据清洗流程：
       去除HTML标签 → 去除表情符号和特殊字符 → 去除纯数字/纯符号评论
       → 去除过短评论(<5字) → 去重
    3. 使用jieba进行中文分词
    4. 去除停用词
    5. 清洗后保存到 data/processed/ 目录
    6. 同时将清洗后数据写入SQLite数据库

用法：
    # 清洗指定CSV文件
    python cleaner.py --input data/raw/bilibili_xxx.csv --platform bilibili --anime_name "进击的巨人"

    # 清洗raw目录下所有CSV文件
    python cleaner.py --all --anime_name "进击的巨人"

    # 清洗指定子目录下所有CSV（自动从CSV中提取番剧名）
    python cleaner.py --input_dir bangumi_top100 --platform bangumi

依赖：
    pip install pandas jieba
"""

import os
import re
import sys
import sqlite3
import argparse
import logging
from datetime import datetime

import pandas as pd
import jieba

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ===================== 路径配置 =====================

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
# 数据目录
RAW_DIR = os.path.join(PROJECT_ROOT, "data", "raw")
PROCESSED_DIR = os.path.join(PROJECT_ROOT, "data", "processed")
STOPWORDS_PATH = os.path.join(PROJECT_ROOT, "data", "stopwords.txt")
# SQLite数据库路径
DB_PATH = os.path.join(PROJECT_ROOT, "data", "anime_sentiment.db")


def load_stopwords(filepath=None):
    """
    加载停用词表。

    Args:
        filepath: 停用词文件路径，默认使用 data/stopwords.txt

    Returns:
        set: 停用词集合
    """
    if filepath is None:
        filepath = STOPWORDS_PATH

    stopwords = set()
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                word = line.strip()
                if word:
                    stopwords.add(word)
        logger.info("加载停用词 %d 个", len(stopwords))
    except FileNotFoundError:
        logger.warning("停用词文件不存在: %s，将使用空停用词表", filepath)
    except UnicodeDecodeError:
        logger.error("停用词文件编码错误，请确保文件为UTF-8编码")
    return stopwords


def remove_html_tags(text):
    """去除HTML标签"""
    return re.sub(r"<[^>]+>", "", text)


def remove_emojis_and_special_chars(text):
    """
    去除表情符号和特殊字符，保留中文、英文、数字和常见标点。
    """
    # 去除emoji表情
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # 笑脸表情
        "\U0001F300-\U0001F5FF"  # 符号和象形文字
        "\U0001F680-\U0001F6FF"  # 交通和地图
        "\U0001F1E0-\U0001F1FF"  # 旗帜
        "\U00002702-\U000027B0"  # 其他符号
        "\U000024C2-\U0001F251"
        "\U0001f926-\U0001f937"
        "\U00010000-\U0010ffff"
        "]+",
        flags=re.UNICODE
    )
    text = emoji_pattern.sub("", text)

    # 去除B站特殊表情标记 [xxx]
    text = re.sub(r"\[[\w\u4e00-\u9fff]+\]", "", text)

    # 保留中文、英文字母、数字和常见中英文标点
    text = re.sub(r"[^\u4e00-\u9fff\u3000-\u303fa-zA-Z0-9，。！？、；：\u201c\u201d\u2018\u2019（）【】\s]", "", text)

    return text.strip()


def is_valid_comment(text, min_length=5):
    """
    判断评论是否有效（非纯数字、非纯符号、长度>=指定值）。

    Args:
        text: 评论文本
        min_length: 最小字符长度，默认5

    Returns:
        bool: 是否为有效评论
    """
    if not text or len(text.strip()) < min_length:
        return False

    # 去除空白后判断
    clean_text = text.strip()

    # 纯数字
    if re.match(r"^\d+$", clean_text):
        return False

    # 纯符号（不含中英文字符）
    if not re.search(r"[\u4e00-\u9fffa-zA-Z]", clean_text):
        return False

    return True


def segment_text(text, stopwords=None):
    """
    使用jieba对文本进行分词，并去除停用词。

    Args:
        text: 待分词的文本
        stopwords: 停用词集合，None表示不去除停用词

    Returns:
        str: 以空格分隔的分词结果字符串
    """
    words = jieba.lcut(text)

    if stopwords:
        words = [w for w in words if w.strip() and w not in stopwords]
    else:
        words = [w for w in words if w.strip()]

    return " ".join(words)


def clean_dataframe(df, stopwords=None, platform="bilibili", min_length=5):
    """
    对评论DataFrame执行完整的清洗流程。

    Args:
        df: 包含评论数据的DataFrame
        stopwords: 停用词集合
        platform: 数据来源平台 ('bilibili' 或 'douban')
        min_length: 最小字符长度过滤阈值，默认5，弹幕可设为2

    Returns:
        pd.DataFrame: 清洗后的DataFrame
    """
    original_count = len(df)
    logger.info("开始清洗数据，原始数据量: %d 条", original_count)

    # 确定评论内容列名（B站和豆瓣的列名可能不同）
    content_col = "content"
    if content_col not in df.columns:
        logger.error("数据中未找到 '%s' 列，可用列: %s", content_col, list(df.columns))
        return pd.DataFrame()

    # 删除评论内容为空的行
    df = df.dropna(subset=[content_col]).copy()
    df[content_col] = df[content_col].astype(str)
    logger.info("去除空值后: %d 条", len(df))

    # 第一步：去除HTML标签
    df["clean_content"] = df[content_col].apply(remove_html_tags)

    # 第二步：去除表情符号和特殊字符
    df["clean_content"] = df["clean_content"].apply(remove_emojis_and_special_chars)

    # 第三步：去除无效评论（纯数字/纯符号/过短）
    mask = df["clean_content"].apply(lambda x: is_valid_comment(x, min_length=min_length))
    df = df[mask].copy()
    logger.info("去除无效评论后: %d 条", len(df))

    # 第四步：去重（基于清洗后的内容）
    df = df.drop_duplicates(subset=["clean_content"]).copy()
    logger.info("去重后: %d 条", len(df))

    # 第五步：jieba分词 + 去除停用词
    logger.info("正在进行jieba分词...")
    df["segmented"] = df["clean_content"].apply(
        lambda x: segment_text(x, stopwords)
    )

    # 去除分词后为空的行
    df = df[df["segmented"].str.strip().astype(bool)].copy()

    removed_count = original_count - len(df)
    logger.info("清洗完成: 原始 %d 条 → 剩余 %d 条 (移除 %d 条, %.1f%%)",
                original_count, len(df), removed_count,
                removed_count / max(original_count, 1) * 100)

    df.attrs["removed_count"] = removed_count
    df.attrs["original_count"] = original_count
    return df


def init_database(db_path=None):
    """
    初始化SQLite数据库，创建所需的表（如果不存在）。

    Args:
        db_path: 数据库文件路径，默认使用 data/anime_sentiment.db

    Returns:
        sqlite3.Connection: 数据库连接对象
    """
    if db_path is None:
        db_path = DB_PATH

    # 确保数据库所在目录存在
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 创建动漫信息表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS anime (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            platform TEXT NOT NULL,
            url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 创建评论表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            anime_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            clean_content TEXT,
            publish_time TIMESTAMP,
            likes INTEGER DEFAULT 0,
            platform TEXT NOT NULL,
            sentiment_label TEXT,
            sentiment_score REAL,
            model_used TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (anime_id) REFERENCES anime(id)
        )
    """)

    # 创建主题表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            anime_id INTEGER NOT NULL,
            topic_id INTEGER NOT NULL,
            keywords TEXT NOT NULL,
            weight REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (anime_id) REFERENCES anime(id)
        )
    """)

    conn.commit()
    logger.info("数据库初始化完成: %s", db_path)
    return conn


def get_or_create_anime(conn, anime_name, platform, url=None):
    """
    获取或创建动漫记录，返回anime_id。

    Args:
        conn: 数据库连接
        anime_name: 动漫名称
        platform: 来源平台
        url: 相关URL

    Returns:
        int: 动漫记录的ID
    """
    cursor = conn.cursor()

    # 查找是否已存在
    cursor.execute(
        "SELECT id FROM anime WHERE name = ? AND platform = ?",
        (anime_name, platform)
    )
    row = cursor.fetchone()

    if row:
        return row[0]

    # 不存在则新建
    cursor.execute(
        "INSERT INTO anime (name, platform, url) VALUES (?, ?, ?)",
        (anime_name, platform, url)
    )
    conn.commit()
    anime_id = cursor.lastrowid
    logger.info("创建动漫记录: id=%d, name=%s, platform=%s", anime_id, anime_name, platform)
    return anime_id


def save_to_database(conn, df, anime_id, platform):
    """
    将清洗后的评论数据写入SQLite数据库。

    Args:
        conn: 数据库连接
        df: 清洗后的DataFrame
        anime_id: 动漫记录ID
        platform: 来源平台

    Returns:
        int: 写入的记录数
    """
    cursor = conn.cursor()
    count = 0

    # 根据平台确定字段映射
    for _, row in df.iterrows():
        content = row.get("content", "")
        clean_content = row.get("clean_content", "")

        # 获取发布时间
        if platform == "bilibili":
            publish_time = row.get("ctime", None) or row.get("send_time", None)
            likes = int(row.get("like", 0)) if pd.notna(row.get("like", 0)) else 0
        elif platform == "douban":
            publish_time = row.get("time", None)
            votes_val = row.get("votes", 0)
            likes = int(votes_val) if pd.notna(votes_val) else 0
        elif platform == "bangumi":
            publish_time = row.get("time", None)
            likes = 0  # Bangumi吐槽箱无点赞数
        else:
            publish_time = None
            likes = 0

        try:
            cursor.execute(
                """INSERT INTO comments (anime_id, content, clean_content, publish_time, likes, platform)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (anime_id, content, clean_content, publish_time, likes, platform)
            )
            count += 1
        except sqlite3.Error as e:
            logger.debug("插入评论失败: %s", e)
            continue

    conn.commit()
    logger.info("已写入数据库 %d 条评论记录", count)
    return count


def process_file(input_path, platform, anime_name=None, stopwords=None, db_conn=None, output_dir=None):
    """
    处理单个CSV文件：清洗 → 保存CSV → 写入数据库。

    Args:
        input_path: 输入CSV文件路径
        platform: 数据来源平台 ('bilibili', 'douban', 'bangumi')
        anime_name: 动漫名称，为None时自动从CSV的anime_title列提取
        stopwords: 停用词集合
        db_conn: 数据库连接（可选）
        output_dir: 输出目录，默认使用 data/processed/

    Returns:
        pd.DataFrame: 清洗后的DataFrame
    """
    logger.info("处理文件: %s", input_path)

    # 读取CSV文件
    try:
        df = pd.read_csv(input_path, encoding="utf-8-sig")
    except UnicodeDecodeError:
        df = pd.read_csv(input_path, encoding="utf-8")
    except FileNotFoundError:
        logger.error("文件不存在: %s", input_path)
        return pd.DataFrame()

    if df.empty:
        logger.warning("文件为空: %s", input_path)
        return pd.DataFrame()

    # 自动提取番剧名
    if anime_name is None and "anime_title" in df.columns:
        anime_name = str(df["anime_title"].iloc[0])
    if anime_name is None:
        anime_name = os.path.splitext(os.path.basename(input_path))[0]

    # 根据文件名判断是否为弹幕文件，弹幕使用更短的最小长度
    basename = os.path.basename(input_path)
    is_danmaku = "_dm_" in basename or basename.startswith("bilibili_dm")
    ml = 2 if is_danmaku else 5

    # 执行清洗
    df_clean = clean_dataframe(df, stopwords=stopwords, platform=platform, min_length=ml)

    if df_clean.empty:
        logger.warning("清洗后无有效数据")
        return df_clean

    # 保存到processed目录
    save_dir = output_dir if output_dir else PROCESSED_DIR
    os.makedirs(save_dir, exist_ok=True)
    filename = os.path.basename(input_path)
    output_filename = f"cleaned_{filename}"
    output_path = os.path.join(save_dir, output_filename)

    try:
        df_clean.to_csv(output_path, index=False, encoding="utf-8-sig")
        logger.info("清洗后数据已保存到: %s", output_path)
    except (OSError, PermissionError) as e:
        logger.error("保存清洗结果失败: %s", e)

    # 写入数据库
    if db_conn is not None:
        anime_id = get_or_create_anime(db_conn, anime_name, platform)
        save_to_database(db_conn, df_clean, anime_id, platform)

    return df_clean


# ===================== 命令行入口 =====================

def main():
    """命令行入口函数"""
    parser = argparse.ArgumentParser(description="评论数据清洗工具")
    parser.add_argument("--input", type=str, help="输入CSV文件路径")
    parser.add_argument("--input_dir", type=str, default=None,
                        help="输入目录名（data/raw/下的子目录，如 bangumi_top100）")
    parser.add_argument("--all", action="store_true", help="清洗raw目录下所有CSV文件")
    parser.add_argument("--platform", type=str, default="bilibili",
                        choices=["bilibili", "douban", "bangumi"],
                        help="数据来源平台（默认bilibili）")
    parser.add_argument("--anime_name", type=str, default=None,
                        help="动漫名称（用于数据库记录，不指定则自动从CSV提取）")
    parser.add_argument("--stopwords", type=str, default=None,
                        help="停用词文件路径（默认使用 data/stopwords.txt）")
    parser.add_argument("--db_path", type=str, default=None,
                        help="SQLite数据库路径（默认 data/anime_sentiment.db）")
    parser.add_argument("--no_db", action="store_true",
                        help="不写入数据库，仅保存CSV")
    args = parser.parse_args()

    # 加载停用词
    stopwords = load_stopwords(args.stopwords)

    # 初始化数据库连接
    db_conn = None
    if not args.no_db:
        db_conn = init_database(args.db_path)

    try:
        if args.input_dir:
            # 清洗指定子目录下所有CSV文件
            target_dir = os.path.join(RAW_DIR, args.input_dir)
            if not os.path.isdir(target_dir):
                logger.error("目录不存在: %s", target_dir)
                return

            csv_files = sorted([f for f in os.listdir(target_dir)
                                if f.endswith(".csv") and not f.startswith("_")])
            if not csv_files:
                logger.warning("目录下没有可清洗的CSV文件")
                return

            output_dir = os.path.join(PROCESSED_DIR, args.input_dir)
            logger.info("找到 %d 个CSV文件待清洗，输出目录: %s", len(csv_files), output_dir)

            total_original = 0
            total_clean = 0
            for i, csv_file in enumerate(csv_files, 1):
                logger.info("[%d/%d] %s", i, len(csv_files), csv_file)
                input_path = os.path.join(target_dir, csv_file)
                df_clean = process_file(
                    input_path, args.platform, args.anime_name,
                    stopwords=stopwords, db_conn=db_conn, output_dir=output_dir
                )
                if df_clean is not None and not df_clean.empty:
                    total_clean += len(df_clean)
                    total_original += df_clean.attrs.get("original_count", len(df_clean))

            logger.info("========================================")
            logger.info("批量清洗完成！共处理 %d 个文件，清洗后共 %d 条有效评论",
                        len(csv_files), total_clean)
            logger.info("========================================")

        elif args.all:
            # 清洗raw目录下所有CSV文件
            if not os.path.exists(RAW_DIR):
                logger.error("原始数据目录不存在: %s", RAW_DIR)
                return

            csv_files = [f for f in os.listdir(RAW_DIR) if f.endswith(".csv")]
            if not csv_files:
                logger.warning("raw目录下没有CSV文件")
                return

            logger.info("找到 %d 个CSV文件待清洗", len(csv_files))
            for csv_file in csv_files:
                input_path = os.path.join(RAW_DIR, csv_file)
                # 根据文件名自动判断平台
                if csv_file.startswith("bilibili"):
                    platform = "bilibili"
                elif csv_file.startswith("douban"):
                    platform = "douban"
                elif csv_file.startswith("bangumi"):
                    platform = "bangumi"
                else:
                    platform = args.platform

                process_file(input_path, platform, args.anime_name,
                             stopwords=stopwords, db_conn=db_conn)

        elif args.input:
            # 清洗指定文件
            process_file(args.input, args.platform, args.anime_name,
                         stopwords=stopwords, db_conn=db_conn)

        else:
            print("请指定 --input, --input_dir 或 --all 参数")
            print("示例:")
            print('  python cleaner.py --input data/raw/bilibili_xxx.csv --anime_name "进击的巨人"')
            print('  python cleaner.py --all --anime_name "进击的巨人"')
            print('  python cleaner.py --input_dir bangumi_top100 --platform bangumi')
            parser.print_help()

    finally:
        # 确保数据库连接关闭
        if db_conn:
            db_conn.close()
            logger.info("数据库连接已关闭")


if __name__ == "__main__":
    main()
