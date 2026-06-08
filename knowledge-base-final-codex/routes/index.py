"""Index routes — build_index, stop, cancel, indexing-status, failed-files."""

import threading

from flask import Blueprint, request, jsonify

import config
from state import get_index_status, _indexing_stop_events, _indexing_stop_events_lock
from services.kb_manager import resolve_kb
from services.vector_store import get_store
from services.pipeline import run_pipeline
from services.failed_files import build_failed_groups, load_failed_files
from services.embed_key_router import pick_key

index_bp = Blueprint("index", __name__)


def _get_kb_name():
    if request.method == "GET":
        name = request.args.get("kb_name", config.CURRENT_KB)
    else:
        data = request.get_json(silent=True) or {}
        name = data.get("kb_name", config.CURRENT_KB)
    return resolve_kb(name)


@index_bp.route("/api/kb/index", methods=["POST"])
def build_index():
    data = request.get_json() or {}
    files = data.get("files", [])
    kb_name = data.get("kb_name", config.CURRENT_KB)

    if not files:
        return jsonify({"success": False, "error": "没有提供文件列表"})
    api_key = data.get("api_key", config.DEEPSEEK_API_KEY)
    if not api_key:
        return jsonify({"success": False, "error": "请先配置 DeepSeek API 密钥"})

    embedding_api_key = data.get("embedding_api_key", "")
    embed_keys = [k.strip() for k in embedding_api_key.split(",") if k.strip()] if embedding_api_key else []
    if embed_keys:
        config.EMBEDDING_API_KEY = embed_keys[0]
    else:
        embed_keys = [pick_key("") or api_key]
    if not embed_keys or not embed_keys[0]:
        embed_keys = [api_key]

    status = get_index_status(kb_name)
    if status.get("running"):
        return jsonify({"success": False, "error": f"知识库「{kb_name}」正在索引中，请稍候"})

    # Set running flag BEFORE spawning thread to prevent race condition
    status.clear()
    status.update({"running": True, "progress": "初始化...", "total": len(files), "current": 0, "errors": []})

    def index_task(kb=kb_name, flist=files):
        store = get_store(kb)
        idx_status = get_index_status(kb)
        with _indexing_stop_events_lock:
            stop_event = _indexing_stop_events.setdefault(kb, threading.Event())
        stop_event.clear()
        run_pipeline(kb, flist, store, idx_status, stop_event, api_key, embed_keys)

    thread = threading.Thread(target=index_task, daemon=True)
    thread.start()
    return jsonify({"success": True, "data": {"message": "索引已启动", "total": len(files)}})


@index_bp.route("/api/kb/indexing-status", methods=["GET"])
def get_indexing_status():
    kb_name = request.args.get("kb_name", config.CURRENT_KB)
    status = dict(get_index_status(kb_name))
    if "cancelled_indices" in status and isinstance(status["cancelled_indices"], set):
        status["cancelled_indices"] = sorted(status["cancelled_indices"])
    return jsonify({"success": True, "data": status})


@index_bp.route("/api/kb/failed-files", methods=["GET"])
def get_failed_files():
    kb_name = request.args.get("kb_name", config.CURRENT_KB)
    status = get_index_status(kb_name)

    fss = status.get("file_statuses", [])
    errors = status.get("errors", [])
    if not fss:
        cached = load_failed_files(kb_name)
        if cached:
            return jsonify({"success": True, "data": cached})
        return jsonify({"success": True, "data": {"total": 0, "failed": [], "groups": {}}})

    if status.get("running"):
        return jsonify({"success": False, "error": "索引仍在进行中"})

    result = build_failed_groups(fss, errors)
    return jsonify({"success": True, "data": result})


@index_bp.route("/api/kb/stop-indexing", methods=["POST"])
def stop_indexing():
    kb_name = _get_kb_name()
    with _indexing_stop_events_lock:
        event = _indexing_stop_events.get(kb_name)
    if event:
        event.set()
        return jsonify({"success": True, "data": {"message": "已发送停止信号，将在当前批次完成后停止"}})
    return jsonify({"success": False, "error": "没有正在进行的索引任务"})


@index_bp.route("/api/kb/cancel-file", methods=["POST"])
def cancel_file():
    """Cancel a single file in the running index pipeline."""
    data = request.get_json() or {}
    kb_name = data.get("kb_name", config.CURRENT_KB)
    file_path = data.get("file_path", "").strip()
    file_index = data.get("file_index")
    if file_index is None and not file_path:
        return jsonify({"success": False, "error": "请指定 file_path 或 file_index"})

    status = get_index_status(kb_name)
    if not status.get("running"):
        return jsonify({"success": False, "error": "没有正在进行的索引任务"})

    if "cancelled_indices" not in status:
        status["cancelled_indices"] = set()

    fss = status.get("file_statuses", [])
    if file_index is not None:
        if file_index < 0 or file_index >= len(fss):
            return jsonify({"success": False, "error": f"file_index {file_index} 超出范围 (0-{len(fss)-1})"})
        st = fss[file_index]["status"]
        if st in ("embedded", "duplicate", "error", "cancelled", "cancelling"):
            return jsonify({"success": False, "error": f"文件状态为 {st}，无法取消"})
        status["cancelled_indices"].add(file_index)
        fss[file_index]["status"] = "cancelling"
        return jsonify({"success": True, "data": {"message": f"已取消文件: {fss[file_index]['name']}"}})

    for idx, fs in enumerate(fss):
        if fs.get("path") == file_path or os.path.normpath(fs.get("path", "")) == os.path.normpath(file_path):
            st = fs["status"]
            if st in ("embedded", "duplicate", "error", "cancelled", "cancelling"):
                return jsonify({"success": False, "error": f"文件状态为 {st}，无法取消"})
            status["cancelled_indices"].add(idx)
            fss[idx]["status"] = "cancelling"
            return jsonify({"success": True, "data": {"message": f"已取消文件: {fs['name']}"}})

    return jsonify({"success": False, "error": "未找到匹配的文件"})
