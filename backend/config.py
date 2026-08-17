# -*- coding: utf-8 -*-
"""集中读取后端配置，并为数据库、模型、Agent 和认证提供默认值。

环境变量优先于本地配置文件；模块只负责组装配置，不在这里建立连接或
调用外部服务。
"""

import os
from pathlib import Path
from urllib.parse import quote_plus

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_local_env(path: str | None = None) -> None:
    """加载本机私有环境文件，但不覆盖进程中已经设置的变量。"""
    env_path = Path(path or os.path.join(PROJECT_ROOT, ".env.mysql.local"))
    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name or not name.replace("_", "a").isalnum():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(name, value)


_load_local_env()


def _get_bool_env(name, default=False):
    """把常见的真值字符串转换为布尔值，缺失时返回默认值。"""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _get_int_env(name, default):
    """读取整数环境变量；非法值按未配置处理。"""
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_cors_origins():
    """将逗号分隔的来源转换为列表，并保留通配符语义。"""
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
    """使用绝对路径构造指定驱动的 SQLite SQLAlchemy URL。"""
    path = Path(DB_PATH).resolve().as_posix()
    return f"sqlite+{driver}:///{path}"


# 同步 URL 供离线任务和 Agent 使用，异步 URL 供 FastAPI 请求使用。
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
# 密钥只在当前提供商对应的变量中查找，避免误把其他平台密钥发送出去。
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

# 预设只给出兼容接口地址和默认模型，显式环境变量仍具有最高优先级。
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

# ===== Rerank 配置 =====
RERANK_PROVIDER = os.environ.get("RERANK_PROVIDER", "qwen").strip().lower()
RERANK_WORKSPACE_ID = os.environ.get("RERANK_WORKSPACE_ID", "").strip()
RERANK_MODEL = os.environ.get("RERANK_MODEL", "qwen3-rerank").strip()
RERANK_BASE_URL = os.environ.get("RERANK_BASE_URL", "").strip()
if not RERANK_BASE_URL and RERANK_WORKSPACE_ID:
    RERANK_BASE_URL = (
        f"https://{RERANK_WORKSPACE_ID}.cn-beijing.maas.aliyuncs.com/"
        "compatible-api/v1/reranks"
    )
RERANK_API_KEY = next(
    (
        os.environ.get(name, "").strip()
        for name in ("RERANK_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY", "LLM_API_KEY")
        if os.environ.get(name, "").strip()
    ),
    "",
)
RERANK_TIMEOUT = _get_int_env("RERANK_TIMEOUT", 20)
RERANK_MAX_DOCUMENT_CHARS = _get_int_env("RERANK_MAX_DOCUMENT_CHARS", 6000)

# ===== Opinion Agent LLM budget =====
OPINION_LLM_TIMEOUT = _get_int_env("OPINION_LLM_TIMEOUT", LLM_TIMEOUT)
OPINION_LLM_MAX_TOKENS = _get_int_env("OPINION_LLM_MAX_TOKENS", 1800)
OPINION_LLM_REPAIR_MAX_TOKENS = _get_int_env("OPINION_LLM_REPAIR_MAX_TOKENS", 2000)
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
RECOMMEND_LLM_MAX_TOKENS = _get_int_env("RECOMMEND_LLM_MAX_TOKENS", 2800)
RECOMMEND_LLM_REPAIR_MAX_TOKENS = _get_int_env("RECOMMEND_LLM_REPAIR_MAX_TOKENS", 2800)
RECOMMEND_FOLLOWUP_MAX_TOKENS = _get_int_env("RECOMMEND_FOLLOWUP_MAX_TOKENS", 2200)
RECOMMEND_LLM_TIMEOUT = _get_int_env("RECOMMEND_LLM_TIMEOUT", 60)
RECOMMEND_PROMPT_MAX_CHARS = _get_int_env("RECOMMEND_PROMPT_MAX_CHARS", 6000)
RECOMMEND_TOOL_MAX_ROUNDS = _get_int_env("RECOMMEND_TOOL_MAX_ROUNDS", 3)
RECOMMEND_GRAPH_RECURSION_LIMIT = _get_int_env("RECOMMEND_GRAPH_RECURSION_LIMIT", 30)
RECOMMEND_CHECKPOINT_DB = os.environ.get(
    "RECOMMEND_CHECKPOINT_DB",
    os.path.join(PROJECT_ROOT, "data", "langgraph_checkpoints.db"),
).strip()
REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0").strip()
AGENT_REDIS_KEY_PREFIX = os.environ.get(
    "AGENT_REDIS_KEY_PREFIX",
    "anime-agent",
).strip().strip(":")
RECOMMEND_CHECKPOINT_BACKEND = os.environ.get(
    "RECOMMEND_CHECKPOINT_BACKEND",
    "redis",
).strip().lower()
if RECOMMEND_CHECKPOINT_BACKEND not in {"redis", "sqlite"}:
    raise RuntimeError("RECOMMEND_CHECKPOINT_BACKEND must be 'redis' or 'sqlite'")
