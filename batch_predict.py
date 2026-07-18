# -*- coding: utf-8 -*-
"""
批量情感预测回写脚本

功能：
    加载已训练的模型（TextCNN 或 BERT），对数据库中 sentiment_label 为空的评论
    执行批量预测，并将结果（标签、置信度、模型名）回写到 comments 表。

用法：
    # 使用 TextCNN（默认，速度快）
    python batch_predict.py

    # 使用 BERT（精度略高，速度较慢）
    python batch_predict.py --model bert

    # 只预测指定动漫
    python batch_predict.py --anime_id 218

    # 强制重新预测（覆盖已有标签）
    python batch_predict.py --overwrite
"""

import os
import sys
import sqlite3
import argparse
import logging
import time

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(PROJECT_ROOT, "data", "anime_sentiment.db")
TEXTCNN_MODEL_DIR = os.path.join(PROJECT_ROOT, "models", "saved", "textcnn")
BERT_MODEL_DIR = os.path.join(PROJECT_ROOT, "models", "saved", "bert")


def load_model(model_name):
    """加载指定的情感分类模型"""
    if model_name == "textcnn":
        from models.textcnn_classifier import TextCNNClassifier
        classifier = TextCNNClassifier()
        classifier.load(TEXTCNN_MODEL_DIR)
    elif model_name == "bert":
        from models.bert_classifier import BertClassifier
        classifier = BertClassifier()
        classifier.load(BERT_MODEL_DIR)
    else:
        raise ValueError(f"不支持的模型: {model_name}")
    return classifier


def fetch_comments(db_path, anime_id=None, overwrite=False):
    """
    从数据库读取待预测的评论

    Returns:
        list[tuple]: [(id, content), ...]
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    sql = "SELECT id, content FROM comments WHERE content IS NOT NULL AND content != ''"
    params = []

    if not overwrite:
        sql += " AND sentiment_label IS NULL"

    if anime_id is not None:
        sql += " AND anime_id = ?"
        params.append(anime_id)

    sql += " ORDER BY id"
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return rows


def update_predictions(db_path, updates):
    """
    批量回写预测结果

    Args:
        db_path: 数据库路径
        updates: [(sentiment_label, sentiment_score, model_used, comment_id), ...]
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executemany(
        "UPDATE comments SET sentiment_label = ?, sentiment_score = ?, model_used = ? WHERE id = ?",
        updates
    )
    conn.commit()
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="批量情感预测回写")
    parser.add_argument("--model", type=str, default="textcnn",
                        choices=["textcnn", "bert"], help="使用的模型 (默认 textcnn)")
    parser.add_argument("--anime_id", type=int, default=None,
                        help="只预测指定动漫ID的评论")
    parser.add_argument("--batch_size", type=int, default=128,
                        help="每批预测的评论数 (默认128)")
    parser.add_argument("--overwrite", action="store_true",
                        help="覆盖已有的预测结果")
    parser.add_argument("--db_path", type=str, default=None,
                        help="数据库路径 (默认 data/anime_sentiment.db)")
    args = parser.parse_args()

    db_path = args.db_path or DB_PATH

    # 1. 读取待预测评论
    logger.info("读取待预测评论 (overwrite=%s, anime_id=%s)", args.overwrite, args.anime_id)
    comments = fetch_comments(db_path, args.anime_id, args.overwrite)
    total = len(comments)

    if total == 0:
        logger.info("没有需要预测的评论，退出")
        return

    logger.info("共 %d 条评论待预测", total)

    # 2. 加载模型
    logger.info("加载模型: %s", args.model)
    classifier = load_model(args.model)

    # 3. 分批预测并回写
    batch_size = args.batch_size
    done = 0
    start_time = time.time()

    for i in range(0, total, batch_size):
        batch = comments[i:i + batch_size]
        ids = [row[0] for row in batch]
        texts = [row[1] for row in batch]

        # 预测
        results = classifier.predict(texts)

        # 构造回写数据
        updates = []
        for comment_id, result in zip(ids, results):
            updates.append((
                result["label"],
                result["confidence"],
                args.model,
                comment_id
            ))

        # 回写数据库
        update_predictions(db_path, updates)

        done += len(batch)
        elapsed = time.time() - start_time
        speed = done / elapsed if elapsed > 0 else 0
        logger.info("进度: %d/%d (%.1f%%)  速度: %.1f条/秒",
                     done, total, done / total * 100, speed)

    elapsed = time.time() - start_time
    logger.info("========================================")
    logger.info("批量预测完成！模型: %s, 共 %d 条, 耗时 %.1f秒", args.model, total, elapsed)
    logger.info("========================================")


if __name__ == "__main__":
    main()