"""KB management routes — libraries CRUD, scan, status, files, clear, etc."""

import os
import json
import hashlib
import shutil
import sys

from flask import Blueprint, request, jsonify

import config
from state import get_index_status
from services.kb_manager import ensure_libraries, load_libraries, save_libraries, find_kb, resolve_kb
from services.vector_store import get_store
from services.text_chunker import chunk_document
from services.embed_key_router import pick_key
from services.failed_files import remove_file_from_failed
from utils import parse_via_subprocess, get_logger, format_size as _format_size

_logger = get_logger()

kb_bp = Blueprint("kb", __name__)


def _get_kb_name():
    if request.method == "GET":
        name = request.args.get("kb_name", config.CURRENT_KB)
    else:
        data = request.get_json(silent=True) or {}
        name = data.get("kb_name", config.CURRENT_KB)
    return resolve_kb(name)


def _remove_from_manifest(kb_name, file_path):
    """Remove a file entry from the indexed manifest."""
    manifest_path = os.path.join(config.INDEX_DIR, kb_name, "file_manifest.json")
    if not os.path.exists(manifest_path):
        return
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        files = manifest.get("files", [])
        norm = os.path.normpath(file_path)
        files = [f for f in files if os.path.normpath(f if isinstance(f, str) else f["path"]) != norm]
        manifest["files"] = files
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ── Library management ─────────────────────────────────

@kb_bp.route("/api/kb/libraries", methods=["GET"])
def list_libraries():
    ensure_libraries()
    libs = load_libraries()
    return jsonify({"success": True, "data": {"libraries": libs, "current": config.CURRENT_KB}})


@kb_bp.route("/api/kb/libraries/create", methods=["POST"])
def create_library():
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"success": False, "error": "请提供知识库名称"})
    if "/" in name or "\\" in name or name in (".", ".."):
        return jsonify({"success": False, "error": "名称包含非法字符"})

    ensure_libraries()
    libs = load_libraries()
    if find_kb(name, libs):
        return jsonify({"success": False, "error": f"知识库「{name}」已存在"})
    libs.append(name)
    save_libraries(libs)
    os.makedirs(os.path.join(config.INDEX_DIR, name), exist_ok=True)
    return jsonify({"success": True, "data": {"message": f"知识库「{name}」已创建", "libraries": libs}})


@kb_bp.route("/api/kb/libraries/delete", methods=["POST"])
def delete_library():
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    ensure_libraries()
    libs = load_libraries()
    actual = find_kb(name, libs)
    if not actual:
        return jsonify({"success": False, "error": f"知识库「{name}」不存在"})
    if len(libs) <= 1:
        return jsonify({"success": False, "error": "至少保留一个知识库"})
    libs.remove(actual)
    save_libraries(libs)
    kb_dir = os.path.join(config.INDEX_DIR, actual)
    if os.path.exists(kb_dir):
        shutil.rmtree(kb_dir, ignore_errors=True)
    if config.CURRENT_KB.lower() == actual.lower():
        config.CURRENT_KB = libs[0]
    return jsonify({"success": True, "data": {"message": f"知识库「{name}」已删除", "libraries": libs, "current": config.CURRENT_KB}})


@kb_bp.route("/api/kb/libraries/switch", methods=["POST"])
def switch_library():
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    ensure_libraries()
    libs = load_libraries()
    actual = find_kb(name, libs)
    if not actual:
        return jsonify({"success": False, "error": f"知识库「{name}」不存在"})
    config.CURRENT_KB = actual
    return jsonify({"success": True, "data": {"libraries": libs, "current": actual}})


# ── Scan ────────────────────────────────────────────────

