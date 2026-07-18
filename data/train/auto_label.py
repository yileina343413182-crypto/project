# -*- coding: utf-8 -*-
"""
自动标注脚本

功能：
    1. 读取 data/processed/ 下所有清洗后的CSV文件
    2. 对Bangumi数据根据评分自动标注（4-5星=positive, 3星=neutral, 1-2星=negative）
    3. 对B站数据使用SnowNLP情感打分自动标注（>0.6=positive, 0.3-0.6=neutral, <0.3=negative）
    4. 输出统一格式的训练数据 data/train/sentiment_train.csv

用法：
    python auto_label.py
    python auto_label.py --output data/train/sentiment_train.csv --max_per_class 5000

依赖：
    pip install pandas snownlp
"""

import os
import sys
import glob
import argparse
import logging
import random

import pandas as pd
from snownlp import SnowNLP

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROCESSED_DIR = os.path.join(PROJECT_ROOT, "data", "processed")
TRAIN_DIR = os.path.join(PROJECT_ROOT, "data", "train")


def clean_text(text):
    """简单文本清洗：去HTML、表情标记，保留中文英文数字和常见标点"""
    import re
    text = str(text)
    # 去除HTML标签
    text = re.sub(r"<[^>]+>", "", text)
    # 去除B站表情标记 [xxx]
    text = re.sub(r"\[[\w\u4e00-\u9fff]+\]", "", text)
    # 去除URL
    text = re.sub(r"https?://\S+", "", text)
    # 保留中文、英文、数字和常见标点
    text = re.sub(r"[^\u4e00-\u9fff\u3000-\u303fa-zA-Z0-9，。！？、；：\u201c\u201d\u2018\u2019（）\s]", "", text)
    return text.strip()


def label_by_rating(rate):
    """根据Bangumi评分（1-10分）标注情感"""
    if pd.isna(rate):
        return None
    rate = float(rate)
    if rate >= 8:      # 4-5星（Bangumi是10分制，8-10分对应4-5星）
        return "positive"
    elif rate >= 5:    # 3星（5-7分）
        return "neutral"
    else:              # 1-2星（1-4分）
        return "negative"


def label_by_snownlp(text):
    """使用SnowNLP对文本进行情感打分标注"""
    try:
        score = SnowNLP(str(text)).sentiments
        if score > 0.6:
            return "positive"
        elif score < 0.3:
            return "negative"
        else:
            return "neutral"
    except Exception:
        return None


def process_bangumi_files():
    """处理Bangumi数据（基于评分标注）"""
    bgm_dir = os.path.join(PROCESSED_DIR, "bangumi_top100")
    if not os.path.isdir(bgm_dir):
        logger.warning("Bangumi目录不存在: %s", bgm_dir)
        return pd.DataFrame()

    csv_files = sorted(glob.glob(os.path.join(bgm_dir, "cleaned_*.csv")))
    logger.info("找到 %d 个Bangumi文件", len(csv_files))

    all_rows = []
    for fpath in csv_files:
        try:
            df = pd.read_csv(fpath, encoding="utf-8-sig")
        except Exception:
            try:
                df = pd.read_csv(fpath, encoding="utf-8")
            except Exception as e:
                logger.warning("读取失败: %s - %s", fpath, e)
                continue

        if "content" not in df.columns:
            continue

        # Bangumi有rate列，用评分标注
        if "rate" in df.columns:
            for _, row in df.iterrows():
                text = clean_text(row.get("content", ""))
                if len(text) < 5:
                    continue
                label = label_by_rating(row.get("rate"))
                if label:
                    all_rows.append({
                        "text": text,
                        "label": label,
                        "source": "bangumi",
                        "anime": str(row.get("anime_title", ""))
                    })
        else:
            # 无评分则用SnowNLP
            for _, row in df.iterrows():
                text = clean_text(row.get("content", ""))
                if len(text) < 5:
                    continue
                label = label_by_snownlp(text)
                if label:
                    all_rows.append({
                        "text": text,
                        "label": label,
                        "source": "bangumi",
                        "anime": str(row.get("anime_title", ""))
                    })

    result = pd.DataFrame(all_rows)
    logger.info("Bangumi标注完成: %d 条", len(result))
    return result


