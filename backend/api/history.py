# -*- coding: utf-8 -*-
"""
聊天历史 API（JWT 保护）

POST   /api/history/chat        — 保存聊天消息（问+答一次保存）
GET    /api/history/chat        — 分页获取当前用户历史
DELETE /api/history/chat/<id>   — 删除指定历史条目
"""

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from backend.database import save_chat_message, get_chat_history, delete_chat_message

history_bp = Blueprint("history", __name__, url_prefix="/api/history")


def _ok(data=None, msg="success"):
    return jsonify({"code": 200, "msg": msg, "data": data})


def _err(msg, code=400):
    return jsonify({"code": code, "msg": msg, "data": None}), code


@history_bp.route("/chat", methods=["POST"])
@jwt_required()
def save_history():
    """
    保存一次完整的问答记录（用户问 + AI答，合并为一条记录）
    Body: { user_content, ai_content, anime_card(可选) }
    """
    user_id = int(get_jwt_identity())
    body = request.get_json(silent=True) or {}

    user_content = (body.get("user_content") or "").strip()
    ai_content = (body.get("ai_content") or "").strip()
    anime_card = body.get("anime_card")  # dict or None

    if not user_content or not ai_content:
        return _err("user_content 和 ai_content 不能为空")

    # 将用户问和AI答各保存一条（同一user_id）
    save_chat_message(user_id, "user", user_content)
    msg_id = save_chat_message(user_id, "ai", ai_content, anime_card)

    return _ok({"msg_id": msg_id}, msg="保存成功")


@history_bp.route("/chat", methods=["GET"])
@jwt_required()
def list_history():
    """获取当前用户的聊天历史（分页）"""
    user_id = int(get_jwt_identity())
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(50, max(1, int(request.args.get("page_size", 20))))
    except (ValueError, TypeError):
        page, page_size = 1, 20

    result = get_chat_history(user_id, page=page, page_size=page_size)
    return _ok(result)


@history_bp.route("/chat/<int:msg_id>", methods=["DELETE"])
@jwt_required()
def delete_history(msg_id):
    """删除指定历史条目（只能删自己的）"""
    user_id = int(get_jwt_identity())
    affected = delete_chat_message(msg_id, user_id)
    if affected == 0:
        return _err("记录不存在或无权删除", 404)
    return _ok(msg="删除成功")
