# -*- coding: utf-8 -*-
"""
LDA 主题建模模块

功能：
    1. 从数据库或CSV读取指定动漫的所有评论（已分词版本）
    2. 使用gensim实现LDA主题建模
    3. 为每个主题提取权重最高的10个关键词
    4. 提供困惑度(perplexity)和一致性(coherence)评估
    5. 结果写入数据库 topics 表

用法：
    python -m topic.lda_model --anime_id 1 --num_topics 8
    python -m topic.lda_model --anime_id 1 --find_best --min_topics 3 --max_topics 15
"""

import os
import sys
import sqlite3
import argparse
import logging
import json

import jieba
import pandas as pd
from gensim import corpora, models
from gensim.models import CoherenceModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "anime_sentiment.db")
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
    """
    从数据库读取指定动漫的所有评论

    Args:
        anime_id: 动漫ID
        db_path: 数据库路径

    Returns:
        list[str]: 评论内容列表
    """
    if db_path is None:
        db_path = DB_PATH

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 获取动漫名称
    cur.execute("SELECT name FROM anime WHERE id = ?", (anime_id,))
    row = cur.fetchone()
    anime_name = row[0] if row else "未知动漫"
    logger.info("动漫: [%d] %s", anime_id, anime_name)

    # 读取评论（优先用原始content，因为clean_content可能有问题）
    cur.execute("SELECT content FROM comments WHERE anime_id = ?", (anime_id,))
    comments = [r[0] for r in cur.fetchall() if r[0]]
    conn.close()

    logger.info("读取到 %d 条评论", len(comments))
    return comments, anime_name


def tokenize_comments(comments, stopwords=None):
    """
    对评论列表进行分词和过滤

    Args:
        comments: 评论文本列表
        stopwords: 停用词集合

    Returns:
        list[list[str]]: 分词后的文档列表
    """
    if stopwords is None:
        stopwords = set()

    docs = []
    for text in comments:
        words = jieba.lcut(str(text))
        # 过滤停用词、单字、纯数字、纯英文短词
        filtered = [
            w.strip() for w in words
            if w.strip()
            and len(w.strip()) > 1
            and w.strip() not in stopwords
            and not w.strip().isdigit()
        ]
        if filtered:
            docs.append(filtered)

    logger.info("分词完成: %d 篇有效文档", len(docs))
    return docs


class LDATopicModel:
    """LDA主题模型封装"""

    def __init__(self, num_topics=8, iterations=100, passes=10, random_state=42):
        """
        Args:
            num_topics: 主题数量
            iterations: 每次pass的最大迭代次数
            passes: 遍历语料库的次数
            random_state: 随机种子
        """
        self.num_topics = num_topics
        self.iterations = iterations
        self.passes = passes
        self.random_state = random_state

        self.dictionary = None
        self.corpus = None
        self.model = None
        self.docs = None  # 保存分词文档，用于一致性评估

    def fit(self, docs):
        """
        训练LDA模型

        Args:
            docs: 分词后的文档列表 list[list[str]]
        """
        self.docs = docs

        # 构建词典
        self.dictionary = corpora.Dictionary(docs)
        # 过滤极端词频：出现少于3次或超过50%文档的词
        self.dictionary.filter_extremes(no_below=3, no_above=0.5)
        logger.info("词典大小: %d", len(self.dictionary))

        # 构建词袋语料
        self.corpus = [self.dictionary.doc2bow(doc) for doc in docs]

        # 训练LDA
        logger.info("训练LDA: num_topics=%d, passes=%d, iterations=%d",
                     self.num_topics, self.passes, self.iterations)
        self.model = models.LdaModel(
            corpus=self.corpus,
            id2word=self.dictionary,
            num_topics=self.num_topics,
            iterations=self.iterations,
            passes=self.passes,
            random_state=self.random_state,
            alpha="auto",
            eta="auto"
        )
        logger.info("LDA模型训练完成")

    def get_topics(self, top_n=10):
        """
        获取所有主题及其关键词

        Args:
            top_n: 每个主题返回的关键词数量

        Returns:
            list[dict]: [{topic_id, keywords: [{word, weight}, ...]}, ...]
        """
        if self.model is None:
            raise RuntimeError("模型未训练")

        topics = []
        for topic_id in range(self.num_topics):
            word_weights = self.model.show_topic(topic_id, topn=top_n)
            keywords = [{"word": w, "weight": round(float(p), 6)} for w, p in word_weights]
            topics.append({
                "topic_id": topic_id,
                "keywords": keywords
            })

        return topics

    def get_perplexity(self):
        """计算困惑度（越低越好）"""
        if self.model is None or self.corpus is None:
            return None
        return self.model.log_perplexity(self.corpus)

    def get_coherence(self, coherence_type="c_v"):
        """
        计算主题一致性（越高越好）

        Args:
            coherence_type: 一致性计算方式，可选 c_v, u_mass, c_npmi

        Returns:
            float: 一致性分数
        """
        if self.model is None or self.docs is None:
            return None

        cm = CoherenceModel(
            model=self.model,
            texts=self.docs,
            dictionary=self.dictionary,
            coherence=coherence_type
        )
        return cm.get_coherence()

    def print_topics(self, top_n=10):
        """打印所有主题"""
        topics = self.get_topics(top_n)
        print("\n" + "=" * 60)
        print("LDA主题建模结果 (共 %d 个主题)" % self.num_topics)
        print("=" * 60)

        for t in topics:
            kw_str = "  ".join("%s(%.4f)" % (k["word"], k["weight"]) for k in t["keywords"])
            print("主题 %d: %s" % (t["topic_id"], kw_str))

        perplexity = self.get_perplexity()
        if perplexity is not None:
            print("\n困惑度(log perplexity): %.4f" % perplexity)
        print("=" * 60)


