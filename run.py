# -*- coding: utf-8 -*-
"""
一键启动脚本

功能：
    1. 初始化数据库（建表）
    2. 检查数据是否存在，无数据时提示先运行 generate_demo_data.py
    3. 检查模型文件是否存在
    4. 启动 FastAPI 后端服务

"""

import os
import sys
import logging
import argparse

from sqlalchemy import func, select

# Windows 终端 UTF-8 输出
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

TEXTCNN_MODEL_DIR = os.path.join(PROJECT_ROOT, "models", "saved", "textcnn")
BERT_MODEL_DIR = os.path.join(PROJECT_ROOT, "models", "saved", "bert")

from backend.db.models import Anime, Comment, Topic
from backend.db.session import get_sync_engine, session_scope

# ANSI 颜色
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"


def banner():
    print(f"""{CYAN}{BOLD}
+----------------------------------------------------------+
|  动漫评论情感分析与舐情监控系统  v1.0                        |
|  Anime Sentiment Analysis & Opinion Monitoring          |
+----------------------------------------------------------+
{RESET}""")


def step(msg):
    print(f"{CYAN}>> {msg}{RESET}")


def ok(msg):
    print(f"{GREEN}  [OK] {msg}{RESET}")


def warn(msg):
    print(f"{YELLOW}  [!!] {msg}{RESET}")


def err(msg):
    print(f"{RED}  [ERR] {msg}{RESET}")


# ───────────────────────── 步骤1：初始化数据库 ─────────────────────────

def init_database():
    step("初始化数据库...")
    os.makedirs(os.path.join(PROJECT_ROOT, "data"), exist_ok=True)
    try:
        from crawler.cleaner import init_database as initialize_business_database
        session = initialize_business_database()
        session.close()
        target = get_sync_engine().url.render_as_string(hide_password=True)
        ok(f"数据库已就绪: {target}")
        return True
    except Exception as e:
        err(f"数据库初始化失败: {e}")
        return False


# ───────────────────────── 步骤2：检查数据 ─────────────────────────

def check_data():
    step("检查数据库数据...")
    try:
        with session_scope() as session:
            anime_count = session.scalar(select(func.count()).select_from(Anime)) or 0
            comment_count = session.scalar(select(func.count()).select_from(Comment)) or 0
            labeled_count = session.scalar(
                select(func.count()).select_from(Comment).where(Comment.sentiment_label.is_not(None))
            ) or 0
            topic_count = session.scalar(select(func.count()).select_from(Topic)) or 0

        ok(f"动漫数量: {anime_count} 部")
        ok(f"评论总数: {comment_count} 条（已标注: {labeled_count}）")
        ok(f"主题数量: {topic_count} 个")

        if anime_count == 0:
            print()
            warn("数据库中没有动漫数据！")
            print(f"""
{YELLOW}  请先准备数据，有以下两种方式：{RESET}

  {BOLD}方式1：爬取真实数据{RESET}
    python prepare_data.py --anime "进击的巨人" --platform bilibili --max_pages 30
""")
            return False

        if labeled_count == 0:
            warn("评论尚未完成情感预测，建议运行：")
            print(f"  python batch_predict.py")

        return True
    except Exception as e:
        err(f"数据检查失败: {e}")
        return False


# ───────────────────────── 步骤3：检查模型 ─────────────────────────

def check_models():
    step("检查模型文件...")

    textcnn_ok = os.path.exists(os.path.join(TEXTCNN_MODEL_DIR, "textcnn_model.pt")) and \
                 os.path.exists(os.path.join(TEXTCNN_MODEL_DIR, "textcnn_vocab.pkl")) and \
                 os.path.exists(os.path.join(TEXTCNN_MODEL_DIR, "textcnn_config.json"))
    bert_ok = os.path.exists(os.path.join(BERT_MODEL_DIR, "bert_sentiment_model.pt")) and \
              os.path.exists(os.path.join(BERT_MODEL_DIR, "classifier_config.json")) and \
              os.path.exists(os.path.join(BERT_MODEL_DIR, "config.json"))

    if textcnn_ok:
        ok("TextCNN 模型已就绪")
    else:
        warn("TextCNN 模型不存在（API 实时预测功能不可用，但查询已有数据正常）")

    if bert_ok:
        ok("BERT 模型已就绪")
    else:
        warn("BERT 模型不存在（API 实时预测功能不可用，但查询已有数据正常）")

    return True  # 模型不存在不阻塞启动


# ───────────────────────── 步骤4：启动 FastAPI ─────────────────────────

def start_fastapi(host="0.0.0.0", port=5000, debug=False):
    step("启动 FastAPI 后端服务...")
    print(f"""
{GREEN}{BOLD}  系统启动成功！{RESET}
{GREEN}  后端 API: http://localhost:{port}/api/health{RESET}
{GREEN}  健康检查: http://localhost:{port}/api/anime/list{RESET}

{CYAN}  前端启动方式（新终端）：{RESET}
    cd frontend
    npm run dev
{CYAN}  前端访问地址: http://localhost:3000{RESET}

{CYAN}  Agent Worker + Beat（另开三个终端，Redis 需已启动）：{RESET}
    celery -A backend.celery_app:celery_app worker -Q agent.recommendation,agent.control -P threads --concurrency=2 -n recommend-local
    celery -A backend.celery_app:celery_app worker -Q agent.opinion -P threads --concurrency=2 -n opinion-local
    celery -A backend.celery_app:celery_app beat -l info

  按 Ctrl+C 停止服务
""")
    try:
        import uvicorn

        uvicorn.run(
            "backend.app:create_app",
            factory=True,
            host=host,
            port=port,
            reload=debug,
            workers=1,
        )
    except ImportError as e:
        err(f"无法导入 FastAPI 应用: {e}")
        err("请确保已安装依赖: pip install -r requirements.txt")
        sys.exit(1)
    except OSError as e:
        if "Address already in use" in str(e) or "10048" in str(e):
            err(f"端口 {port} 已被占用，请使用 --port 指定其他端口")
        else:
            err(f"启动失败: {e}")
        sys.exit(1)


# ───────────────────────── 主入口 ─────────────────────────

def main():
    parser = argparse.ArgumentParser(description="动漫情感分析系统一键启动脚本")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址 (默认 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5000, help="端口号 (默认 5000)")
    parser.add_argument("--debug", action="store_true", help="开启 FastAPI 自动重载")
    parser.add_argument("--no-check", action="store_true", help="跳过数据检查直接启动")
    args = parser.parse_args()

    banner()

    # 步骤1：初始化数据库
    if not init_database():
        sys.exit(1)

    # 步骤2：检查数据（可跳过）
    if not args.no_check:
        data_ok = check_data()
        if not data_ok:
            ans = input(f"\n{YELLOW}数据不完整，是否仍要启动服务？[y/N]: {RESET}").strip().lower()
            if ans not in ("y", "yes"):
                print("已取消启动。")
                sys.exit(0)
    else:
        warn("已跳过数据检查")

    # 步骤3：检查模型
    check_models()

    # 步骤4：启动 FastAPI
    start_fastapi(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
