# -*- coding: utf-8 -*-
"""
统一训练入口

命令行接口，支持训练TextCNN和BERT模型。

用法示例：
    python -m models.trainer --model textcnn --data_path data/train/sentiment_train.csv --epochs 10
    python -m models.trainer --model bert --data_path data/train/sentiment_train.csv --epochs 3 --batch_size 16 --lr 2e-5
"""

import os
import sys
import argparse
import logging

import pandas as pd
from sklearn.model_selection import train_test_split

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def load_data(data_path, test_size=0.2, random_state=42):
    """
    加载训练数据并划分训练集/验证集

    Args:
        data_path: CSV文件路径，需包含 text 和 label 列
        test_size: 验证集比例
        random_state: 随机种子

    Returns:
        tuple: (train_texts, train_labels, val_texts, val_labels)
    """
    logger.info("加载数据: %s", data_path)
    df = pd.read_csv(data_path)

    if "text" not in df.columns or "label" not in df.columns:
        raise ValueError("数据文件必须包含 'text' 和 'label' 列")

    # 去除空值
    df = df.dropna(subset=["text", "label"])
    df = df[df["text"].str.strip().astype(bool)]

    logger.info("数据量: %d 条", len(df))
    logger.info("标签分布:\n%s", df["label"].value_counts().to_string())

    # 划分训练集和验证集
    train_df, val_df = train_test_split(
        df, test_size=test_size, random_state=random_state, stratify=df["label"]
    )

    train_texts = train_df["text"].tolist()
    train_labels = train_df["label"].tolist()
    val_texts = val_df["text"].tolist()
    val_labels = val_df["label"].tolist()

    logger.info("训练集: %d 条, 验证集: %d 条", len(train_texts), len(val_texts))
    return train_texts, train_labels, val_texts, val_labels


def train_textcnn(args):
    """训练TextCNN模型"""
    from models.textcnn_classifier import TextCNNClassifier

    train_texts, train_labels, val_texts, val_labels = load_data(
        args.data_path, test_size=args.test_size
    )

    classifier = TextCNNClassifier(
        embed_dim=args.embed_dim,
        num_filters=args.num_filters,
        dropout=args.dropout,
        max_len=args.max_len,
        max_vocab_size=args.max_vocab_size
    )

    history = classifier.train(
        train_texts, train_labels,
        val_texts, val_labels,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr
    )

    # 保存模型
    save_dir = os.path.join(PROJECT_ROOT, "models", "saved", "textcnn")
    classifier.save(save_dir)

    # 自动运行评估
    _auto_evaluate(classifier, val_texts, val_labels, "TextCNN")

    return classifier, history


def train_bert(args):
    """训练BERT模型"""
    from models.bert_classifier import BertClassifier

    train_texts, train_labels, val_texts, val_labels = load_data(
        args.data_path, test_size=args.test_size
    )

    classifier = BertClassifier(
        bert_model_name=args.bert_model,
        max_len=args.max_len,
        dropout=args.dropout
    )

    history = classifier.train(
        train_texts, train_labels,
        val_texts, val_labels,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr
    )

    # 保存模型
    save_dir = os.path.join(PROJECT_ROOT, "models", "saved", "bert")
    classifier.save(save_dir)

    # 自动运行评估
    _auto_evaluate(classifier, val_texts, val_labels, "BERT")

    return classifier, history


def _auto_evaluate(classifier, val_texts, val_labels, model_name):
    """训练后自动评估"""
    try:
        from models.evaluator import ModelEvaluator

        logger.info("===== 自动评估 %s =====", model_name)
        evaluator = ModelEvaluator()

        pred_labels = classifier.predict_labels(val_texts)
        report = evaluator.evaluate(val_labels, pred_labels, model_name=model_name)
        evaluator.print_report(report)

        # 保存评估报告
        report_dir = os.path.join(PROJECT_ROOT, "models", "saved", "reports")
        os.makedirs(report_dir, exist_ok=True)
        report_path = os.path.join(report_dir, "%s_eval_report.txt" % model_name.lower())
        evaluator.save_report(report, report_path)

    except ImportError:
        logger.warning("评估模块未找到，跳过自动评估")
    except Exception as e:
        logger.warning("自动评估失败: %s", str(e))


def main():
    parser = argparse.ArgumentParser(description="情感分析模型训练入口")

    # 通用参数
    parser.add_argument("--model", type=str, required=True, choices=["textcnn", "bert"],
                        help="模型类型: textcnn 或 bert")
    parser.add_argument("--data_path", type=str, required=True,
                        help="训练数据CSV路径（需包含text和label列）")
    parser.add_argument("--epochs", type=int, default=None,
                        help="训练轮数 (TextCNN默认10, BERT默认3)")
    parser.add_argument("--batch_size", type=int, default=None,
                        help="批次大小 (TextCNN默认64, BERT默认16)")
    parser.add_argument("--lr", type=float, default=None,
                        help="学习率 (TextCNN默认1e-3, BERT默认2e-5)")
    parser.add_argument("--dropout", type=float, default=0.3,
                        help="Dropout概率 (默认0.3)")
    parser.add_argument("--max_len", type=int, default=128,
                        help="最大序列长度 (默认128)")
    parser.add_argument("--test_size", type=float, default=0.2,
                        help="验证集比例 (默认0.2)")

    # TextCNN专用参数
    parser.add_argument("--embed_dim", type=int, default=128,
                        help="[TextCNN] 词嵌入维度 (默认128)")
    parser.add_argument("--num_filters", type=int, default=128,
                        help="[TextCNN] 每种卷积核数量 (默认128)")
    parser.add_argument("--max_vocab_size", type=int, default=50000,
                        help="[TextCNN] 最大词汇表大小 (默认50000)")

    # BERT专用参数
    parser.add_argument("--bert_model", type=str, default="bert-base-chinese",
                        help="[BERT] 预训练模型名称或本地路径 (默认bert-base-chinese)")

    args = parser.parse_args()

    # 设置模型默认参数
    if args.model == "textcnn":
        if args.epochs is None:
            args.epochs = 10
        if args.batch_size is None:
            args.batch_size = 64
        if args.lr is None:
            args.lr = 1e-3
    elif args.model == "bert":
        if args.epochs is None:
            args.epochs = 3
        if args.batch_size is None:
            args.batch_size = 16
        if args.lr is None:
            args.lr = 2e-5

    logger.info("=" * 60)
    logger.info("模型: %s", args.model.upper())
    logger.info("数据: %s", args.data_path)
    logger.info("参数: epochs=%d, batch_size=%d, lr=%s, dropout=%.2f, max_len=%d",
                args.epochs, args.batch_size, args.lr, args.dropout, args.max_len)
    logger.info("=" * 60)

    if args.model == "textcnn":
        train_textcnn(args)
    elif args.model == "bert":
        train_bert(args)


if __name__ == "__main__":
    main()
