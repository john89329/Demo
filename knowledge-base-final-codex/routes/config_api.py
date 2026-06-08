"""Config, shutdown, and connection test routes."""

import os
import sys
import json
import secrets
import time
import threading
import urllib.request

from flask import Blueprint, request, jsonify, current_app

import config

config_bp = Blueprint("config", __name__)


@config_bp.route("/api/config", methods=["GET", "POST"])
def handle_config():
    if request.method == "GET":
        return jsonify({
            "success": True,
            "data": {
                "api_key": config.DEEPSEEK_API_KEY[:6] + "..." if config.DEEPSEEK_API_KEY else "",
                "has_api_key": bool(config.DEEPSEEK_API_KEY),
                "embedding_api_key": config.EMBEDDING_API_KEY[:6] + "..." if config.EMBEDDING_API_KEY else "",
                "has_embedding_api_key": bool(config.EMBEDDING_API_KEY),
                "chunk_size": config.CHUNK_SIZE,
                "top_k": config.TOP_K_RESULTS,
            }
        })
    data = request.get_json() or {}
    if "api_key" in data and data["api_key"]:
        config.DEEPSEEK_API_KEY = data["api_key"]
    if "embedding_api_key" in data:
        config.EMBEDDING_API_KEY = data["embedding_api_key"]
    config.save_settings()
    if "chunk_size" in data:
        config.CHUNK_SIZE = int(data["chunk_size"])
    if "top_k" in data:
        config.TOP_K_RESULTS = int(data["top_k"])
    return jsonify({"success": True, "data": {"message": "配置已更新"}})


@config_bp.route("/api/shutdown", methods=["POST"])
def shutdown():
    token = request.headers.get("X-Admin-Token", "")
    expected = current_app.config.get("ADMIN_TOKEN", "")
    if not expected or not secrets.compare_digest(token, expected):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return jsonify({"success": True, "data": {"message": "服务器正在关闭..."}})


@config_bp.route("/api/config/test", methods=["POST"])
def test_connection():
    data = request.get_json() or {}
    api_key = data.get("api_key", config.DEEPSEEK_API_KEY)
    if not api_key:
        return jsonify({"success": False, "error": "请提供 API 密钥"})
    try:
        req_data = json.dumps({
            "model": config.CHAT_MODEL,
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 5,
        }).encode()
        req = urllib.request.Request(
            url=f"{config.DEEPSEEK_BASE_URL}/chat/completions",
            data=req_data,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
        return jsonify({"success": True, "data": {"message": "连接成功！", "model": result["model"]}})
    except Exception as e:
        return jsonify({"success": False, "error": f"连接失败: {str(e)}"})


@config_bp.route("/api/config/test-embedding", methods=["POST"])
def test_embedding_keys():
    data = request.get_json() or {}
    keys = data.get("keys", "")
    if not keys or not keys.strip():
        return jsonify({"success": False, "error": "请提供 Embedding API 密钥（支持逗号分隔多个密钥）"})
    key_list = [k.strip() for k in keys.split(",") if k.strip()]

    from services.vector_store import get_store
    try:
        store = get_store("default")
        store.load()
    except Exception:
        store = get_store("default")

    results = []
    for key in key_list:
        try:
            t0 = __import__("time").time()
            store.embed_chunks(
                [{"chunk_id": "test", "text": "test connection", "source_file": "test", "file_type": "text", "chunk_index": 0, "total_chunks": 1, "metadata": {}}],
                key,
            )
            elapsed = __import__("time").time() - t0
            results.append({"key": f"{key[:8]}...{key[-4:]}", "status": "ok", "elapsed": round(elapsed, 1)})
        except Exception as e:
            err = str(e)
            if "401" in err or "Invalid token" in err or "Unauthorized" in err:
                results.append({"key": f"{key[:8]}...{key[-4:]}", "status": "auth_error", "error": "密钥无效或未授权"})
            else:
                results.append({"key": f"{key[:8]}...{key[-4:]}", "status": "error", "error": err[:100]})

    ok_count = sum(1 for r in results if r["status"] == "ok")
    return jsonify({
        "success": ok_count > 0,
        "data": {
            "results": results,
            "total": len(results),
            "ok": ok_count,
            "message": f"{ok_count}/{len(results)} 个密钥连接成功" if ok_count else "所有密钥连接失败",
        }
    })


@config_bp.route("/api/ocr/test", methods=["POST"])
def test_ocr():
    """Temp: test OCR API with current config keys."""
    try:
        from services.ocr_siliconflow import _call_ocr_api
        # Create a tiny test image: 1x1 pixel PNG
        import base64
        # minimal valid PNG with text "test"
        test_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAB4AAAAeCAIAAAC0Ujn1AAAAJElEQVR4nO3MMQ0AAAwDoPo33XpYsg8EkL6JWq1Wq9VqtVp9N5KXgguhe6EuAAAAAElFTkSuQmCC"
        )
        t0 = time.time()
        result = _call_ocr_api(test_png, "image/png")
        elapsed = time.time() - t0
        return jsonify({
            "success": bool(result),
            "data": {
                "result": result[:500] if result else "(empty)",
                "elapsed": round(elapsed, 2),
                "key_count": len([k.strip() for k in config.OCR_API_KEY.split(",") if k.strip()]),
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})
