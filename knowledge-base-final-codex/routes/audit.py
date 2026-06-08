"""Audit trail routes — sessions, export, log."""

import time

from flask import Blueprint, request, jsonify

import config
from services.audit_log import (
    create_new_session, switch_session, delete_session,
    list_sessions, get_session, export_session_markdown, set_topic,
)

audit_bp = Blueprint("audit", __name__)


@audit_bp.route("/api/kb/audit-log", methods=["GET"])
def audit_log():
    kb_name = request.args.get("kb_name", config.CURRENT_KB)
    session_id = request.args.get("session_id")

    if session_id:
        session = get_session(kb_name, session_id)
        if not session:
            return jsonify({"success": False, "error": "会话不存在"})
        return jsonify({"success": True, "data": {"sessions": [session.to_dict()]}})

    sessions = list_sessions(kb_name)
    return jsonify({"success": True, "data": {"sessions": sessions}})


@audit_bp.route("/api/kb/export-session", methods=["POST"])
def export_session_route():
    data = request.get_json() or {}
    session_id = data.get("session_id")
    kb_name = data.get("kb_name", config.CURRENT_KB)

    session = get_session(kb_name, session_id) if session_id else None
    if not session:
        return jsonify({"success": False, "error": "会话不存在"})

    text = export_session_markdown(session)
    date_str = time.strftime("%Y%m%d")
    filename = f"审计记录_{session.session_id[:8]}_{date_str}.md"

    return jsonify({
        "success": True,
        "data": {"text": text, "mime": "text/markdown", "filename": filename},
    })


@audit_bp.route("/api/kb/session/new", methods=["POST"])
def new_audit_session():
    data = request.get_json() or {}
    topic = data.get("topic", "")
    kb_name = data.get("kb_name", config.CURRENT_KB)
    session = create_new_session(kb_name, topic)
    return jsonify({
        "success": True,
        "data": {
            "session_id": session.session_id,
            "start_time": session.start_time,
            "topic": session.topic,
        }
    })


@audit_bp.route("/api/kb/session/switch", methods=["POST"])
def switch_audit_session():
    data = request.get_json() or {}
    session_id = data.get("session_id")
    kb_name = data.get("kb_name", config.CURRENT_KB)
    if switch_session(kb_name, session_id):
        return jsonify({"success": True, "data": {"message": "已切换会话"}})
    return jsonify({"success": False, "error": "会话不存在"})


@audit_bp.route("/api/kb/session/delete", methods=["POST"])
def delete_audit_session():
    data = request.get_json() or {}
    session_id = data.get("session_id")
    kb_name = data.get("kb_name", config.CURRENT_KB)
    if not session_id:
        return jsonify({"success": False, "error": "缺少 session_id"})
    if delete_session(kb_name, session_id):
        return jsonify({"success": True, "data": {"message": "会话已删除"}})
    return jsonify({"success": False, "error": "会话不存在"})


@audit_bp.route("/api/kb/session/topic", methods=["POST"])
def set_topic_route():
    data = request.get_json() or {}
    session_id = data.get("session_id")
    topic = data.get("topic", "")
    kb_name = data.get("kb_name", config.CURRENT_KB)
    if not session_id:
        return jsonify({"success": False, "error": "缺少 session_id"})
    if set_topic(kb_name, session_id, topic):
        return jsonify({"success": True, "data": {"message": "会话标题已更新"}})
    return jsonify({"success": False, "error": "会话不存在"})
