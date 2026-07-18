# -*- coding: utf-8 -*-
"""
Flask 后端配置
"""

import os

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _get_bool_env(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _get_int_env(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_cors_origins():
    value = os.environ.get("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    value = value.strip()
    if value == "*":
        return "*"
    return [origin.strip() for origin in value.split(",") if origin.strip()]

# 数据库
DB_PATH = os.path.join(PROJECT_ROOT, "data", "anime_sentiment.db")

# 模型路径
TEXTCNN_MODEL_DIR = os.path.join(PROJECT_ROOT, "models", "saved", "textcnn")
BERT_MODEL_DIR = os.path.join(PROJECT_ROOT, "models", "saved", "bert")

# 默认使用的预测模型
DEFAULT_MODEL = "bert"  # textcnn 或 bert

# 停用词
STOPWORDS_PATH = os.path.join(PROJECT_ROOT, "data", "stopwords.txt")

# Flask
DEBUG = _get_bool_env("FLASK_DEBUG", True)
HOST = os.environ.get("FLASK_HOST", "0.0.0.0")
PORT = _get_int_env("FLASK_PORT", 5000)

# CORS 跨域
CORS_ORIGINS = _get_cors_origins()

# ===== LLM 配置 =====

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "qwen").strip().lower()

# 不同服务商只能读取对应的密钥，避免 Qwen 误用 OPENAI_API_KEY
_PROVIDER_API_KEYS = {
    "qwen": (
        "LLM_API_KEY",
        "DASHSCOPE_API_KEY",
        "QWEN_API_KEY",
    ),
    "openai": (
        "LLM_API_KEY",
        "OPENAI_API_KEY",
    ),
    "zhipu": (
        "LLM_API_KEY",
        "ZHIPU_API_KEY",
    ),
}

LLM_API_KEY = next(
    (
        os.environ.get(name, "").strip()
        for name in _PROVIDER_API_KEYS.get(
            LLM_PROVIDER,
            ("LLM_API_KEY",),
        )
        if os.environ.get(name, "").strip()
    ),
    "",
)

_LLM_PRESETS = {
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3-8b",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-5.4-mini",
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
    },
}

_preset = _LLM_PRESETS.get(
    LLM_PROVIDER,
    _LLM_PRESETS["qwen"],
)

LLM_BASE_URL = os.environ.get(
    "LLM_BASE_URL",
    _preset["base_url"],
).strip()

LLM_MODEL = os.environ.get(
    "LLM_MODEL",
    _preset["model"],
).strip()

# 外部模型服务超时时快速进入本地推荐，避免等待约 90 秒
LLM_TIMEOUT = _get_int_env("LLM_TIMEOUT", 30)
LLM_MAX_RETRIES = _get_int_env("LLM_MAX_RETRIES", 0)

# ===== Opinion Agent LLM budget =====
OPINION_LLM_TIMEOUT = _get_int_env("OPINION_LLM_TIMEOUT", LLM_TIMEOUT)
OPINION_LLM_MAX_TOKENS = _get_int_env("OPINION_LLM_MAX_TOKENS", 900)
OPINION_LLM_REPAIR_MAX_TOKENS = _get_int_env("OPINION_LLM_REPAIR_MAX_TOKENS", 700)
OPINION_PROMPT_MAX_CHARS = _get_int_env("OPINION_PROMPT_MAX_CHARS", 7000)

# ===== 推荐 Agent 2.0 配置 =====

RECOMMEND_CANDIDATE_LIMIT = _get_int_env(
    "RECOMMEND_CANDIDATE_LIMIT",
    8,
)

RECOMMEND_EVIDENCE_CANDIDATES = _get_int_env(
    "RECOMMEND_EVIDENCE_CANDIDATES",
    4,
)

RECOMMEND_EVIDENCE_PER_ANIME = _get_int_env(
    "RECOMMEND_EVIDENCE_PER_ANIME",
    3,
)

RECOMMEND_COMMENT_MAX_CHARS = _get_int_env(
    "RECOMMEND_COMMENT_MAX_CHARS",
    160,
)

RECOMMEND_CONTEXT_MAX_CHARS = _get_int_env(
    "RECOMMEND_CONTEXT_MAX_CHARS",
    2000,
)

RECOMMEND_HISTORY_LIMIT = _get_int_env(
    "RECOMMEND_HISTORY_LIMIT",
    6,
)

RECOMMEND_LLM_REPAIR_RETRIES = _get_int_env(
    "RECOMMEND_LLM_REPAIR_RETRIES",
    1,
)
RECOMMEND_LLM_MAX_TOKENS = _get_int_env("RECOMMEND_LLM_MAX_TOKENS", 800)
RECOMMEND_LLM_REPAIR_MAX_TOKENS = _get_int_env("RECOMMEND_LLM_REPAIR_MAX_TOKENS", 500)
RECOMMEND_LLM_TIMEOUT = _get_int_env("RECOMMEND_LLM_TIMEOUT", 20)
RECOMMEND_PROMPT_MAX_CHARS = _get_int_env("RECOMMEND_PROMPT_MAX_CHARS", 6000)

# ===== JWT 认证配置 =====
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "").strip()
if not JWT_SECRET_KEY:
    if DEBUG:
        JWT_SECRET_KEY = "dev-only-change-me"
    else:
        raise RuntimeError("JWT_SECRET_KEY must be set when FLASK_DEBUG is disabled")
JWT_ACCESS_TOKEN_EXPIRES = _get_int_env("JWT_ACCESS_TOKEN_EXPIRES", 86400)  # 默认24小时（秒）
