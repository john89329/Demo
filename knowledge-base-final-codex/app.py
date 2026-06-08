"""知识库系统 — main entry point.

Flask app creation, blueprint registration, and server launcher.
All route logic lives in routes/. Pipeline logic in services/pipeline.py.
Shared state in state.py. Utilities in utils.py.
"""

import os
import sys
import secrets
import multiprocessing

from flask import Flask, request, jsonify, render_template

import config
from state import get_index_status
from services.kb_manager import ensure_libraries
from services.vector_store import get_store
from utils import get_logger

_logger = get_logger()

app = Flask(__name__, template_folder="templates")
app.jinja_env.auto_reload = True
app.config["TEMPLATES_AUTO_RELOAD"] = True

# ── Admin token for protected endpoints ────────────────
_admin_token = secrets.token_hex(16)
app.config["ADMIN_TOKEN"] = _admin_token
_logger.info("Admin token: %s", _admin_token)


def _require_admin():
    """Check X-Admin-Token header. Returns None on success, (response, status) on failure."""
    token = request.headers.get("X-Admin-Token", "")
    if not secrets.compare_digest(token, _admin_token):
        return jsonify({"success": False, "error": "Unauthorized — 需要管理员令牌"}), 401
    return None


# ── Root + diag ─────────────────────────────────────────

@app.route("/")
def index():
    from flask import make_response
    import time
    resp = make_response(render_template("index.html"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/api/diag", methods=["GET"])
def diag_info():
    if auth_err := _require_admin():
        return auth_err
    import subprocess
    import base64 as _b64
    info = {
        "sys.executable": sys.executable,
        "sys.version": sys.version,
        "sys.path": sys.path[:5],
        "cwd": os.getcwd(),
    }
    try:
        p = subprocess.run(
            [sys.executable, "-c", "print('subprocess_ok')"],
            capture_output=True, text=True, timeout=10,
        )
        info["subprocess_test"] = {
            "stdout": p.stdout.strip(),
            "stderr": p.stderr.strip()[:200],
            "returncode": p.returncode,
        }
    except Exception as e:
        info["subprocess_test"] = {"error": str(e)}

    # OCR API test (uses in-process config with full keys)
    try:
        from services.ocr_siliconflow import _call_ocr_api
        test_png = _b64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAB4AAAAeCAIAAAC0Ujn1AAAAJElEQVR4nO3MMQ0AAAwDoPo33XpYsg8EkL6JWq1Wq9VqtVp9N5KXgguhe6EuAAAAAElFTkSuQmCC"
        )
        t0 = __import__("time").time()
        ocr_result = _call_ocr_api(test_png, "image/png")
        ocr_elapsed = __import__("time").time() - t0
        key_count = len([k.strip() for k in config.OCR_API_KEY.split(",") if k.strip()])
        info["ocr_test"] = {
            "success": bool(ocr_result),
            "elapsed": round(ocr_elapsed, 2),
            "key_count": key_count,
            "key_prefix": config.OCR_API_KEY[:12] + "..." if config.OCR_API_KEY else "(empty)",
            "raw_ocr_len": len(config.OCR_API_KEY or ""),
            "raw_embed_len": len(config.EMBEDDING_API_KEY or ""),
            "_ocr_internal": (config._get("_ocr_api_key") or "")[:20],
            "_embed_internal": (config._get("_embedding_api_key") or "")[:50],
            "result_preview": (ocr_result or "(empty)")[:300],
        }
    except Exception as _ocr_e:
        import traceback as _tb
        info["ocr_test"] = {"error": str(_ocr_e), "traceback": _tb.format_exc()[-500:]}

    return jsonify({"success": True, "data": info})


# ── Register blueprints ─────────────────────────────────

from routes import register_blueprints
register_blueprints(app)


# ── Server launcher ─────────────────────────────────────

def start_server(host="127.0.0.1", port=5000, debug=False):
    import webbrowser
    os.makedirs(config.INDEX_DIR, exist_ok=True)
    ensure_libraries()
    store = get_store()
    try:
        store.load()
    except Exception as e:
        _logger.warning("索引加载失败，已忽略: %s", e)
    url = f"http://{host}:{port}"
    _logger.info("知识库服务已启动: %s", url)
    webbrowser.open(url)
    app.run(host=host, port=port, debug=debug, threaded=True, use_reloader=False)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    start_server(host="127.0.0.1", port=5001)
