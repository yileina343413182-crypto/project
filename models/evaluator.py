# -*- coding: utf-8 -*-
"""
模型评估模块

功能：
    - 计算 Accuracy / Precision / Recall / F1（逐类别 + macro/weighted）
    - 生成混淆矩阵
    - 保存评估报告为文本文件
    - 支持 TextCNN vs BERT 对比
"""

import os
import logging
from collections import OrderedDict

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
    classification_report,
    confusion_matrix
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

LABELS = ["positive", "neutral", "negative"]


class ModelEvaluator:
    """模型评估器"""

    def evaluate(self, true_labels, pred_labels, model_name="Model"):
        """
        评估模型性能

        Args:
            true_labels: 真实标签列表
            pred_labels: 预测标签列表
            model_name: 模型名称

        Returns:
            dict: 评估报告
        """
        report = OrderedDict()
        report["model_name"] = model_name
        report["total_samples"] = len(true_labels)

        # 总体准确率
        report["accuracy"] = accuracy_score(true_labels, pred_labels)

        # Macro 平均
        report["macro_precision"] = precision_score(true_labels, pred_labels,
                                                     labels=LABELS, average="macro",
                                                     zero_division=0)
        report["macro_recall"] = recall_score(true_labels, pred_labels,
                                               labels=LABELS, average="macro",
                                               zero_division=0)
        report["macro_f1"] = f1_score(true_labels, pred_labels,
                                       labels=LABELS, average="macro",
                                       zero_division=0)

        # Weighted 平均
        report["weighted_precision"] = precision_score(true_labels, pred_labels,
                                                        labels=LABELS, average="weighted",
                                                        zero_division=0)
        report["weighted_recall"] = recall_score(true_labels, pred_labels,
                                                   labels=LABELS, average="weighted",
                                                   zero_division=0)
        report["weighted_f1"] = f1_score(true_labels, pred_labels,
                                          labels=LABELS, average="weighted",
                                          zero_division=0)

        # 逐类别指标
        report["per_class"] = {}
        for label in LABELS:
            p = precision_score(true_labels, pred_labels, labels=[label],
                                average="macro", zero_division=0)
            r = recall_score(true_labels, pred_labels, labels=[label],
                             average="macro", zero_division=0)
            f = f1_score(true_labels, pred_labels, labels=[label],
                         average="macro", zero_division=0)
            support = sum(1 for t in true_labels if t == label)
            report["per_class"][label] = {
                "precision": p, "recall": r, "f1": f, "support": support
            }

        # 混淆矩阵
        cm = confusion_matrix(true_labels, pred_labels, labels=LABELS)
        report["confusion_matrix"] = cm.tolist()

        # sklearn 完整分类报告（字符串格式）
        report["classification_report"] = classification_report(
            true_labels, pred_labels, labels=LABELS, zero_division=0
        )

        return report

    def print_report(self, report):
        """打印评估报告"""
        print("\n" + "=" * 60)
        print("模型评估报告: %s" % report["model_name"])
        print("=" * 60)
        print("样本数: %d" % report["total_samples"])
        print()

        # 逐类别
        print("%-12s %-10s %-10s %-10s %-8s" % (
            "类别", "Precision", "Recall", "F1-Score", "Support"))
        print("-" * 50)
        for label in LABELS:
            cls = report["per_class"][label]
            print("%-12s %-10.4f %-10.4f %-10.4f %-8d" % (
                label, cls["precision"], cls["recall"], cls["f1"], cls["support"]))

        print("-" * 50)
        print("%-12s %-10.4f %-10.4f %-10.4f" % (
            "Macro Avg", report["macro_precision"],
            report["macro_recall"], report["macro_f1"]))
        print("%-12s %-10.4f %-10.4f %-10.4f" % (
            "Weighted Avg", report["weighted_precision"],
            report["weighted_recall"], report["weighted_f1"]))
        print()
        print("Accuracy: %.4f" % report["accuracy"])

        # 混淆矩阵
        print("\n混淆矩阵 (行=真实, 列=预测):")
        print("%-12s %s" % ("", "  ".join("%-10s" % l for l in LABELS)))
        cm = report["confusion_matrix"]
        for i, label in enumerate(LABELS):
            row_str = "  ".join("%-10d" % cm[i][j] for j in range(len(LABELS)))
            print("%-12s %s" % (label, row_str))

        print("=" * 60)

    def save_report(self, report, filepath):
        """
        保存评估报告到文本文件

        Args:
            report: evaluate()返回的报告字典
            filepath: 保存路径
        """
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        lines = []
        lines.append("=" * 60)
        lines.append("模型评估报告: %s" % report["model_name"])
        lines.append("=" * 60)
        lines.append("样本数: %d" % report["total_samples"])
        lines.append("")

        lines.append("%-12s %-10s %-10s %-10s %-8s" % (
            "类别", "Precision", "Recall", "F1-Score", "Support"))
        lines.append("-" * 50)
        for label in LABELS:
            cls = report["per_class"][label]
            lines.append("%-12s %-10.4f %-10.4f %-10.4f %-8d" % (
                label, cls["precision"], cls["recall"], cls["f1"], cls["support"]))

        lines.append("-" * 50)
        lines.append("%-12s %-10.4f %-10.4f %-10.4f" % (
            "Macro Avg", report["macro_precision"],
            report["macro_recall"], report["macro_f1"]))
        lines.append("%-12s %-10.4f %-10.4f %-10.4f" % (
            "Weighted Avg", report["weighted_precision"],
            report["weighted_recall"], report["weighted_f1"]))
        lines.append("")
        lines.append("Accuracy: %.4f" % report["accuracy"])
        lines.append("")

        lines.append("混淆矩阵 (行=真实, 列=预测):")
        lines.append("%-12s %s" % ("", "  ".join("%-10s" % l for l in LABELS)))
        cm = report["confusion_matrix"]
        for i, label in enumerate(LABELS):
            row_str = "  ".join("%-10d" % cm[i][j] for j in range(len(LABELS)))
            lines.append("%-12s %s" % (label, row_str))

        lines.append("=" * 60)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        logger.info("评估报告已保存: %s", filepath)

    def compare_models(self, reports):
        """
        对比多个模型的评估结果

        Args:
            reports: 评估报告列表

        Returns:
            str: 对比结果文本
        """
        lines = []
        lines.append("\n" + "=" * 70)
        lines.append("模型对比")
        lines.append("=" * 70)

        # 表头
        header = "%-15s %-10s %-10s %-10s %-10s %-10s" % (
            "Model", "Accuracy", "M-Prec", "M-Recall", "M-F1", "W-F1")
        lines.append(header)
        lines.append("-" * 65)

        for report in reports:
            row = "%-15s %-10.4f %-10.4f %-10.4f %-10.4f %-10.4f" % (
                report["model_name"],
                report["accuracy"],
                report["macro_precision"],
                report["macro_recall"],
                report["macro_f1"],
                report["weighted_f1"]
            )
            lines.append(row)

        lines.append("-" * 65)

        # 找出最优模型
        best = max(reports, key=lambda r: r["weighted_f1"])
        lines.append("最优模型（Weighted F1）: %s (%.4f)" % (
            best["model_name"], best["weighted_f1"]))

        lines.append("=" * 70)

        result = "\n".join(lines)
        print(result)
        return result

    def save_comparison(self, reports, filepath):
        """保存模型对比报告"""
        result = self.compare_models(reports)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(result)
        logger.info("对比报告已保存: %s", filepath)
