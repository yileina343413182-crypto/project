# -*- coding: utf-8 -*-
"""
BERT 情感分类模型

基于 Hugging Face transformers 的 bert-base-chinese 微调模型。

模型结构：
    bert-base-chinese → [CLS] hidden → Dropout → FC → 3分类

支持：
    - GPU/CPU自适应
    - 保存/加载微调后的完整模型
    - 预测返回标签 + 置信度
"""

import os
import logging

import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from transformers import BertTokenizer, BertModel, BertConfig

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


# ===================== 数据集 =====================

class BertDataset(Dataset):
    """BERT输入数据集"""

    def __init__(self, texts, labels, tokenizer, max_len=128):
        self.texts = texts
        self.labels = labels
        self.tokenizer = tokenizer
        self.max_len = max_len

    def __len__(self):
        return len(self.texts)

    def __getitem__(self, idx):
        text = str(self.texts[idx])
        label = LABEL2ID.get(self.labels[idx], 1)

        encoding = self.tokenizer(
            text,
            add_special_tokens=True,
            max_length=self.max_len,
            padding="max_length",
            truncation=True,
            return_tensors="pt"
        )

        return {
            "input_ids": encoding["input_ids"].squeeze(0),
            "attention_mask": encoding["attention_mask"].squeeze(0),
            "label": torch.tensor(label, dtype=torch.long)
        }


# ===================== BERT分类模型 =====================

class BertSentimentModel(nn.Module):
    """
    BERT情感分类模型

    Args:
        bert_model_name: 预训练模型名称或路径
        num_classes: 分类数
        dropout: dropout概率
    """

    def __init__(self, bert_model_name="bert-base-chinese", num_classes=3, dropout=0.3):
        super(BertSentimentModel, self).__init__()
        self.bert = BertModel.from_pretrained(bert_model_name)
        hidden_size = self.bert.config.hidden_size  # 768
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Linear(hidden_size, num_classes)

    def forward(self, input_ids, attention_mask):
        """
        前向传播
        input_ids: (batch, seq_len)
        attention_mask: (batch, seq_len)
        """
        outputs = self.bert(input_ids=input_ids, attention_mask=attention_mask)
        # 取 [CLS] token 的输出
        cls_output = outputs.last_hidden_state[:, 0, :]  # (batch, hidden_size)
        cls_output = self.dropout(cls_output)
        logits = self.fc(cls_output)  # (batch, num_classes)
        return logits


# ===================== 训练与预测接口 =====================

