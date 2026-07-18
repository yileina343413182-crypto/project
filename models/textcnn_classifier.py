# -*- coding: utf-8 -*-
"""
TextCNN 情感分类模型

模型结构：
    Embedding → 多尺度卷积(kernel_size=2,3,4) → MaxPooling → Dropout → FC → 3分类

支持：
    - 预训练词向量（可选）或随机初始化
    - GPU/CPU自适应
    - 保存/加载模型权重
"""

import os
import json
import logging
import pickle

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
import jieba

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# 标签映射
LABEL2ID = {"positive": 0, "neutral": 1, "negative": 2}
ID2LABEL = {v: k for k, v in LABEL2ID.items()}

# GPU/CPU自适应
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


# ===================== 词汇表 =====================

class Vocabulary:
    """词汇表，负责文本到索引的转换"""

    def __init__(self, max_size=50000):
        self.word2idx = {"<PAD>": 0, "<UNK>": 1}
        self.idx2word = {0: "<PAD>", 1: "<UNK>"}
        self.word_freq = {}
        self.max_size = max_size

    def build_from_texts(self, texts):
        """从文本列表构建词汇表"""
        for text in texts:
            words = jieba.lcut(str(text))
            for word in words:
                word = word.strip()
                if word:
                    self.word_freq[word] = self.word_freq.get(word, 0) + 1

        # 按词频排序，取top max_size
        sorted_words = sorted(self.word_freq.items(), key=lambda x: x[1], reverse=True)
        for word, freq in sorted_words[:self.max_size - 2]:  # 减去PAD和UNK
            idx = len(self.word2idx)
            self.word2idx[word] = idx
            self.idx2word[idx] = word

        logger.info("词汇表构建完成: %d 个词", len(self.word2idx))

    def text_to_ids(self, text, max_len=128):
        """将文本转换为索引序列"""
        words = jieba.lcut(str(text))
        ids = []
        for word in words:
            word = word.strip()
            if word:
                ids.append(self.word2idx.get(word, 1))  # 1=UNK

        # 截断或填充
        if len(ids) > max_len:
            ids = ids[:max_len]
        else:
            ids = ids + [0] * (max_len - len(ids))
        return ids

    def save(self, path):
        """保存词汇表"""
        with open(path, "wb") as f:
            pickle.dump({
                "word2idx": self.word2idx,
                "idx2word": self.idx2word,
                "max_size": self.max_size
            }, f)

    def load(self, path):
        """加载词汇表"""
        with open(path, "rb") as f:
            data = pickle.load(f)
        self.word2idx = data["word2idx"]
        self.idx2word = data["idx2word"]
        self.max_size = data["max_size"]
        logger.info("词汇表已加载: %d 个词", len(self.word2idx))


# ===================== 数据集 =====================

class TextDataset(Dataset):
    """文本分类数据集"""

    def __init__(self, texts, labels, vocab, max_len=128):
        self.texts = texts
        self.labels = labels
        self.vocab = vocab
        self.max_len = max_len

    def __len__(self):
        return len(self.texts)

    def __getitem__(self, idx):
        ids = self.vocab.text_to_ids(self.texts[idx], self.max_len)
        label = LABEL2ID.get(self.labels[idx], 1)
        return torch.tensor(ids, dtype=torch.long), torch.tensor(label, dtype=torch.long)


# ===================== TextCNN模型 =====================