def save_topics_to_db(anime_id, topics, db_path=None):
    """
    将主题结果写入数据库 topics 表

    Args:
        anime_id: 动漫ID
        topics: get_topics()返回的主题列表
        db_path: 数据库路径
    """
    if db_path is None:
        db_path = DB_PATH

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 删除该动漫的旧主题数据
    cur.execute("DELETE FROM topics WHERE anime_id = ?", (anime_id,))

    # 插入新主题
    for t in topics:
        keywords_json = json.dumps(t["keywords"], ensure_ascii=False)
        # weight 存储该主题的总权重（所有关键词权重之和）
        total_weight = sum(k["weight"] for k in t["keywords"])
        cur.execute(
            "INSERT INTO topics (anime_id, topic_id, keywords, weight) VALUES (?, ?, ?, ?)",
            (anime_id, t["topic_id"], keywords_json, round(total_weight, 6))
        )

    conn.commit()
    conn.close()
    logger.info("已将 %d 个主题写入数据库 (anime_id=%d)", len(topics), anime_id)


def find_best_num_topics(docs, min_topics=3, max_topics=15, step=1):
    """
    通过一致性评估找出最优主题数

    Args:
        docs: 分词后的文档列表
        min_topics: 最小主题数
        max_topics: 最大主题数
        step: 步长

    Returns:
        tuple: (best_k, results) results为 [{k, coherence, perplexity}, ...]
    """
    results = []
    logger.info("搜索最优主题数: range(%d, %d, %d)", min_topics, max_topics + 1, step)

    for k in range(min_topics, max_topics + 1, step):
        lda = LDATopicModel(num_topics=k, passes=10, iterations=100)
        lda.fit(docs)

        coherence = lda.get_coherence("c_v")
        perplexity = lda.get_perplexity()
        results.append({"k": k, "coherence": coherence, "perplexity": perplexity})
        logger.info("k=%d  coherence=%.4f  perplexity=%.4f", k, coherence, perplexity)

    # 选择一致性最高的k
    best = max(results, key=lambda x: x["coherence"])
    logger.info("最优主题数: k=%d (coherence=%.4f)", best["k"], best["coherence"])

    print("\n主题数搜索结果:")
    print("%-6s %-12s %-12s" % ("K", "Coherence", "Perplexity"))
    print("-" * 30)
    for r in results:
        marker = " ← best" if r["k"] == best["k"] else ""
        print("%-6d %-12.4f %-12.4f%s" % (r["k"], r["coherence"], r["perplexity"], marker))

    return best["k"], results


def run_lda_for_anime(anime_id, num_topics=8, save_db=True, db_path=None):
    """
    对指定动漫执行完整的LDA流程

    Args:
        anime_id: 动漫ID
        num_topics: 主题数
        save_db: 是否保存到数据库
        db_path: 数据库路径

    Returns:
        list[dict]: 主题列表
    """
    stopwords = load_stopwords()
    comments, anime_name = get_comments_from_db(anime_id, db_path)

    if not comments:
        logger.warning("动漫 [%d] 无评论数据", anime_id)
        return []

    docs = tokenize_comments(comments, stopwords)
    if len(docs) < 5:
        logger.warning("有效文档太少 (%d)，跳过LDA", len(docs))
        return []

    lda = LDATopicModel(num_topics=num_topics)
    lda.fit(docs)
    lda.print_topics()

    topics = lda.get_topics()

    if save_db:
        save_topics_to_db(anime_id, topics, db_path)

    return topics


def main():
    parser = argparse.ArgumentParser(description="LDA主题建模")
    parser.add_argument("--anime_id", type=int, default=1, help="动漫ID")
    parser.add_argument("--num_topics", type=int, default=8, help="主题数量 (默认8)")
    parser.add_argument("--passes", type=int, default=10, help="遍历次数 (默认10)")
    parser.add_argument("--no_save", action="store_true", help="不保存到数据库")
    parser.add_argument("--find_best", action="store_true", help="搜索最优主题数")
    parser.add_argument("--min_topics", type=int, default=3, help="搜索最小主题数 (默认3)")
    parser.add_argument("--max_topics", type=int, default=15, help="搜索最大主题数 (默认15)")

    args = parser.parse_args()

    stopwords = load_stopwords()
    comments, anime_name = get_comments_from_db(args.anime_id)

    if not comments:
        logger.error("动漫 [%d] 无评论数据", args.anime_id)
        return

    docs = tokenize_comments(comments, stopwords)
    if len(docs) < 5:
        logger.error("有效文档太少 (%d)，无法进行LDA", len(docs))
        return

    if args.find_best:
        # 搜索最优主题数
        best_k, _ = find_best_num_topics(docs, args.min_topics, args.max_topics)
        logger.info("使用最优主题数 k=%d 重新训练", best_k)
        args.num_topics = best_k

    # 训练并输出结果
    lda = LDATopicModel(num_topics=args.num_topics, passes=args.passes)
    lda.fit(docs)
    lda.print_topics()

    # 计算一致性
    coherence = lda.get_coherence("c_v")
    if coherence is not None:
        print("一致性(c_v): %.4f" % coherence)

    topics = lda.get_topics()

    if not args.no_save:
        save_topics_to_db(args.anime_id, topics)


if __name__ == "__main__":
    main()
