# -*- coding: utf-8 -*-
"""
关键词提取模块

功能：
    1. 使用 TF-IDF 提取高权重关键词
    2. 使用词频统计（Counter）提取高频词
    3. 提供 get_wordcloud_data(anime_id) 接口
    4. 返回前100个高频词（已去停用词）

用法：
    python -m topic.keyword_extractor --anime_id 1
    python -m topic.keyword_extractor --anime_id 1 --top_n 50 --method both
"""

import os
import sys
import argparse
import logging
from collections import Counter

import jieba
import pandas as pd
from sqlalchemy import select
from sklearn.feature_extraction.text import TfidfVectorizer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.db.models import Anime, Comment
from backend.db.session import session_scope

STOPWORDS_PATH = os.path.join(PROJECT_ROOT, "data", "stopwords.txt")


def load_stopwords(filepath=None):
    """加载停用词表"""
    if filepath is None:
        filepath = STOPWORDS_PATH
    stopwords = set()
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                word = line.strip()
                if word:
                    stopwords.add(word)
    except FileNotFoundError:
        logger.warning("停用词文件不存在: %s", filepath)
    return stopwords


def get_comments_from_db(anime_id, db_path=None):
    """从数据库读取指定动漫的评论"""
    with session_scope(db_path=db_path) as session:
        anime_name = session.scalar(select(Anime.name).where(Anime.id == anime_id)) or "未知动漫"
        comments = list(session.scalars(
            select(Comment.content)
            .where(Comment.anime_id == anime_id, Comment.content.is_not(None))
            .order_by(Comment.id)
        ))

    logger.info("动漫: [%d] %s, 评论数: %d", anime_id, anime_name, len(comments))
    return comments, anime_name


def tokenize_and_filter(comments, stopwords=None):
    """
    分词并过滤停用词，返回每条评论的分词字符串列表

    Args:
        comments: 评论文本列表
        stopwords: 停用词集合

    Returns:
        list[str]: 每条评论的分词结果（空格分隔）
    """
    if stopwords is None:
        stopwords = set()

    result = []
    for text in comments:
        words = jieba.lcut(str(text))
        filtered = [
            w.strip() for w in words
            if w.strip()
            and len(w.strip()) > 1
            and w.strip() not in stopwords
            and not w.strip().isdigit()
        ]
        if filtered:
            result.append(" ".join(filtered))

    return result


def extract_keywords_tfidf(tokenized_docs, top_n=100):
    """
    使用TF-IDF提取关键词

    Args:
        tokenized_docs: 分词后的文档列表（空格分隔字符串）
        top_n: 返回前N个关键词

    Returns:
        list[dict]: [{"word": "剧情", "weight": 0.85}, ...]
    """
    if not tokenized_docs:
        return []

    vectorizer = TfidfVectorizer(
        max_features=5000,
        token_pattern=r"(?u)\b\w+\b"
    )
    tfidf_matrix = vectorizer.fit_transform(tokenized_docs)
    feature_names = vectorizer.get_feature_names_out()

    # 计算每个词的平均TF-IDF分数
    avg_tfidf = tfidf_matrix.mean(axis=0).A1
    word_scores = list(zip(feature_names, avg_tfidf))
    word_scores.sort(key=lambda x: x[1], reverse=True)

    results = [
        {"word": word, "weight": round(float(score), 6)}
        for word, score in word_scores[:top_n]
    ]

    return results


def extract_keywords_freq(tokenized_docs, top_n=100):
    """
    使用词频统计提取高频词

    Args:
        tokenized_docs: 分词后的文档列表（空格分隔字符串）
        top_n: 返回前N个高频词

    Returns:
        list[dict]: [{"word": "剧情", "count": 150}, ...]
    """
    if not tokenized_docs:
        return []

    counter = Counter()
    for doc in tokenized_docs:
        words = doc.split()
        counter.update(words)

    results = [
        {"word": word, "count": count}
        for word, count in counter.most_common(top_n)
    ]

    return results


def get_wordcloud_data(anime_id, top_n=100, db_path=None):
    """
    获取指定动漫的词云数据（词频统计）

    Args:
        anime_id: 动漫ID
        top_n: 返回前N个词
        db_path: 数据库路径

    Returns:
        list[dict]: [{"word": "剧情", "count": 150}, ...]
    """
    stopwords = load_stopwords()
    comments, anime_name = get_comments_from_db(anime_id, db_path)

    if not comments:
        logger.warning("动漫 [%d] 无评论数据", anime_id)
        return []

    tokenized = tokenize_and_filter(comments, stopwords)
    return extract_keywords_freq(tokenized, top_n)


def get_tfidf_keywords(anime_id, top_n=100, db_path=None):
    """
    获取指定动漫的TF-IDF关键词

    Args:
        anime_id: 动漫ID
        top_n: 返回前N个词
        db_path: 数据库路径

    Returns:
        list[dict]: [{"word": "剧情", "weight": 0.85}, ...]
    """
    stopwords = load_stopwords()
    comments, anime_name = get_comments_from_db(anime_id, db_path)

    if not comments:
        logger.warning("动漫 [%d] 无评论数据", anime_id)
        return []

    tokenized = tokenize_and_filter(comments, stopwords)
    return extract_keywords_tfidf(tokenized, top_n)


def main():
    parser = argparse.ArgumentParser(description="关键词提取")
    parser.add_argument("--anime_id", type=int, default=1, help="动漫ID")
    parser.add_argument("--top_n", type=int, default=30, help="返回关键词数量 (默认30)")
    parser.add_argument("--method", type=str, default="both", choices=["tfidf", "freq", "both"],
                        help="提取方法: tfidf, freq, both (默认both)")

    args = parser.parse_args()

    stopwords = load_stopwords()
    comments, anime_name = get_comments_from_db(args.anime_id)

    if not comments:
        logger.error("动漫 [%d] 无评论数据", args.anime_id)
        return

    tokenized = tokenize_and_filter(comments, stopwords)
    logger.info("分词完成: %d 篇有效文档", len(tokenized))

    if args.method in ("tfidf", "both"):
        tfidf_keywords = extract_keywords_tfidf(tokenized, args.top_n)
        print("\n" + "=" * 50)
        print("TF-IDF 关键词 Top %d (动漫: %s)" % (args.top_n, anime_name))
        print("=" * 50)
        for i, kw in enumerate(tfidf_keywords, 1):
            print("%3d. %-10s  %.6f" % (i, kw["word"], kw["weight"]))

    if args.method in ("freq", "both"):
        freq_keywords = extract_keywords_freq(tokenized, args.top_n)
        print("\n" + "=" * 50)
        print("词频统计 Top %d (动漫: %s)" % (args.top_n, anime_name))
        print("=" * 50)
        for i, kw in enumerate(freq_keywords, 1):
            print("%3d. %-10s  %d" % (i, kw["word"], kw["count"]))


if __name__ == "__main__":
    main()