class BertClassifier:
    """BERT分类器封装，提供train/predict/save/load接口"""

    def __init__(self, bert_model_name="bert-base-chinese", max_len=128, dropout=0.3):
        self.bert_model_name = bert_model_name
        self.max_len = max_len
        self.dropout = dropout
        self.device = DEVICE
        self.tokenizer = None
        self.model = None

    def train(self, train_texts, train_labels, val_texts=None, val_labels=None,
              epochs=3, batch_size=16, lr=2e-5, warmup_ratio=0.1):
        """
        训练BERT模型

        Args:
            train_texts: 训练文本列表
            train_labels: 训练标签列表
            val_texts: 验证文本列表（可选）
            val_labels: 验证标签列表（可选）
            epochs: 训练轮数（推荐3-5）
            batch_size: 批次大小（BERT较大，推荐8-16）
            lr: 学习率（推荐2e-5）
            warmup_ratio: warmup比例

        Returns:
            dict: 训练历史 {train_loss, train_acc, val_loss, val_acc}
        """
        # 初始化tokenizer和模型
        logger.info("加载预训练模型: %s", self.bert_model_name)
        self.tokenizer = BertTokenizer.from_pretrained(self.bert_model_name)
        self.model = BertSentimentModel(
            bert_model_name=self.bert_model_name,
            num_classes=3,
            dropout=self.dropout
        ).to(self.device)

        # 构建数据集
        train_dataset = BertDataset(train_texts, train_labels, self.tokenizer, self.max_len)
        train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)

        val_loader = None
        if val_texts is not None and val_labels is not None:
            val_dataset = BertDataset(val_texts, val_labels, self.tokenizer, self.max_len)
            val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False)

        # 优化器：BERT参数使用较小学习率
        optimizer = torch.optim.AdamW(self.model.parameters(), lr=lr, weight_decay=0.01)

        # 学习率调度器
        total_steps = len(train_loader) * epochs
        warmup_steps = int(total_steps * warmup_ratio)
        scheduler = torch.optim.lr_scheduler.LinearLR(
            optimizer, start_factor=0.1, total_iters=warmup_steps
        )

        criterion = nn.CrossEntropyLoss()

        history = {"train_loss": [], "train_acc": [], "val_loss": [], "val_acc": []}

        logger.info("开始训练BERT (device=%s, epochs=%d, batch_size=%d, lr=%s)",
                     self.device, epochs, batch_size, lr)

        for epoch in range(epochs):
            # ===== 训练阶段 =====
            self.model.train()
            total_loss, correct, total = 0, 0, 0

            for step, batch in enumerate(train_loader):
                input_ids = batch["input_ids"].to(self.device)
                attention_mask = batch["attention_mask"].to(self.device)
                labels = batch["label"].to(self.device)

                optimizer.zero_grad()
                logits = self.model(input_ids, attention_mask)
                loss = criterion(logits, labels)
                loss.backward()

                # 梯度裁剪，防止梯度爆炸
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)

                optimizer.step()
                scheduler.step()

                total_loss += loss.item() * input_ids.size(0)
                preds = logits.argmax(dim=1)
                correct += (preds == labels).sum().item()
                total += input_ids.size(0)

                # 每50步打印一次进度
                if (step + 1) % 50 == 0:
                    logger.info("  Epoch [%d/%d] Step [%d/%d] loss=%.4f",
                                epoch + 1, epochs, step + 1, len(train_loader),
                                loss.item())

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

        logger.info("BERT训练完成")
        return history

    def _evaluate(self, data_loader, criterion):
        """评估模型"""
        self.model.eval()
        total_loss, correct, total = 0, 0, 0

        with torch.no_grad():
            for batch in data_loader:
                input_ids = batch["input_ids"].to(self.device)
                attention_mask = batch["attention_mask"].to(self.device)
                labels = batch["label"].to(self.device)

                logits = self.model(input_ids, attention_mask)
                loss = criterion(logits, labels)

                total_loss += loss.item() * input_ids.size(0)
                preds = logits.argmax(dim=1)
                correct += (preds == labels).sum().item()
                total += input_ids.size(0)

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

        dataset = BertDataset(texts, ["neutral"] * len(texts), self.tokenizer, self.max_len)
        loader = DataLoader(dataset, batch_size=16, shuffle=False)

        with torch.no_grad():
            for batch in loader:
                input_ids = batch["input_ids"].to(self.device)
                attention_mask = batch["attention_mask"].to(self.device)

                logits = self.model(input_ids, attention_mask)
                probs = torch.softmax(logits, dim=1)

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
        保存微调后的模型、tokenizer和配置

        Args:
            save_dir: 保存目录
        """
        os.makedirs(save_dir, exist_ok=True)

        # 保存完整模型（包括BERT权重和分类头）
        model_path = os.path.join(save_dir, "bert_sentiment_model.pt")
        torch.save(self.model.state_dict(), model_path)

        # 保存tokenizer
        self.tokenizer.save_pretrained(save_dir)

        # 保存BERT config（用于重建模型）
        self.model.bert.config.save_pretrained(save_dir)

        import json
        config = {
            "bert_model_name": self.bert_model_name,
            "max_len": self.max_len,
            "dropout": self.dropout,
        }
        config_path = os.path.join(save_dir, "classifier_config.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

        logger.info("BERT模型已保存到: %s", save_dir)

    def load(self, save_dir):
        """
        加载微调后的模型

        Args:
            save_dir: 模型目录
        """
        import json

        # 加载配置
        config_path = os.path.join(save_dir, "classifier_config.json")
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        self.max_len = config["max_len"]
        self.dropout = config["dropout"]

        # 加载tokenizer
        self.tokenizer = BertTokenizer.from_pretrained(save_dir)

        # 重建模型（使用保存的BERT config，避免重新下载）
        bert_config = BertConfig.from_pretrained(save_dir)
        self.model = BertSentimentModel.__new__(BertSentimentModel)
        nn.Module.__init__(self.model)
        self.model.bert = BertModel(bert_config)
        hidden_size = bert_config.hidden_size
        self.model.dropout = nn.Dropout(self.dropout)
        self.model.fc = nn.Linear(hidden_size, 3)

        # 加载权重
        model_path = os.path.join(save_dir, "bert_sentiment_model.pt")
        self.model.load_state_dict(
            torch.load(model_path, map_location=self.device, weights_only=True)
        )
        self.model.to(self.device)
        self.model.eval()

        logger.info("BERT模型已加载: %s", save_dir)
