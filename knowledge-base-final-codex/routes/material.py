"""Material generation and summarization routes."""

from flask import Blueprint, request, jsonify

import config
from services.material_gen import generate_material, TEMPLATES
from services.summarizer import summarize_documents
from services.embed_key_router import pick_key

material_bp = Blueprint("material", __name__)


@material_bp.route("/api/kb/summarize", methods=["POST"])
def kb_summarize():
    data = request.get_json() or {}
    files = data.get("files", [])
    api_key = data.get("api_key", config.DEEPSEEK_API_KEY)
    kb_name = data.get("kb_name", config.CURRENT_KB)

    embedding_api_key = data.get("embedding_api_key", "")
    if embedding_api_key:
        config.EMBEDDING_API_KEY = embedding_api_key

    if not files:
        return jsonify({"success": False, "error": "请选择要总结的文件"})
    if not api_key:
        return jsonify({"success": False, "error": "请先配置 DeepSeek API 密钥"})

    try:
        result = summarize_documents(files, api_key, kb_name=kb_name)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": f"总结失败: {str(e)}"})


@material_bp.route("/api/material/templates", methods=["GET"])
def get_templates():
    return jsonify({"success": True, "data": {k: v for k, v in TEMPLATES.items()}})


@material_bp.route("/api/material/report-templates", methods=["GET"])
def get_report_templates():
    audit_templates = {k: v for k, v in TEMPLATES.items() if k.startswith("审计")}
    return jsonify({"success": True, "data": audit_templates})


@material_bp.route("/api/material/generate", methods=["POST"])
def generate():
    data = request.get_json() or {}
    framework = data.get("framework", "").strip()
    api_key = data.get("api_key", config.DEEPSEEK_API_KEY)
    kb_name = data.get("kb_name", config.CURRENT_KB)

    embedding_api_key = data.get("embedding_api_key", "")
    if embedding_api_key:
        config.EMBEDDING_API_KEY = embedding_api_key
    embed_key = pick_key(embedding_api_key)

    if not framework:
        return jsonify({"success": False, "error": "请提供框架内容"})
    if not api_key:
        return jsonify({"success": False, "error": "请先配置 DeepSeek API 密钥"})

    try:
        result = generate_material(framework, api_key, embed_key=embed_key, kb_name=kb_name)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "error": f"生成失败: {str(e)}"})


@material_bp.route("/api/material/export", methods=["POST"])
def export_material():
    data = request.get_json() or {}
    text = data.get("text", "")
    fmt = data.get("format", "md")
    if fmt == "md":
        return jsonify({"success": True, "data": {"text": text, "mime": "text/markdown"}})
    return jsonify({"success": True, "data": {"text": text, "mime": "text/plain"}})