@kb_bp.route("/api/kb/scan", methods=["POST"])
def scan_directory():
    data = request.get_json() or {}
    directory = data.get("path", "")
    if not directory or not os.path.isdir(directory):
        return jsonify({"success": False, "error": "路径无效或不存在"})
    files = []
    for root, dirs, fnames in os.walk(directory):
        for fname in fnames:
            # Skip WPS Office / MS Office temp & lock files
            if fname.startswith(".~") or fname.startswith("~$"):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext in config.SUPPORTED_EXTENSIONS:
                fpath = os.path.join(root, fname)
                files.append({
                    "path": fpath,
                    "name": fname,
                    "ext": ext,
                    "size": os.path.getsize(fpath),
                    "size_str": _format_size(os.path.getsize(fpath)),
                })
    return jsonify({"success": True, "data": {"directory": directory, "files": files, "count": len(files)}})

# ── Status / Files / Clear ──────────────────────────────

@kb_bp.route("/api/kb/status", methods=["GET"])
def kb_status():
    kb_name = request.args.get("kb_name", config.CURRENT_KB)
    store = get_store(kb_name)
    loaded = False
    if store.index is None or store.index.ntotal == 0:
        loaded = store.load()

    manifest = {}
    manifest_path = os.path.join(config.INDEX_DIR, kb_name, "file_manifest.json")
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

    indexed_files = store.get_file_list()
    file_paths = [f["path"] for f in indexed_files]

    checkpoint_info = None
    checkpoint_path = os.path.join(config.INDEX_DIR, kb_name, "index_checkpoint.json")
    if os.path.exists(checkpoint_path):
        try:
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                cp = json.load(f)
            checkpoint_info = {
                "file_list": cp.get("file_list", []),
                "completed": cp.get("completed", 0),
                "total": len(cp.get("file_list", [])),
                "status": cp.get("status", ""),
                "parse_errors": cp.get("parse_errors", 0),
                "embed_errors": cp.get("embed_errors", 0),
            }
        except Exception as e:
            _logger.warning("kb_status: checkpoint read failed: %s", e)

    return jsonify({
        "success": True,
        "data": {
            "kb_name": kb_name,
            "loaded": loaded or (store.index is not None and store.index.ntotal > 0),
            "chunks": store.count(),
            "files": file_paths,
            "file_count": len(file_paths),
            "indexed_at": manifest.get("indexed_at"),
            "python_version": sys.version,
            "checkpoint": checkpoint_info,
        }
    })


@kb_bp.route("/api/kb/files", methods=["GET"])
def list_files():
    kb_name = request.args.get("kb_name", config.CURRENT_KB)
    store = get_store(kb_name)
    if store.index is None or store.index.ntotal == 0:
        store.load()
    files = store.get_file_list()
    return jsonify({"success": True, "data": {"files": files, "count": len(files)}})


@kb_bp.route("/api/kb/files/delete", methods=["POST"])
def delete_file():
    data = request.get_json() or {}
    file_path = data.get("path", "").strip()
    kb_name = data.get("kb_name", config.CURRENT_KB)
    if not file_path:
        return jsonify({"success": False, "error": "未指定文件路径"})

    store = get_store(kb_name)
    store.load()
    removed = store.remove_by_source_files([file_path])
    store.save()
    _remove_from_manifest(kb_name, file_path)

    return jsonify({"success": True, "data": {"removed": removed, "file": file_path}})


@kb_bp.route("/api/kb/remove-file", methods=["POST"])
def remove_file_index():
    data = request.get_json() or {}
    file_path = data.get("file_path", "").strip()
    kb_name = data.get("kb_name", config.CURRENT_KB)

    if not file_path:
        return jsonify({"success": False, "error": "未指定文件路径"})

    store = get_store(kb_name)
    if store.index is None or store.index.ntotal == 0:
        store.load()
    removed = store.remove_by_source_files([file_path])
    store.save()
    _remove_from_manifest(kb_name, file_path)

    return jsonify({"success": True, "data": {"removed": removed, "file": file_path}})