RECOMMEND_CHECKPOINT_REDIS_URL = os.environ.get(
    "RECOMMEND_CHECKPOINT_REDIS_URL",
    REDIS_URL,
).strip()
RECOMMEND_CHECKPOINT_SQLITE_FALLBACK = _get_bool_env(
    "RECOMMEND_CHECKPOINT_SQLITE_FALLBACK",
    DEBUG,
)
RECOMMEND_CHECKPOINT_TTL_MINUTES = max(
    1,
    _get_int_env("RECOMMEND_CHECKPOINT_TTL_MINUTES", 1440),
)
RECOMMEND_REDIS_MAX_CONNECTIONS = max(
    1,
    _get_int_env("RECOMMEND_REDIS_MAX_CONNECTIONS", 4),
)

# ===== Agent Celery / Redis 分布式并发配置 =====

# 保留 M1 的并发变量作为 Celery Worker 部署参数；队列本身不再驻留在 API 进程。
# 同一会话仍由数据库事务中的活动任务检查严格串行化。
AGENT_PARALLEL_ENABLED = _get_bool_env("AGENT_PARALLEL_ENABLED", True)
AGENT_MAX_CONCURRENT = max(
    1,
    _get_int_env(
        "AGENT_MAX_CONCURRENT",
        _get_int_env("AGENT_TASK_WORKERS", 4),
    ),
)
RECOMMEND_AGENT_MAX_CONCURRENT = max(
    1,
    _get_int_env("RECOMMEND_AGENT_MAX_CONCURRENT", 2),
)
OPINION_AGENT_MAX_CONCURRENT = max(
    1,
    _get_int_env("OPINION_AGENT_MAX_CONCURRENT", 2),
)
CELERY_BROKER_URL = os.environ.get(
    "CELERY_BROKER_URL",
    REDIS_URL,
).strip()
CELERY_RESULT_BACKEND = os.environ.get(
    "CELERY_RESULT_BACKEND",
    REDIS_URL,
).strip()
CELERY_STORE_RESULTS = _get_bool_env("CELERY_STORE_RESULTS", False)
CELERY_BROKER_POOL_LIMIT = max(
    1,
    _get_int_env("CELERY_BROKER_POOL_LIMIT", 3),
)
CELERY_REDIS_MAX_CONNECTIONS = max(
    1,
    _get_int_env("CELERY_REDIS_MAX_CONNECTIONS", 6),
)
CELERY_VISIBILITY_TIMEOUT = max(
    300,
    _get_int_env("CELERY_VISIBILITY_TIMEOUT", 3600),
)
CELERY_RESULT_EXPIRES = max(
    300,
    _get_int_env("CELERY_RESULT_EXPIRES", 86400),
)
CELERY_BROKER_SOCKET_TIMEOUT = max(
    1,
    _get_int_env("CELERY_BROKER_SOCKET_TIMEOUT", 5),
)
AGENT_TASK_LEASE_SECONDS = max(
    60,
    _get_int_env("AGENT_TASK_LEASE_SECONDS", 180),
)
AGENT_TASK_HEARTBEAT_SECONDS = max(
    5,
    min(
        _get_int_env("AGENT_TASK_HEARTBEAT_SECONDS", 30),
        AGENT_TASK_LEASE_SECONDS // 2,
    ),
)
AGENT_TASK_QUEUE_STALE_SECONDS = max(
    60,
    _get_int_env("AGENT_TASK_QUEUE_STALE_SECONDS", 300),
)
AGENT_TASK_RECOVERY_INTERVAL_SECONDS = max(
    60,
    _get_int_env("AGENT_TASK_RECOVERY_INTERVAL_SECONDS", 120),
)

# ===== JWT 认证配置 =====
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "").strip()
if not JWT_SECRET_KEY:
    if DEBUG:
        JWT_SECRET_KEY = "dev-only-change-me"
    else:
        raise RuntimeError("JWT_SECRET_KEY must be set when debug mode is disabled")
JWT_ACCESS_TOKEN_EXPIRES = _get_int_env("JWT_ACCESS_TOKEN_EXPIRES", 86400)  # 默认24小时（秒）