class TextCNN(nn.Module):
    """
    TextCNN文本分类模型

    Args:
        vocab_size: 词汇表大小
        embed_dim: 词嵌入维度
        num_classes: 分类数
        kernel_sizes: 卷积核尺寸列表
        num_filters: 每种尺寸卷积核的数量
        dropout: dropout概率
        pretrained_embeddings: 预训练词向量矩阵（可选）
    """

    def __init__(self, vocab_size, embed_dim=128, num_classes=3,
                 kernel_sizes=(2, 3, 4), num_filters=128,
                 dropout=0.3, pretrained_embeddings=None):
        super(TextCNN, self).__init__()

        # 词嵌入层
        self.embedding = nn.Embedding(vocab_size, embed_dim, padding_idx=0)
        if pretrained_embeddings is not None:
            self.embedding.weight.data.copy_(torch.FloatTensor(pretrained_embeddings))
            self.embedding.weight.requires_grad = True  # 允许微调

        # 多尺度卷积层
        self.convs = nn.ModuleList([
            nn.Conv1d(in_channels=embed_dim, out_channels=num_filters,
                      kernel_size=ks, padding=0)
            for ks in kernel_sizes
        ])

        # 全连接分类层
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Linear(num_filters * len(kernel_sizes), num_classes)

    def forward(self, x):
        """
        前向传播
        x: (batch_size, seq_len)
        """
        # 嵌入: (batch, seq_len, embed_dim)
        embedded = self.embedding(x)

        # 转置: (batch, embed_dim, seq_len)，适配Conv1d
        embedded = embedded.permute(0, 2, 1)

        # 多尺度卷积 + ReLU + 最大池化
        conv_outputs = []
        for conv in self.convs:
            c = F.relu(conv(embedded))          # (batch, num_filters, L')
            c = F.max_pool1d(c, c.size(2))      # (batch, num_filters, 1)
            c = c.squeeze(2)                     # (batch, num_filters)
            conv_outputs.append(c)

        # 拼接所有卷积核的输出
        out = torch.cat(conv_outputs, dim=1)    # (batch, num_filters * len(kernel_sizes))

        # Dropout + 全连接
        out = self.dropout(out)
        logits = self.fc(out)                   # (batch, num_classes)
        return logits


# ===================== 训练与预测接口 =====================

