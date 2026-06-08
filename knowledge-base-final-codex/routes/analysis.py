"""Analysis routes — deep audit analysis (focus + scan modes)."""

from flask import Blueprint, request, jsonify

import config
from services.deep_analysis import focus_analysis, scan_analysis

analysis_bp = Blueprint("analysis", __name__)


@analysis_bp.route("/api/analysis/focus", methods=["POST"])
def focus():
    """Mode A: user selects files + describes task → system checks only those files."""
    data = request.get_json() or {}
    file_paths = data.get("file_paths", [])
    task = data.get("task", "").strip()
    api_key = data.get("api_key", config.DEEPSEEK_API_KEY)
    kb_name = data.get("kb_name", config.CURRENT_KB)

    if not file_paths:
        return jsonify({"success": False, "error": "请选择要分析的文件"})
    if not task:
        return jsonify({"success": False, "error": "请描述审计任务"})

    try:
        result = focus_analysis(file_paths, task, api_key=api_key, kb_name=kb_name)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "error": f"分析失败: {str(e)}"})


@analysis_bp.route("/api/analysis/scan", methods=["POST"])
def scan():
    """Mode B: user provides framework → system scans all files → fills framework."""
    data = request.get_json() or {}
    framework = data.get("framework", "").strip()
    api_key = data.get("api_key", config.DEEPSEEK_API_KEY)
    kb_name = data.get("kb_name", config.CURRENT_KB)

    if not framework:
        return jsonify({"success": False, "error": "请输入分析框架"})

    try:
        result = scan_analysis(framework, api_key=api_key, kb_name=kb_name)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "error": f"分析失败: {str(e)}"})