def process_bilibili_files():
    """处理B站数据（基于SnowNLP标注）"""
    bili_dir = os.path.join(PROCESSED_DIR, "bilibili_top100")
    if not os.path.isdir(bili_dir):
        logger.warning("B站目录不存在: %s", bili_dir)
        return pd.DataFrame()

    csv_files = sorted(glob.glob(os.path.join(bili_dir, "cleaned_*.csv")))
    logger.info("找到 %d 个B站文件", len(csv_files))

    all_rows = []
    for fpath in csv_files:
        try:
            df = pd.read_csv(fpath, encoding="utf-8-sig")
        except Exception:
            try:
                df = pd.read_csv(fpath, encoding="utf-8")
            except Exception as e:
                logger.warning("读取失败: %s - %s", fpath, e)
                continue

        if "content" not in df.columns:
            continue

        # 弹幕文件用较短的阈值
        is_danmaku = "_dm_" in os.path.basename(fpath)
        min_len = 2 if is_danmaku else 5

        for _, row in df.iterrows():
            text = clean_text(row.get("content", ""))
            if len(text) < min_len:
                continue
            label = label_by_snownlp(text)
            if label:
                all_rows.append({
                    "text": text,
                    "label": label,
                    "source": "bilibili",
                    "anime": str(row.get("anime_title", ""))
                })

    result = pd.DataFrame(all_rows)
    logger.info("B站标注完成: %d 条", len(result))
    return result


def balance_dataset(df, max_per_class=None):
    """
    平衡数据集，确保各类别样本数大致均衡。
    如果指定max_per_class，则每类最多取该数量；
    否则按最小类别数量进行下采样。
    """
    if df.empty:
        return df

    counts = df["label"].value_counts()
    logger.info("标注分布: %s", counts.to_dict())

    if max_per_class is None:
        max_per_class = counts.min()

    balanced_parts = []
    for label in ["positive", "negative", "neutral"]:
        subset = df[df["label"] == label]
        if len(subset) > max_per_class:
            subset = subset.sample(n=max_per_class, random_state=42)
        balanced_parts.append(subset)

    result = pd.concat(balanced_parts, ignore_index=True)
    result = result.sample(frac=1, random_state=42).reset_index(drop=True)

    final_counts = result["label"].value_counts()
    logger.info("平衡后分布: %s, 总计: %d", final_counts.to_dict(), len(result))
    return result


def main():
    parser = argparse.ArgumentParser(description="自动标注情感数据")
    parser.add_argument("--output", type=str, default=None,
                        help="输出文件路径（默认 data/train/sentiment_train.csv）")
    parser.add_argument("--max_per_class", type=int, default=None,
                        help="每类最大样本数（用于平衡数据集）")
    parser.add_argument("--no_balance", action="store_true",
                        help="不进行数据平衡")
    args = parser.parse_args()

    output_path = args.output or os.path.join(TRAIN_DIR, "sentiment_train.csv")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # 处理各平台数据
    df_bgm = process_bangumi_files()
    df_bili = process_bilibili_files()

    # 合并
    df_all = pd.concat([df_bgm, df_bili], ignore_index=True)
    logger.info("合并总数: %d 条", len(df_all))

    if df_all.empty:
        logger.error("无数据可供标注！请确认 data/processed/ 下有清洗后的CSV文件")
        return

    # 去重
    df_all = df_all.drop_duplicates(subset=["text"]).reset_index(drop=True)
    logger.info("去重后: %d 条", len(df_all))

    # 平衡数据集
    if not args.no_balance:
        df_all = balance_dataset(df_all, max_per_class=args.max_per_class)

    # 保存
    df_all.to_csv(output_path, index=False, encoding="utf-8-sig")
    logger.info("标注数据已保存: %s (%d 条)", output_path, len(df_all))

    # 打印统计
    print("\n===== 标注数据统计 =====")
    print("总样本数: %d" % len(df_all))
    print("\n按标签统计:")
    print(df_all["label"].value_counts().to_string())
    print("\n按来源统计:")
    print(df_all["source"].value_counts().to_string())
    print("\n样本示例:")
    for label in ["positive", "negative", "neutral"]:
        sample = df_all[df_all["label"] == label].head(2)
        for _, row in sample.iterrows():
            print("  [%s] %s" % (row["label"], row["text"][:60]))


if __name__ == "__main__":
    main()