class TextCNNClassifier:
    """TextCNN分类器封装，提供train/predict/save/load接口"""

    def __init__(self, embed_dim=128, kernel_sizes=(2, 3, 4), num_filters=128,
                 dropout=0.3, max_len=128, max_vocab_size=50000):
        self.embed_dim = embed_dim
        self.kernel_sizes = kernel_sizes
        self.num_filters = num_filters
        self.dropout = dropout
        self.max_len = max_len
        self.max_vocab_size = max_vocab_size

        self.vocab = Vocabulary(max_size=max_vocab_size)
        self.model = None
        self.device = DEVICE

    def train(self, train_texts, train_labels, val_texts=None, val_labels=None,
              epochs=10, batch_size=64, lr=1e-3):
        """
        训练TextCNN模型

        Args:
            train_texts: 训练文本列表
            train_labels: 训练标签列表
            val_texts: 验证文本列表（可选）
            val_labels: 验证标签列表（可选）
            epochs: 训练轮数
            batch_size: 批次大小
            lr: 学习率

        Returns:
            dict: 训练历史 {train_loss, train_acc, val_loss, val_acc}
        """
        # 构建词汇表
        self.vocab.build_from_texts(train_texts)
        vocab_size = len(self.vocab.word2idx)

        # 创建模型
        self.model = TextCNN(
            vocab_size=vocab_size,
            embed_dim=self.embed_dim,
            num_classes=3,
            kernel_sizes=self.kernel_sizes,
            num_filters=self.num_filters,
            dropout=self.dropout
        ).to(self.device)

        # 数据加载
        train_dataset = TextDataset(train_texts, train_labels, self.vocab, self.max_len)
        train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)

        val_loader = None
        if val_texts is not None and val_labels is not None:
            val_dataset = TextDataset(val_texts, val_labels, self.vocab, self.max_len)
            val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False)

        # 优化器和损失函数
        optimizer = torch.optim.Adam(self.model.parameters(), lr=lr)
        criterion = nn.CrossEntropyLoss()

        history = {"train_loss": [], "train_acc": [], "val_loss": [], "val_acc": []}

        logger.info("开始训练TextCNN (device=%s, vocab=%d, epochs=%d)",
                     self.device, vocab_size, epochs)

        for epoch in range(epochs):
            # ===== 训练阶段 =====
            self.model.train()
            total_loss, correct, total = 0, 0, 0

            for batch_x, batch_y in train_loader:
                batch_x = batch_x.to(self.device)
                batch_y = batch_y.to(self.device)

                optimizer.zero_grad()
                logits = self.model(batch_x)
                loss = criterion(logits, batch_y)
                loss.backward()
                optimizer.step()

                total_loss += loss.item() * batch_x.size(0)
                preds = logits.argmax(dim=1)
                correct += (preds == batch_y).sum().item()
                total += batch_x.size(0)

            train_loss = total_loss / total
            train_acc = correct / total
            history["train_loss"].append(train_loss)
            history["train_acc"].append(train_acc)

            msg = "Epoch [%d/%d] train_loss=%.4f train_acc=%.4f" % (
                epoch + 1, epochs, train_loss, train_acc)

            # ===== 验证阶段 =====
            if val_loader is not None:
                val_loss, val_acc = self._evaluate(val_loader, criterion)
                history["val_loss"].append(val_loss)
                history["val_acc"].append(val_acc)
                msg += " val_loss=%.4f val_acc=%.4f" % (val_loss, val_acc)

            logger.info(msg)

        logger.info("TextCNN训练完成")
        return history

    def _evaluate(self, data_loader, criterion):
        """评估模型"""
        self.model.eval()
        total_loss, correct, total = 0, 0, 0

        with torch.no_grad():
            for batch_x, batch_y in data_loader:
                batch_x = batch_x.to(self.device)
                batch_y = batch_y.to(self.device)

                logits = self.model(batch_x)
                loss = criterion(logits, batch_y)

                total_loss += loss.item() * batch_x.size(0)
                preds = logits.argmax(dim=1)
                correct += (preds == batch_y).sum().item()
                total += batch_x.size(0)

        return total_loss / total, correct / total

    def predict(self, texts):
        """
        批量预测

        Args:
            texts: 文本列表或单个字符串

        Returns:
            list[dict]: 每个元素 {label, confidence, scores}
        """
        if self.model is None:
            raise RuntimeError("模型未训练或未加载")

        if isinstance(texts, str):
            texts = [texts]

        self.model.eval()
        results = []

        dataset = TextDataset(texts, ["neutral"] * len(texts), self.vocab, self.max_len)
        loader = DataLoader(dataset, batch_size=64, shuffle=False)

        with torch.no_grad():
            for batch_x, _ in loader:
                batch_x = batch_x.to(self.device)
                logits = self.model(batch_x)
                probs = F.softmax(logits, dim=1)

                for i in range(probs.size(0)):
                    scores = probs[i].cpu().numpy()
                    pred_id = int(scores.argmax())
                    results.append({
                        "label": ID2LABEL[pred_id],
                        "confidence": float(scores[pred_id]),
                        "scores": {
                            "positive": float(scores[0]),
                            "neutral": float(scores[1]),
                            "negative": float(scores[2]),
                        }
                    })

        return results

    def predict_labels(self, texts):
        """仅返回标签列表（用于评估）"""
        results = self.predict(texts)
        return [r["label"] for r in results]

    def save(self, save_dir):
        """
        保存模型权重、词汇表和配置

        Args:
            save_dir: 保存目录
        """
        os.makedirs(save_dir, exist_ok=True)

        # 保存模型权重
        model_path = os.path.join(save_dir, "textcnn_model.pt")
        torch.save(self.model.state_dict(), model_path)

        # 保存词汇表
        vocab_path = os.path.join(save_dir, "textcnn_vocab.pkl")
        self.vocab.save(vocab_path)

        # 保存配置
        config = {
            "embed_dim": self.embed_dim,
            "kernel_sizes": list(self.kernel_sizes),
            "num_filters": self.num_filters,
            "dropout": self.dropout,
            "max_len": self.max_len,
            "vocab_size": len(self.vocab.word2idx),
        }
        config_path = os.path.join(save_dir, "textcnn_config.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

        logger.info("TextCNN模型已保存到: %s", save_dir)

    def load(self, save_dir):
        """
        加载模型权重、词汇表和配置

        Args:
            save_dir: 模型目录
        """
        # 加载配置
        config_path = os.path.join(save_dir, "textcnn_config.json")
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        self.embed_dim = config["embed_dim"]
        self.kernel_sizes = tuple(config["kernel_sizes"])
        self.num_filters = config["num_filters"]
        self.dropout = config["dropout"]
        self.max_len = config["max_len"]

        # 加载词汇表
        vocab_path = os.path.join(save_dir, "textcnn_vocab.pkl")
        self.vocab.load(vocab_path)

        # 创建并加载模型
        self.model = TextCNN(
            vocab_size=config["vocab_size"],
            embed_dim=self.embed_dim,
            num_classes=3,
            kernel_sizes=self.kernel_sizes,
            num_filters=self.num_filters,
            dropout=self.dropout
        ).to(self.device)

        model_path = os.path.join(save_dir, "textcnn_model.pt")
        self.model.load_state_dict(
            torch.load(model_path, map_location=self.device, weights_only=True)
        )
        self.model.eval()

        logger.info("TextCNN模型已加载: %s", save_dir)
