# -*- coding: utf-8 -*-
"""
用户认证 API

POST /api/auth/register  — 注册
POST /api/auth/login     — 登录
GET  /api/auth/me        — 获取当前用户信息（JWT保护）
"""

import re
import bcrypt
from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity

from backend.database import create_user, get_user_by_username, get_user_by_id

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

# 用户名规则：3-20位，字母/数字/下划线
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_\u4e00-\u9fa5]{3,20}$")


def _ok(data=None, msg="success"):
    return jsonify({"code": 200, "msg": msg, "data": data})


def _err(msg, code=400):
    return jsonify({"code": code, "msg": msg, "data": None}), code


@auth_bp.route("/register", methods=["POST"])
def register():
    """注册新用户"""
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    # 基本校验
    if not username or not password:
        return _err("用户名和密码不能为空")
    if not _USERNAME_RE.match(username):
        return _err("用户名需为3-20位（字母、数字、下划线或中文）")
    if len(password) < 6:
        return _err("密码至少6位")
    if len(password) > 72:
        return _err("密码不能超过72位")

    # 检查用户名是否已存在
    if get_user_by_username(username):
        return _err("用户名已存在，请换一个")

    # bcrypt 哈希密码
    pwd_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    user_id = create_user(username, pwd_hash)
    token = create_access_token(identity=str(user_id))

    return _ok({"token": token, "username": username, "user_id": user_id}, msg="注册成功")


@auth_bp.route("/login", methods=["POST"])
def login():
    """用户登录"""
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    if not username or not password:
        return _err("用户名和密码不能为空")

    user = get_user_by_username(username)
    if not user:
        return _err("用户名或密码错误", 401)

    # 验证密码
    if not bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8")):
        return _err("用户名或密码错误", 401)

    token = create_access_token(identity=str(user["id"]))

    return _ok({
        "token": token,
        "username": user["username"],
        "user_id": user["id"]
    }, msg="登录成功")


@auth_bp.route("/me", methods=["GET"])
@jwt_required()
def me():
    """获取当前登录用户信息（验证token有效性）"""
    user_id = int(get_jwt_identity())
    user = get_user_by_id(user_id)
    if not user:
        return _err("用户不存在", 404)
    return _ok({"user_id": user["id"], "username": user["username"], "created_at": user["created_at"]})