@kb_bp.route("/api/kb/retry-file", methods=["POST"])
def retry_file():
    data = request.get_json() or {}
    file_path = data.get("file_path", "").strip()
    kb_name = data.get("kb_name", config.CURRENT_KB)
    api_key = data.get("api_key", config.DEEPSEEK_API_KEY)
    embedding_api_key = data.get("embedding_api_key", "")

    if not file_path:
        return jsonify({"success": False, "error": "未指定文件路径"})
    if not os.path.exists(file_path):
        return jsonify({"success": False, "error": f"文件不存在: {file_path}"})

    embed_key = pick_key(embedding_api_key)

    store = get_store(kb_name)
    if store.index is None or store.index.ntotal == 0:
        store.load()

    store.remove_by_source_files([file_path])
    store.save()
    _remove_from_manifest(kb_name, file_path)

    try:
        parsed = parse_via_subprocess(file_path)
    except Exception as e:
        return jsonify({"success": False, "error": f"解析失败: {str(e)}"})

    chunks = chunk_document(parsed)
    if not chunks:
        return jsonify({"success": False, "error": "解析结果为空，无法生成文本块"})

    chunk_dicts = [
        {
            "chunk_id": c.chunk_id,
            "text": c.text,
            "source_file": c.source_file,
            "file_type": c.file_type,
            "chunk_index": c.chunk_index,
            "total_chunks": c.total_chunks,
            "metadata": c.metadata,
        }
        for c in chunks
    ]

    vectors = store.embed_chunks(chunk_dicts, embed_key)
    if vectors is None:
        return jsonify({"success": False, "error": "嵌入 API 返回空"})

    store.add_embedded_chunks(chunk_dicts, vectors)
    store.save()

    file_hash = ""
    try:
        with open(file_path, "rb") as fh:
            file_hash = hashlib.md5(fh.read()).hexdigest()
    except Exception:
        pass

    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            # Corrupt manifest — try backup, don't silently lose data
            import shutil as _shutil
            bak_path = manifest_path + ".bak"
            try:
                _shutil.copy2(manifest_path, bak_path)
            except Exception:
                pass
            manifest = {"files": [], "_corrupt_backup": bak_path}
    else:
        manifest = {"files": []}

    manifest.setdefault("files", [])
    manifest["files"].append({"path": file_path, "hash": file_hash})
    os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # Clean up failed-files record so the UI error entry disappears after retry
    remove_file_from_failed(kb_name, file_path)

    return jsonify({
        "success": True,
        "data": {
            "file": os.path.basename(file_path),
            "chunks": len(chunks),
            "message": "文件重新解析并索引成功",
        }
    })


@kb_bp.route("/api/kb/clear", methods=["POST"])
def clear_index():
    kb_name = _get_kb_name()
    store = get_store(kb_name)
    store.clear()
    kb_dir = os.path.join(config.INDEX_DIR, kb_name)
    for fname in ["file_manifest.json", "index_checkpoint.json", "dedup_cache.json", "parsed_chunks.json", "failed_files.json"]:
        fpath = os.path.join(kb_dir, fname)
        if os.path.exists(fpath):
            try:
                os.remove(fpath)
            except Exception as e:
                _logger.warning("clear_index: failed to remove %s: %s", fpath, e)
    return jsonify({"success": True, "data": {"message": f"「{kb_name}」索引已清除"}})


# ── Open File ──────────────────────────────────────────

@kb_bp.route("/api/kb/open-file", methods=["POST"])
def open_file():
    data = request.get_json() or {}
    file_path = data.get("path", "")
    if not file_path or not os.path.exists(file_path):
        return jsonify({"success": False, "error": "文件不存在或路径无效"})
    # Path traversal protection
    file_path = os.path.realpath(file_path)
    try:
        if sys.platform == "win32":
            os.startfile(file_path)
        elif sys.platform == "darwin":
            import subprocess
            subprocess.run(["open", file_path])
        else:
            import subprocess
            subprocess.run(["xdg-open", file_path])
        return jsonify({"success": True, "data": {"message": "文件已打开"}})
    except Exception as e:
        return jsonify({"success": False, "error": f"打开文件失败: {str(e)}"})
