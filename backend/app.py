# -*- coding: utf-8 -*-
"""
Flask 应用入口

注册所有Blueprint，配置CORS，统一错误处理。

启动方式：
    cd project
    python -m backend.app
"""

import os
import sys
import logging

from datetime import timedelta
from flask import Flask, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager

# 确保项目根目录在搜索路径中
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.config import DEBUG, HOST, PORT, CORS_ORIGINS, JWT_SECRET_KEY, JWT_ACCESS_TOKEN_EXPIRES
from backend.api.sentiment import sentiment_bp
from backend.api.topic import topic_bp
from backend.api.data import data_bp
from backend.api.recommend import recommend_bp
from backend.api.auth import auth_bp
from backend.api.history import history_bp
from backend.api.agent import agent_bp
from backend.api.rag import rag_bp
from backend.database import init_user_tables
from backend.agents.memory import init_agent_tables
from backend.rag.storage import init_rag_tables

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


def create_app():
    """Flask 应用工厂"""
    app = Flask(__name__)

    # JWT 配置
    app.config["JWT_SECRET_KEY"] = JWT_SECRET_KEY
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(seconds=JWT_ACCESS_TOKEN_EXPIRES)
    app.config["JWT_VERIFY_SUB"] = False
    JWTManager(app)

    # CORS 跨域
    CORS(app, resources={r"/api/*": {"origins": CORS_ORIGINS}})

    # 初始化用户相关数据表
    init_user_tables()
    init_agent_tables()
    init_rag_tables()

    # 注册 Blueprint
    app.register_blueprint(sentiment_bp)
    app.register_blueprint(topic_bp)
    app.register_blueprint(data_bp)
    app.register_blueprint(recommend_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(history_bp)
    app.register_blueprint(agent_bp)
    app.register_blueprint(rag_bp)

    # ===== 统一错误处理 =====

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"code": 404, "msg": "接口不存在", "data": None}), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({"code": 405, "msg": "请求方法不允许", "data": None}), 405

    @app.errorhandler(500)
    def internal_error(e):
        logger.error("服务器内部错误: %s", e)
        return jsonify({"code": 500, "msg": "服务器内部错误", "data": None}), 500

    # ===== 健康检查 =====

    @app.route("/api/health", methods=["GET"])
    def health():
        return jsonify({"code": 200, "msg": "ok", "data": {"status": "running"}})

    logger.info("Flask应用创建完成，已注册 %d 个Blueprint", len(app.blueprints))
    return app


if __name__ == "__main__":
    app = create_app()
    logger.info("启动Flask服务: http://%s:%d", HOST, PORT)
    app.run(host=HOST, port=PORT, debug=DEBUG)


