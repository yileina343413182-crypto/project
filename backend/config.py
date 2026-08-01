# -*- coding: utf-8 -*-
"""后端配置。"""

import os
from pathlib import Path
from urllib.parse import quote_plus

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
DB_PATH = os.environ.get(
    "DATABASE_PATH",
    os.path.join(PROJECT_ROOT, "data", "anime_sentiment.db"),
).strip()


def _build_mysql_url(driver: str) -> str | None:
    """仅在完整提供 MySQL 认证信息时构造 URL，避免猜测本机凭据。"""
    user = os.environ.get("MYSQL_USER", "").strip()
    password = os.environ.get("MYSQL_PASSWORD", "")
    database = os.environ.get("MYSQL_DATABASE", "").strip()
    if not user or not database:
        return None
    host = os.environ.get("MYSQL_HOST", "127.0.0.1").strip()
    port = _get_int_env("MYSQL_PORT", 3306)
    return (
        f"mysql+{driver}://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{database}?charset=utf8mb4"
    )


def _sqlite_url(driver: str) -> str:
    path = Path(DB_PATH).resolve().as_posix()
    return f"sqlite+{driver}:///{path}"


# 未配置 MySQL 凭据时继续使用 SQLite，确保迁移分支可回归且不会误连数据库。
# 正式切换只需提供 DATABASE_URL / ASYNC_DATABASE_URL 或 MYSQL_* 环境变量。
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if not DATABASE_URL:
    DATABASE_URL = _build_mysql_url("pymysql") or _sqlite_url("pysqlite")

ASYNC_DATABASE_URL = os.environ.get("ASYNC_DATABASE_URL", "").strip()
if not ASYNC_DATABASE_URL:
    if DATABASE_URL.startswith("mysql+"):
        ASYNC_DATABASE_URL = DATABASE_URL.replace(
            DATABASE_URL.split(":", 1)[0],
            "mysql+aiomysql",
            1,
        )
    elif DATABASE_URL.startswith("sqlite+"):
        ASYNC_DATABASE_URL = DATABASE_URL.replace(
            DATABASE_URL.split(":", 1)[0],
            "sqlite+aiosqlite",
            1,
        )
    else:
        ASYNC_DATABASE_URL = _sqlite_url("aiosqlite")

DATABASE_IS_MYSQL = DATABASE_URL.startswith("mysql+")

# 模型路径
TEXTCNN_MODEL_DIR = os.path.join(PROJECT_ROOT, "models", "saved", "textcnn")
BERT_MODEL_DIR = os.path.join(PROJECT_ROOT, "models", "saved", "bert")

# 默认使用的预测模型
DEFAULT_MODEL = "bert"  # textcnn 或 bert

# 停用词
STOPWORDS_PATH = os.path.join(PROJECT_ROOT, "data", "stopwords.txt")

# ASGI 服务；保留 FLASK_* 环境变量作为迁移期兼容入口。
DEBUG = _get_bool_env("APP_DEBUG", _get_bool_env("FLASK_DEBUG", True))
HOST = os.environ.get("APP_HOST", os.environ.get("FLASK_HOST", "0.0.0.0"))
PORT = _get_int_env("APP_PORT", _get_int_env("FLASK_PORT", 5000))

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
RECOMMEND_TOOL_MAX_ROUNDS = _get_int_env("RECOMMEND_TOOL_MAX_ROUNDS", 3)
RECOMMEND_GRAPH_RECURSION_LIMIT = _get_int_env("RECOMMEND_GRAPH_RECURSION_LIMIT", 30)
RECOMMEND_CHECKPOINT_DB = os.environ.get(
    "RECOMMEND_CHECKPOINT_DB",
    os.path.join(PROJECT_ROOT, "data", "langgraph_checkpoints.db"),
).strip()

# ===== JWT 认证配置 =====
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "").strip()
if not JWT_SECRET_KEY:
    if DEBUG:
        JWT_SECRET_KEY = "dev-only-change-me"
    else:
        raise RuntimeError("JWT_SECRET_KEY must be set when debug mode is disabled")
JWT_ACCESS_TOKEN_EXPIRES = _get_int_env("JWT_ACCESS_TOKEN_EXPIRES", 86400)  # 默认24小时（秒）
