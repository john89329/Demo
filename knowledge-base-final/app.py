import os
import sys
import json
import hashlib
import shutil
import threading
import time
import multiprocessing
import logging
from logging.handlers import RotatingFileHandler

# Suppress library console windows on Windows — must run before any OCR import
os.environ.setdefault("GLOG_minloglevel", "3")

from flask import Flask, request, jsonify, render_template

import config
from services.document_parser import parse_file, ParsedDocument
from services.text_chunker import chunk_document
from services.vector_store import get_store
from services.rag_qa import ask_question
from services.material_gen import generate_material, TEMPLATES
from services.summarizer import summarize_documents
from services.audit_log import (
    get_or_create_active_session, create_new_session, switch_session, delete_session,
    list_sessions, get_session, set_topic, export_session_markdown, save_sessions,
)

# ── Logging setup ────────────────────────────────────────
_log_format = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_file_handler = RotatingFileHandler(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.log"),
    maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8",
)
_file_handler.setFormatter(_log_format)
_file_handler.setLevel(logging.DEBUG)

_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setFormatter(_log_format)
_console_handler.setLevel(logging.INFO)

_logger = logging.getLogger("kb")
_logger.setLevel(logging.DEBUG)
_logger.addHandler(_file_handler)
_logger.addHandler(_console_handler)

# Also capture Flask's own logs
logging.getLogger("werkzeug").handlers = []
logging.getLogger("werkzeug").addHandler(_file_handler)

app = Flask(__name__, template_folder="templates")

_chat_histories = {}  # kb_name -> list
_chat_histories_lock = threading.Lock()
LIBS_FILE = os.path.join(config.INDEX_DIR, "libraries.json")
_indexing_status = {}  # kb_name -> status dict
_indexing_status_lock = threading.Lock()
_indexing_stop_events = {}  # kb_name -> threading.Event, set to request stop
_indexing_stop_events_lock = threading.Lock()


def _default_index_status():
    return {"running": False, "progress": "", "total": 0, "current": 0, "errors": [], "file_statuses": []}


def _get_index_status(kb_name):
    with _indexing_status_lock:
        if kb_name not in _indexing_status:
            _indexing_status[kb_name] = _default_index_status()
        return _indexing_status[kb_name]


def _ensure_libraries():
    os.makedirs(config.INDEX_DIR, exist_ok=True)
    if not os.path.exists(LIBS_FILE):
        _save_libraries([config.CURRENT_KB])


def _load_libraries():
    try:
        with open(LIBS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                libs = data
            else:
                libs = data.get("libraries", [])
        # Normalize CURRENT_KB to match actual library case
        found = _find_kb(config.CURRENT_KB, libs)
        if found:
            config.CURRENT_KB = found
        return libs
    except Exception:
        return [config.CURRENT_KB]


def _save_libraries(libs):
    os.makedirs(config.INDEX_DIR, exist_ok=True)
    with open(LIBS_FILE, "w", encoding="utf-8") as f:
        json.dump({"libraries": libs}, f, ensure_ascii=False, indent=2)


def _get_kb_name():
    if request.method == "GET":
        name = request.args.get("kb_name", config.CURRENT_KB)
    else:
        data = request.get_json(silent=True) or {}
        name = data.get("kb_name", config.CURRENT_KB)
    return _resolve_kb(name)


def _find_kb(name, libs):
    """Case-insensitive library name lookup. Returns the actual cased name or None."""
    name_lower = name.strip().lower()
    for lib in libs:
        if lib.lower() == name_lower:
            return lib
    return None


def _resolve_kb(name):
    """Resolve a kb_name parameter to the actual cased library name."""
    libs = _load_libraries()
    found = _find_kb(name, libs)
    return found if found else name


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/diag", methods=["GET"])
def diag_info():
    """Diagnostic endpoint: check Python environment."""
    import subprocess
    info = {
        "sys.executable": sys.executable,
        "sys.version": sys.version,
        "sys.path": sys.path[:5],
        "cwd": os.getcwd(),
    }
    # Test subprocess
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
    return jsonify({"success": True, "data": info})


# ── Library management ─────────────────────────────────


@app.route("/api/kb/libraries", methods=["GET"])
def list_libraries():
    _ensure_libraries()
    libs = _load_libraries()
    return jsonify({"success": True, "data": {"libraries": libs, "current": config.CURRENT_KB}})


@app.route("/api/kb/libraries/create", methods=["POST"])
def create_library():
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"success": False, "error": "请提供知识库名称"})
    if "/" in name or "\\" in name or name in (".", ".."):
        return jsonify({"success": False, "error": "名称包含非法字符"})

    _ensure_libraries()
    libs = _load_libraries()
    if _find_kb(name, libs):
        return jsonify({"success": False, "error": f"知识库「{name}」已存在"})
    libs.append(name)
    _save_libraries(libs)
    os.makedirs(os.path.join(config.INDEX_DIR, name), exist_ok=True)
    return jsonify({"success": True, "data": {"message": f"知识库「{name}」已创建", "libraries": libs}})


@app.route("/api/kb/libraries/delete", methods=["POST"])
def delete_library():
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    _ensure_libraries()
    libs = _load_libraries()
    actual = _find_kb(name, libs)
    if not actual:
        return jsonify({"success": False, "error": f"知识库「{name}」不存在"})
    if len(libs) <= 1:
        return jsonify({"success": False, "error": "至少保留一个知识库"})
    libs.remove(actual)
    _save_libraries(libs)
    kb_dir = os.path.join(config.INDEX_DIR, actual)
    if os.path.exists(kb_dir):
        import shutil
        shutil.rmtree(kb_dir, ignore_errors=True)
    if config.CURRENT_KB.lower() == actual.lower():
        config.CURRENT_KB = libs[0]
    return jsonify({"success": True, "data": {"message": f"知识库「{name}」已删除", "libraries": libs, "current": config.CURRENT_KB}})


@app.route("/api/kb/libraries/switch", methods=["POST"])
def switch_library():
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    _ensure_libraries()
    libs = _load_libraries()
    actual = _find_kb(name, libs)
    if not actual:
        return jsonify({"success": False, "error": f"知识库「{name}」不存在"})
    config.CURRENT_KB = actual
    return jsonify({"success": True, "data": {"message": f"已切换到「{name}」", "current": name}})


# ── Scan ────────────────────────────────────────────────


@app.route("/api/kb/scan", methods=["POST"])
def scan_directory():
    data = request.get_json() or {}
    directory = data.get("path", "")
    if not directory or not os.path.isdir(directory):
        return jsonify({"success": False, "error": "路径无效或不存在"})
    files = []
    for root, dirs, fnames in os.walk(directory):
        for fname in fnames:
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


# ── Index ───────────────────────────────────────────────



def _get_python_exe():
    """Return python.exe for subprocess launching.

    Prefer the venv311 Python so that subprocess workers have access to
    rapidocr, pymupdf, and all other installed packages. Falls back to
    the current process's Python if venv311 is not found.
    """
    root_dir = os.path.dirname(os.path.abspath(__file__))
    venv_python = os.path.join(root_dir, "venv311", "Scripts", "python.exe")
    if os.path.exists(venv_python):
        return venv_python
    exe = sys.executable
    if exe.endswith("pythonw.exe"):
        exe = exe[:-len("pythonw.exe")] + "python.exe"
    return exe


def _parse_via_subprocess(fpath, skip_ocr=False, timeout=None):
    """Parse a file in a subprocess via parse_worker.py. Returns ParsedDocument or raises."""
    import subprocess
    import json

    root_dir = os.path.dirname(os.path.abspath(__file__))
    worker_path = os.path.join(root_dir, "parse_worker.py")

    if not os.path.exists(worker_path):
        raise RuntimeError(f"parse_worker.py 不存在: {worker_path}")

    python_exe = _get_python_exe()
    cmd = [python_exe, worker_path, fpath]
    if skip_ocr:
        cmd.append("--skip-ocr")
    timeout_val = timeout or config.PARSE_TIMEOUT

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            encoding="utf-8",
            timeout=timeout_val,
            cwd=root_dir,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as e:
        stderr_raw = e.stderr
        if isinstance(stderr_raw, bytes):
            stderr_raw = stderr_raw.decode("utf-8", errors="replace")
        stderr_tail = stderr_raw[-500:] if stderr_raw else ""
        raise RuntimeError(
            f"解析超时（>{timeout_val}秒）。"
            f"手动测试: python parse_worker.py \"{os.path.basename(fpath)}\""
            f"{' --skip-ocr' if skip_ocr else ''}"
            f"\nstderr: {stderr_tail or '无'}"
        )

    stdout_text = proc.stdout.strip() if proc.stdout else ""
    stderr_text = proc.stderr.strip()[:800] if proc.stderr else ""

    if proc.returncode != 0 or not stdout_text:
        raise RuntimeError(
            f"子进程退出码 {proc.returncode}\n"
            f"Python: {python_exe}\n"
            f"stdout: {stdout_text[:500] or '空'}\n"
            f"stderr: {stderr_text or '无'}\n"
            f"手动测试: cd \"{root_dir}\" && python parse_worker.py \"{os.path.basename(fpath)}\""
        )

    try:
        result, _ = json.JSONDecoder().raw_decode(stdout_text)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"JSON解析失败（退出码 {proc.returncode}）：{e}\n"
            f"stdout: {stdout_text[:500]}\nstderr: {stderr_text}"
        )

    if result.get("status") != "ok":
        tb = result.get("traceback", "")
        raise RuntimeError(f"{result.get('error', '未知错误')}\n{tb[:300]}")

    data = result["data"]
    return ParsedDocument(
        file_path=data["file_path"],
        file_name=data["file_name"],
        file_type=data["file_type"],
        content=data["content"],
        pages=data.get("pages", []),
        metadata=data.get("metadata", {}),
    )


@app.route("/api/kb/index", methods=["POST"])
def build_index():
    data = request.get_json() or {}
    files = data.get("files", [])
    kb_name = data.get("kb_name", config.CURRENT_KB)

    if not files:
        return jsonify({"success": False, "error": "没有提供文件列表"})
    api_key = data.get("api_key", config.DEEPSEEK_API_KEY)
    if not api_key:
        return jsonify({"success": False, "error": "请先配置 DeepSeek API 密钥"})

    # Parse embedding keys — supports comma-separated list for parallel indexing
    embedding_api_key = data.get("embedding_api_key", "")
    embed_keys = [k.strip() for k in embedding_api_key.split(",") if k.strip()] if embedding_api_key else []
    if embed_keys:
        config.EMBEDDING_API_KEY = embed_keys[0]  # set first key as global default
    else:
        embed_keys = [api_key]  # fallback to chat key

    status = _get_index_status(kb_name)
    if status.get("running"):
        return jsonify({"success": False, "error": f"知识库「{kb_name}」正在索引中，请稍候"})

    status.clear()
    status.update({"running": True, "progress": "初始化...", "total": len(files), "current": 0, "errors": []})

    def index_task(kb=kb_name, flist=files):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from services.document_parser import parse_file
        from services.text_chunker import chunk_document

        t_start = time.time()  # ── profiling: overall start

        store = get_store(kb)
        idx_status = _get_index_status(kb)
        # Stop mechanism + per-file status tracking
        with _indexing_stop_events_lock:
            stop_event = _indexing_stop_events.setdefault(kb, threading.Event())
        stop_event.clear()
        cancelled_indices = set()  # file indices cancelled by user during indexing
        idx_status["cancelled_indices"] = cancelled_indices
        idx_status["file_statuses"] = []
        total_chunks = 0
        n = len(flist)
        parse_errors = 0
        embed_errors = 0

        # ── Checkpoint path ──
        checkpoint_path = os.path.join(config.INDEX_DIR, kb, "index_checkpoint.json")
        parsed_cache_path = os.path.join(config.INDEX_DIR, kb, "parsed_chunks.json")
        CHECKPOINT_SAVE_INTERVAL = 10  # save FAISS every N files

        # Load resume state if available
        resume_from = 0
        cp_status = ""      # "parsing" or "embedding"
        if os.path.exists(checkpoint_path):
            try:
                with open(checkpoint_path, "r", encoding="utf-8") as f:
                    cp = json.load(f)
                if cp.get("file_list") == flist:
                    cp_status = cp.get("status", "")
                    if cp_status == "embedding":
                        # Embedding was in progress → resume from last embedded file
                        resume_from = cp.get("completed", 0)
                    elif cp_status == "parsing":
                        # Parsing was in progress → resume from last parsed file
                        resume_from = cp.get("parsed", 0)
                    else:
                        resume_from = cp.get("completed", 0)
                    embed_errors = cp.get("embed_errors", 0)
                    parse_errors = cp.get("parse_errors", 0)
                    total_chunks = cp.get("total_chunks", 0)
                    # Normalize old-format string errors to new structured format
                    raw_errors = cp.get("errors", [])
                    normalized = []
                    for e in raw_errors:
                        if isinstance(e, dict):
                            if "path" not in e:
                                e["path"] = ""
                            normalized.append(e)
                        else:
                            s = str(e)
                            if "跳过" in s and "重复" in s:
                                normalized.append({"type": "duplicate", "file": "", "path": "", "message": s})
                            else:
                                normalized.append({"type": "parse_error", "file": "", "path": "", "message": s})
                    idx_status["errors"] = normalized
                    _logger.info("Checkpoint: resuming from file #%d/%d (status=%s)", resume_from, n, cp_status)
            except Exception:
                pass

        # ── Crash recovery: restore backups if previous run crashed mid-pipeline ──
        index_bak = store._index_path + ".bak"
        db_bak = store._db_path + ".bak"
        if os.path.exists(index_bak) and os.path.exists(db_bak):
            if resume_from == 0:
                # Fresh start: FAISS/SQLite were corrupted by remove-before-crash
                shutil.copy2(index_bak, store._index_path)
                shutil.copy2(db_bak, store._db_path)
                store.load()
                _logger.info("Recovery: restored FAISS + SQLite from backup (previous run crashed)")
            os.remove(index_bak)
            os.remove(db_bak)

        # Write pre-index checkpoint
        os.makedirs(os.path.join(config.INDEX_DIR, kb), exist_ok=True)
        with open(checkpoint_path, "w", encoding="utf-8") as f:
            json.dump({
                "file_list": flist,
                "parsed": resume_from,
                "completed": resume_from,
                "total_chunks": total_chunks,
                "parse_errors": parse_errors,
                "embed_errors": embed_errors,
                "errors": idx_status["errors"],
                "status": "parsing",
            }, f, ensure_ascii=False)

        # ── Dedup: skip files with duplicate content (same MD5) ──
        manifest_path = os.path.join(config.INDEX_DIR, kb, "file_manifest.json")
        dedup_cache_path = os.path.join(config.INDEX_DIR, kb, "dedup_cache.json")
        _logger.info("Dedup: checking %d files for duplicates...", n)

        # Build lookup tables from manifest
        known_hashes = {}     # hash → path (for detecting duplicates across runs)
        path_hash_map = {}    # path → hash (for skipping MD5 of already-indexed files)
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    mf = json.load(f)
                for entry in mf.get("files", []):
                    if isinstance(entry, dict) and entry.get("hash") and entry.get("path"):
                        h = entry["hash"]
                        p = entry["path"]
                        known_hashes[h] = p
                        path_hash_map[p] = h
            except Exception:
                pass

        dup_count = 0
        batch_hashes = {}       # hash → path (for dedup within this batch)
        path_hash_cache = {}    # path → hash (for MD5 skip on resume)

        # Try to load dedup cache from a previous interrupted run
        if os.path.exists(dedup_cache_path):
            try:
                with open(dedup_cache_path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                if cached.get("file_list") == flist:
                    path_hash_cache = cached.get("batch_hashes", {})
                    # Rebuild hash→path for dedup
                    for p, h in path_hash_cache.items():
                        batch_hashes[h] = p
                    _logger.info("Dedup: loaded %d cached hashes, skipping recompute", len(path_hash_cache))
            except Exception:
                pass

        idx_status["progress"] = f"[{kb}] 检查重复文件..."
        for i in range(resume_from, n):
            fpath = flist[i]

            # Skip MD5 if we already know this file's hash from manifest
            if fpath in path_hash_map:
                file_hash = path_hash_map[fpath]
            elif fpath in path_hash_cache:
                # Hash already computed in a previous run (loaded from dedup cache)
                file_hash = path_hash_cache[fpath]
                # Check against known_hashes (may have been indexed since)
                if file_hash in known_hashes:
                    dup_path = known_hashes[file_hash]
                    idx_status["errors"].append({
                        "type": "duplicate", "file": os.path.basename(fpath), "path": fpath,
                        "message": f"跳过（与已索引的 {os.path.basename(dup_path)} 内容重复）"
                    })
                    flist[i] = None
                    dup_count += 1
                    idx_status["current"] = i + 1
                    continue
            else:
                try:
                    with open(fpath, "rb") as fh:
                        file_hash = hashlib.md5(fh.read()).hexdigest()
                except Exception:
                    continue
                # Persist immediately for crash recovery
                path_hash_cache[fpath] = file_hash

            if file_hash in known_hashes:
                dup_path = known_hashes[file_hash]
                idx_status["errors"].append({
                    "type": "duplicate", "file": os.path.basename(fpath), "path": fpath,
                    "message": f"跳过（与已索引的 {os.path.basename(dup_path)} 内容重复）"
                })
                flist[i] = None
                dup_count += 1
                idx_status["current"] = i + 1
            elif file_hash in batch_hashes:
                dup_path = batch_hashes[file_hash]
                idx_status["errors"].append({
                    "type": "duplicate", "file": os.path.basename(fpath), "path": fpath,
                    "message": f"跳过（与本批次 {os.path.basename(dup_path)} 内容重复）"
                })
                flist[i] = None
                dup_count += 1
                idx_status["current"] = i + 1
            else:
                batch_hashes[file_hash] = fpath
            idx_status["current"] = i + 1

        # Save dedup cache for crash recovery (store path→hash for fast MD5 skip on resume)
        try:
            with open(dedup_cache_path, "w", encoding="utf-8") as f:
                json.dump({"file_list": flist, "batch_hashes": path_hash_cache, "dup_count": dup_count}, f)
        except Exception:
            pass

        # Initialize per-file status tracking
        idx_status["file_statuses"] = []
        for i, fpath in enumerate(flist):
            if fpath is None:
                idx_status["file_statuses"].append({"path": "", "name": "", "status": "duplicate", "chunks": 0})
            else:
                idx_status["file_statuses"].append({"path": fpath, "name": os.path.basename(fpath), "status": "pending", "chunks": 0})
        # Backfill duplicate file names from the error list
        dup_entries = [e for e in idx_status["errors"] if e.get("type") == "duplicate"]
        dup_idx = 0
        for fs in idx_status["file_statuses"]:
            if fs["status"] == "duplicate" and dup_idx < len(dup_entries):
                fs["name"] = dup_entries[dup_idx].get("file", "")
                fs["path"] = dup_entries[dup_idx].get("path", "")
                dup_idx += 1

        if dup_count:
            _logger.info("Dedup: skipped %d duplicate files", dup_count)

        effective_n = n - dup_count  # actual number of files to process
        idx_status["total"] = effective_n
        idx_status["current"] = 0

        # ── Pipeline: parse → queue → embed (parallel phases) ──
        from queue import Queue
        import concurrent.futures as cf

        parsed_batches = [None] * n
        parse_times = []  # (fname, seconds) for slow-file identification

        # Try to load parsed chunks from cache (for crash recovery)
        loaded_from_cache = False
        if os.path.exists(parsed_cache_path) and cp_status in ("parsing", "embedding", "pipelining"):
            try:
                with open(parsed_cache_path, "r", encoding="utf-8") as f:
                    parsed_batches = json.load(f)
                loaded_from_cache = True
                already_parsed = len([x for x in parsed_batches if x is not None])
                _logger.info("Checkpoint: loaded %d parsed files from cache (status=%s)", already_parsed, cp_status)
            except Exception:
                pass

        # Determine parse/embed resume positions (raw indices into flist)
        parsed_count = resume_from
        embedded_count = resume_from
        if loaded_from_cache:
            parsed_count = max(parsed_count, cp.get("parsed", resume_from))
            if cp_status in ("embedding", "pipelining"):
                embedded_count = max(embedded_count, cp.get("completed", resume_from))

        # ── Backup FAISS + SQLite before modifying (crash safety) ──
        index_bak = store._index_path + ".bak"
        db_bak = store._db_path + ".bak"
        if os.path.exists(store._index_path):
            shutil.copy2(store._index_path, index_bak)
        if os.path.exists(store._db_path):
            shutil.copy2(store._db_path, db_bak)

        # Batch-remove old chunks for files being re-indexed (before any embedding starts)
        t_remove_start = time.time()
        pending_files = [f for f in flist[resume_from:] if f is not None]
        if pending_files:
            store.remove_by_source_files(pending_files)
            store.save()
        t_remove_elapsed = time.time() - t_remove_start

        # Update checkpoint to pipelining
        with open(checkpoint_path, "r+", encoding="utf-8") as f:
            cp = json.load(f)
            cp["parsed"] = parsed_count
            cp["completed"] = embedded_count
            cp["status"] = "pipelining"
            f.seek(0)
            json.dump(cp, f, ensure_ascii=False)
            f.truncate()

        # Pick keys for embedding threads
        embed_keys_pool = embed_keys if embed_keys else [api_key]

        # Identify parsed chunks that still need embedding (for resume)
        # Walk parsed_batches in flist order; the first (embedded_count - resume_from)
        # non-None, non-empty batches were already embedded
        to_embed_now = []
        valid_before = 0
        for i in range(parsed_count):
            bi = parsed_batches[i]
            if bi is not None and len(bi) > 0 and flist[i] is not None:
                valid_before += 1
                if valid_before > embedded_count - resume_from:
                    to_embed_now.append((i, flist[i], bi))

        # Files that still need parsing
        need_parse = [(i, flist[i]) for i in range(parsed_count, n) if flist[i] is not None]

        # ── Quick OCR-need detection (PyMuPDF text scan, fast) ──
        def _needs_ocr(fpath):
            ext = os.path.splitext(fpath)[1].lower()
            if ext in ('.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.tif'):
                return True
            if ext != '.pdf':
                return False
            try:
                import fitz
                doc = fitz.open(fpath)
                for page in doc:
                    if page.get_text().strip():
                        doc.close()
                        return False
                doc.close()
                return True
            except Exception:
                return False

        ocr_files = [(i, fp) for i, fp in need_parse if _needs_ocr(fp)]
        fast_files = [(i, fp) for i, fp in need_parse if (i, fp) not in set(ocr_files)]

        ocr_count = len(ocr_files)
        fast_count = len(fast_files)
        _logger.info("Pipeline: %d fast-text + %d OCR files -> %d-thread unified pool",
                     fast_count, ocr_count, config.INDEX_WORKERS)
        idx_status["progress"] = f"[{kb}] OCR: {ocr_count}个文件, 快速: {fast_count}个文件, 开始解析..."

        t_parse_elapsed = 0.0
        total_embed_api_time = 0.0
        total_faiss_write_time = 0.0

        if not need_parse and not to_embed_now:
            # Nothing to do — everything was already embedded
            pass
        else:
            chunk_queue = Queue(maxsize=config.PIPELINE_QUEUE_SIZE)
            _SENTINEL = object()
            pipeline_lock = threading.Lock()
            save_lock = threading.Lock()  # prevents concurrent FAISS writes

            # Mutable state shared with embed workers
            pipe_state = {
                "embedded_count": embedded_count,
                "embed_errors": embed_errors,
                "total_chunks": total_chunks,
                "total_embed_api_time": 0.0,
                "total_faiss_write_time": 0.0,
            }

            # ── Embed worker: consume from queue, embed, write to FAISS ──
            parsing_done = threading.Event()

            def embed_worker(key):
                while True:
                    try:
                        item = chunk_queue.get(timeout=10)
                    except Exception:
                        if parsing_done.is_set():
                            break
                        continue
                    if item is _SENTINEL:
                        chunk_queue.task_done()
                        break
                    i, fpath, chunks = item
                    fname = os.path.basename(fpath)
                    # Check if user cancelled this file
                    if i in cancelled_indices:
                        if i < len(idx_status.get("file_statuses", [])):
                            idx_status["file_statuses"][i]["status"] = "cancelled"
                        with pipeline_lock:
                            pipe_state["embedded_count"] += 1
                        chunk_queue.task_done()
                        continue
                    # Update file status: embedding
                    if i < len(idx_status.get("file_statuses", [])):
                        idx_status["file_statuses"][i]["status"] = "embedding"
                    try:
                        t0 = time.time()
                        vectors = store.embed_chunks(chunks, key)
                        api_time = time.time() - t0
                        with pipeline_lock:
                            pipe_state["total_embed_api_time"] += api_time
                        if vectors is not None:
                            t1 = time.time()
                            added = store.add_embedded_chunks(chunks, vectors)
                            faiss_time = time.time() - t1
                            with pipeline_lock:
                                pipe_state["total_faiss_write_time"] += faiss_time
                                pipe_state["total_chunks"] += added
                                pipe_state["embedded_count"] += 1
                                ec = pipe_state["embedded_count"]
                            if i < len(idx_status.get("file_statuses", [])):
                                idx_status["file_statuses"][i]["status"] = "embedded"
                        else:
                            with pipeline_lock:
                                pipe_state["embedded_count"] += 1
                                pipe_state["embed_errors"] += 1
                                ec = pipe_state["embedded_count"]
                            idx_status["errors"].append({
                                "type": "embed_error", "file": fname, "path": fpath,
                                "message": "嵌入 API 返回空"
                            })

                        # Save FAISS + checkpoint + parsed cache periodically
                        if ec % CHECKPOINT_SAVE_INTERVAL == 0:
                            if save_lock.acquire(blocking=False):
                                try:
                                    store.save()
                                finally:
                                    save_lock.release()
                            try:
                                with open(checkpoint_path, "r+", encoding="utf-8") as f:
                                    cp2 = json.load(f)
                                    cp2["completed"] = ec
                                    cp2["total_chunks"] = pipe_state["total_chunks"]
                                    cp2["embed_errors"] = pipe_state["embed_errors"]
                                    cp2["parse_errors"] = parse_errors
                                    cp2["errors"] = idx_status["errors"]
                                    cp2["status"] = "pipelining"
                                    f.seek(0)
                                    json.dump(cp2, f, ensure_ascii=False)
                                    f.truncate()
                            except Exception:
                                pass
                            try:
                                with open(parsed_cache_path, "w", encoding="utf-8") as f:
                                    json.dump(parsed_batches, f, ensure_ascii=False)
                            except Exception:
                                pass

                        # Update progress
                        idx_status["current"] = ec
                        idx_status["progress"] = (
                            f"[{kb}] 解析 {parsed_count}/{effective_n}，"
                            f"嵌入 {ec}/{effective_n}，"
                            f"队列: {chunk_queue.qsize()}"
                        )
                    except Exception as e:
                        import traceback
                        traceback.print_exc()
                        with pipeline_lock:
                            pipe_state["embedded_count"] += 1
                            pipe_state["embed_errors"] += 1
                            ec = pipe_state["embedded_count"]
                        if i < len(idx_status.get("file_statuses", [])):
                            idx_status["file_statuses"][i]["status"] = "error"
                            idx_status["file_statuses"][i]["error"] = str(e)
                        idx_status["errors"].append({
                            "type": "embed_error", "file": fname, "path": fpath,
                            "message": str(e)
                        })
                    finally:
                        chunk_queue.task_done()

            # ── Parse function: parse file, chunk, enqueue ──
            def parse_and_enqueue(fpath, i, skip_ocr=False):
                nonlocal parsed_count, parse_errors
                t0 = time.time()
                fname = os.path.basename(fpath)
                is_ocr = not skip_ocr
                tag = "[OCR]" if is_ocr else "[FAST]"
                file_timeout = config.OCR_PARSE_TIMEOUT if is_ocr else config.PARSE_TIMEOUT
                _logger.info("%s 开始解析: %s (超时: %ds)", tag, fname, file_timeout)
                # Update file status: parsing
                if i < len(idx_status.get("file_statuses", [])):
                    idx_status["file_statuses"][i]["status"] = "parsing"
                    idx_status["file_statuses"][i]["started_at"] = time.time()
                # Check if user cancelled this file
                if i in cancelled_indices:
                    _logger.info("%s 已取消: %s", tag, fname)
                    if i < len(idx_status.get("file_statuses", [])):
                        idx_status["file_statuses"][i]["status"] = "cancelled"
                    with pipeline_lock:
                        parsed_count += 1
                        pipe_state["embedded_count"] += 1
                    return
                try:
                    parsed = _parse_via_subprocess(fpath, skip_ocr=skip_ocr, timeout=file_timeout)

                    chunks = chunk_document(parsed)
                    elapsed = time.time() - t0
                    _logger.info("%s 解析完成: %s (%.1fs, %d chunks)", tag, fname, elapsed, len(chunks))
                    parse_times.append((fname, elapsed, len(chunks)))
                    chunk_dicts = []
                    if chunks:
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
                    parsed_batches[i] = chunk_dicts
                    if i < len(idx_status.get("file_statuses", [])):
                        idx_status["file_statuses"][i]["status"] = "parsed"
                        idx_status["file_statuses"][i]["chunks"] = len(chunk_dicts)
                    with pipeline_lock:
                        parsed_count += 1
                    if chunk_dicts:
                        try:
                            chunk_queue.put((i, fpath, chunk_dicts), timeout=30)
                        except Exception:
                            _logger.warning("Pipeline: queue full for 30s, skipping embedding for %s", fname)
                            idx_status["errors"].append({
                                "type": "embed_error", "file": fname, "path": fpath,
                                "message": "队列满超时，跳过嵌入"
                            })
                            with pipeline_lock:
                                pipe_state["embedded_count"] += 1
                                pipe_state["embed_errors"] += 1
                    else:
                        # No text extracted (e.g. image-only PDF) — still count as done
                        with pipeline_lock:
                            pipe_state["embedded_count"] += 1
                except Exception as e:
                    parse_errors += 1
                    import traceback as _tb
                    # Log stderr to help diagnose subprocess failures
                    stderr_info = ""
                    if hasattr(e, "__cause__") and e.__cause__:
                        stderr_info = str(e.__cause__)[:200]
                    elif hasattr(e, "stderr") and e.stderr:
                        stderr_info = str(e.stderr)[:200]
                    _tb.print_exc()
                    error_msg = str(e)
                    if stderr_info and stderr_info not in error_msg:
                        _logger.warning("Parse: %s stderr: %s", fname, stderr_info)
                    _logger.error("Parse: %s failed: %s", fname, error_msg)
                    parsed_batches[i] = []
                    if i < len(idx_status.get("file_statuses", [])):
                        idx_status["file_statuses"][i]["status"] = "error"
                        idx_status["file_statuses"][i]["error"] = error_msg
                    idx_status["errors"].append({
                        "type": "parse_error", "file": fname, "path": fpath,
                        "message": error_msg
                    })
                    with pipeline_lock:
                        parsed_count += 1
                        pipe_state["embedded_count"] += 1
                        pipe_state["embed_errors"] += 1

            # ── Start embed pool first, then pre-queue, then parse pool ──
            idx_status["progress"] = f"[{kb}] 管道: 解析+嵌入并行 ({config.INDEX_WORKERS}→{config.EMBED_WORKERS} 线程)..."

            # Start embed workers BEFORE pre-queuing (avoid bounded-queue deadlock)
            embed_executor = ThreadPoolExecutor(max_workers=config.EMBED_WORKERS)
            embed_futures = [
                embed_executor.submit(embed_worker, embed_keys_pool[j % len(embed_keys_pool)])
                for j in range(config.EMBED_WORKERS)
            ]

            # Pre-queue cached chunks that need re-embedding
            for i, fpath, chunks in to_embed_now:
                try:
                    chunk_queue.put((i, fpath, chunks), timeout=60)
                except Exception:
                    fname = os.path.basename(fpath)
                    _logger.warning("Pipeline: queue full, skipping pre-queue for %s", fname)
                    idx_status["errors"].append({
                        "type": "embed_error", "file": fname, "path": fpath,
                        "message": "预排队超时，跳过嵌入"
                    })

            t_parse_start = time.time()

            # Unified pool: submit fast files first so they complete quickly,
            # then OCR files. All threads can handle any file type.
            if fast_files or ocr_files:
                parse_future_map = {}
                unified_pool = ThreadPoolExecutor(max_workers=config.INDEX_WORKERS)

                # Fast files first (prioritized), then OCR files
                for i, fpath in fast_files:
                    f = unified_pool.submit(parse_and_enqueue, fpath, i, True)
                    parse_future_map[f] = (i, os.path.basename(fpath), fpath)

                for i, fpath in ocr_files:
                    f = unified_pool.submit(parse_and_enqueue, fpath, i, False)
                    parse_future_map[f] = (i, os.path.basename(fpath), fpath)

                parse_remaining = set(parse_future_map.keys())

                try:
                    while parse_remaining:
                        if stop_event.is_set():
                            _logger.info("Parse: stop signal received, saving checkpoint and exiting")
                            for f in parse_remaining:
                                f.cancel()
                            for f in parse_remaining:
                                ri, _, _ = parse_future_map[f]
                                if ri < len(idx_status.get("file_statuses", [])):
                                    if idx_status["file_statuses"][ri]["status"] in ("parsing", "pending"):
                                        idx_status["file_statuses"][ri]["status"] = "pending"
                            break

                        completed, parse_remaining = cf.wait(
                            parse_remaining, timeout=15, return_when=cf.FIRST_COMPLETED
                        )
                        for future in completed:
                            i, fname, _ = parse_future_map[future]
                            try:
                                future.result()
                            except Exception:
                                pass  # error already handled in parse_and_enqueue

                        # Update progress with active file names
                        active = [parse_future_map[f][1] for f in parse_remaining if f in parse_future_map]
                        active_hint = f"，解析中: {', '.join(active[:3])}" if active else ""
                        idx_status["progress"] = (
                            f"[{kb}] 解析 {parsed_count}/{effective_n}，"
                            f"嵌入 {pipe_state['embedded_count']}/{effective_n}，"
                            f"队列: {chunk_queue.qsize()}{active_hint}"
                        )
                finally:
                    unified_pool.shutdown(wait=True)

            t_parse_end = time.time()
            t_parse_elapsed = t_parse_end - t_parse_start
            cancelled = effective_n - parsed_count
            _logger.info("Parse: 阶段完成: parsed=%d/%d, errors=%d, cancelled=%d, elapsed=%.1fs",
                  parsed_count, effective_n, parse_errors, cancelled, t_parse_elapsed)

            # Signal embed workers that parsing is done
            parsing_done.set()
            for _ in range(config.EMBED_WORKERS):
                try:
                    chunk_queue.put(_SENTINEL, timeout=30)
                except Exception:
                    pass  # workers will exit via parsing_done check anyway

            # Wait for all embedding to complete (with timeout to prevent hang)
            embed_timeout = max(300, effective_n * 15) + 120  # 15s per file + 2min grace
            try:
                for future in cf.as_completed(embed_futures, timeout=embed_timeout):
                    try:
                        future.result()
                    except Exception:
                        pass
                    # Update progress during embed wait phase
                    ec_done = pipe_state["embedded_count"]
                    if ec_done >= effective_n:
                        idx_status["progress"] = f"[{kb}] 解析 {effective_n}/{effective_n}，嵌入 {ec_done}/{effective_n}，即将完成..."
                    else:
                        idx_status["progress"] = f"[{kb}] 解析 {effective_n}/{effective_n}，嵌入 {ec_done}/{effective_n}，等待嵌入线程退出..."
            except cf.TimeoutError:
                incomplete = [f for f in embed_futures if not f.done()]
                _logger.warning("Pipeline: embedding timeout after %ds — %d/%d workers did not finish",
                              embed_timeout, len(incomplete), config.EMBED_WORKERS)

            embed_executor.shutdown(wait=False)
            _logger.info("Pipeline: all embed workers done, saving index...")

            # Deferred PQ training (avoids blocking embed workers during indexing)
            store.train_pq_if_needed()

            # Pull final state from pipe_state
            embed_errors = pipe_state["embed_errors"]
            total_chunks = pipe_state["total_chunks"]
            total_embed_api_time = pipe_state["total_embed_api_time"]
            total_faiss_write_time = pipe_state["total_faiss_write_time"]
            embedded_count = pipe_state["embedded_count"]

            # Persist parsed chunks for crash recovery
            try:
                with open(parsed_cache_path, "w", encoding="utf-8") as f:
                    json.dump(parsed_batches, f, ensure_ascii=False)
            except Exception:
                pass

        store.save()

        # ── Profiling summary ──
        t_total = time.time() - t_start
        t_pipeline = t_total - t_remove_elapsed
        total_parse_chunks = sum(n_chunks for _, _, n_chunks in parse_times)
        # ── Profiling summary ──
        total_parse_chunks = sum(n_chunks for _, _, n_chunks in parse_times)
        _profile_lines = [
            "====== 索引性能分析 =====",
            f"总耗时: {t_total:.1f}s ({t_total/60:.1f}min)",
            "---",
            f"解析: {t_parse_elapsed:.1f}s ({len(parse_times)}文件, {total_parse_chunks}块)",
            f"批量删除: {t_remove_elapsed:.1f}s",
        ]
        if total_chunks > 0:
            _profile_lines.append(
                f"API调用: {total_embed_api_time:.1f}s (并行{config.EMBED_WORKERS}线程, "
                f"{total_embed_api_time/total_chunks*1000:.0f}ms/chunk)")
        else:
            _profile_lines.append(f"API调用: {total_embed_api_time:.1f}s")
        _profile_lines.append(f"FAISS写: {total_faiss_write_time:.1f}s")
        if t_parse_elapsed > 0 and total_embed_api_time > 0:
            overlap_saved = (t_parse_elapsed + total_embed_api_time) - t_pipeline
            _profile_lines.append(f"重叠节省: {overlap_saved:.1f}s (并行收益)")
        _profile_lines.append("---")
        if parse_times:
            parse_times.sort(key=lambda x: -x[1])
            _profile_lines.append("最慢解析 TOP3: " + ", ".join(
                f"{fname}({t:.1f}s/{nchunks}块)" for fname, t, nchunks in parse_times[:3]
            ))
        _profile_lines.append("================================")
        _logger.info("Profile:\n%s", "\n".join(f"  {line}" for line in _profile_lines))

        # ── Write manifest (with MD5 hashes for dedup) ──
        manifest_path = os.path.join(config.INDEX_DIR, kb, "file_manifest.json")
        existing_by_path = {}
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    old_files = json.load(f).get("files", [])
                for entry in old_files:
                    if isinstance(entry, dict):
                        existing_by_path[entry["path"]] = entry
                    else:
                        existing_by_path[entry] = {"path": entry}  # legacy format
            except Exception:
                pass

        # Only add fully-indexed files (status "embedded") to the manifest.
        # Files that were stopped mid-pipeline (pending/parsing/parsed/error) are
        # excluded so they will be retried on the next indexing run.
        embedded_paths = set()
        for fs in idx_status.get("file_statuses", []):
            if fs.get("status") == "embedded":
                embedded_paths.add(fs.get("path", ""))

        for f in flist:
            if f is not None and f not in existing_by_path and f in embedded_paths:
                h = ""
                try:
                    with open(f, "rb") as fh:
                        h = hashlib.md5(fh.read()).hexdigest()
                except Exception:
                    pass
                existing_by_path[f] = {"path": f, "hash": h}

        manifest_files = list(existing_by_path.values())
        os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump({"files": manifest_files, "indexed_at": time.time()}, f)

        # Clean up checkpoint, caches, and backups
        for p in [checkpoint_path, parsed_cache_path, dedup_cache_path, index_bak, db_bak]:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

        idx_status["running"] = False
        idx_status["progress"] = (
            f"[{kb}] 完成: {n - dup_count} 个文件（跳过 {dup_count} 个重复）, {total_chunks} 个文本块, "
            f"解析错误: {parse_errors}, 嵌入错误: {embed_errors}"
        )

        # Persist failed files for the /api/kb/failed-files endpoint
        _save_failed_files(kb, idx_status)

    thread = threading.Thread(target=index_task, daemon=True)
    thread.start()
    return jsonify({"success": True, "data": {"message": "索引已开始", "total_files": len(files), "kb_name": kb_name}})


@app.route("/api/kb/indexing-status", methods=["GET"])
def get_indexing_status():
    kb_name = request.args.get("kb_name", config.CURRENT_KB)
    status = dict(_get_index_status(kb_name))
    # Convert non-serializable sets to lists for JSON
    if "cancelled_indices" in status and isinstance(status["cancelled_indices"], set):
        status["cancelled_indices"] = sorted(status["cancelled_indices"])
    return jsonify({"success": True, "data": status})


@app.route("/api/kb/failed-files", methods=["GET"])
def get_failed_files():
    """Return files that were not successfully indexed in the last run."""
    kb_name = request.args.get("kb_name", config.CURRENT_KB)
    status = _get_index_status(kb_name)

    # Use in-memory data if available, otherwise fall back to persisted file
    fss = status.get("file_statuses", [])
    errors = status.get("errors", [])
    if not fss:
        # Try loading from disk
        cached = _load_failed_files(kb_name)
        if cached:
            return jsonify({"success": True, "data": cached})
        return jsonify({"success": True, "data": {"total": 0, "failed": [], "groups": {}}})

    if status.get("running"):
        return jsonify({"success": False, "error": "索引仍在进行中"})

    result = _build_failed_groups(fss, errors)
    return jsonify({"success": True, "data": result})


def _build_failed_groups(fss, errors):
    """Build grouped failed-files structure from file_statuses and errors."""
    groups = {"parse_error": [], "embed_error": [], "duplicate": [], "cancelled": []}
    for idx, fs in enumerate(fss):
        st = fs.get("status", "pending")
        if st == "embedded":
            continue
        entry = {
            "index": idx,
            "name": fs.get("name", ""),
            "path": fs.get("path", ""),
            "status": st,
            "error": fs.get("error", ""),
        }
        if st == "error":
            matched = False
            fp = fs.get("path", "")
            for e in errors:
                if e.get("path") == fp:
                    if e.get("type") == "embed_error":
                        groups["embed_error"].append(entry)
                    else:
                        groups["parse_error"].append(entry)
                    matched = True
                    break
            if not matched:
                groups["parse_error"].append(entry)
        elif st in ("duplicate",):
            groups["duplicate"].append(entry)
        elif st in ("cancelled", "cancelling"):
            groups["cancelled"].append(entry)
        elif st in ("pending", "parsing", "parsed", "embedding"):
            groups["parse_error"].append({**entry, "status": "incomplete", "error": "索引中断，未完成"})

    failed = []
    for g in ["parse_error", "embed_error", "duplicate", "cancelled"]:
        failed.extend(groups[g])

    return {"total": len(failed), "failed": failed, "groups": groups}


def _failed_files_path(kb_name):
    return os.path.join(config.INDEX_DIR, kb_name, "failed_files.json")


def _save_failed_files(kb_name, idx_status):
    """Persist failed files info to disk so it survives restarts."""
    fss = idx_status.get("file_statuses", [])
    errors = idx_status.get("errors", [])
    if not fss:
        return
    try:
        result = _build_failed_groups(fss, errors)
        path = _failed_files_path(kb_name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
    except Exception:
        pass


def _load_failed_files(kb_name):
    """Load persisted failed files from disk."""
    path = _failed_files_path(kb_name)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


@app.route("/api/kb/stop-indexing", methods=["POST"])
def stop_indexing():
    kb_name = _get_kb_name()
    with _indexing_stop_events_lock:
        event = _indexing_stop_events.get(kb_name)
    if event:
        event.set()
        return jsonify({"success": True, "data": {"message": "已发送停止信号，将在当前批次完成后停止"}})
    return jsonify({"success": False, "error": "没有正在进行的索引任务"})


@app.route("/api/kb/cancel-file", methods=["POST"])
def cancel_file():
    """Cancel a single file in the running index pipeline."""
    data = request.get_json() or {}
    kb_name = data.get("kb_name", config.CURRENT_KB)
    file_index = data.get("file_index")
    if file_index is None:
        return jsonify({"success": False, "error": "未指定文件索引"})
    status = _get_index_status(kb_name)
    if not status.get("running"):
        return jsonify({"success": False, "error": "没有正在进行的索引任务"})
    cancelled = status.get("cancelled_indices")
    if cancelled is None:
        return jsonify({"success": False, "error": "无法取消：索引任务不支持逐文件取消"})
    cancelled.add(int(file_index))
    # Mark in file_statuses immediately for UI feedback
    fss = status.get("file_statuses", [])
    idx = int(file_index)
    if idx < len(fss) and fss[idx]["status"] in ("pending", "parsing", "parsed", "embedding"):
        fss[idx]["status"] = "cancelling"
    return jsonify({"success": True, "data": {"message": f"已标记文件 #{file_index} 取消", "file_index": file_index}})


# ── Status / Files / Clear ──────────────────────────────


@app.route("/api/kb/status", methods=["GET"])
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

    # Use actual indexed file list from the store (SQLite), not manifest which
    # may contain files that were submitted but never successfully indexed.
    indexed_files = store.get_file_list()
    file_paths = [f["path"] for f in indexed_files]

    # Check for interrupted index (resumable checkpoint)
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
        except Exception:
            pass

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


@app.route("/api/kb/files", methods=["GET"])
def list_files():
    kb_name = request.args.get("kb_name", config.CURRENT_KB)
    store = get_store(kb_name)
    if store.index is None or store.index.ntotal == 0:
        store.load()
    files = store.get_file_list()
    return jsonify({"success": True, "data": {"files": files, "count": len(files)}})


@app.route("/api/kb/files/delete", methods=["POST"])
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

    # Clean up manifest
    manifest_path = os.path.join(config.INDEX_DIR, kb_name, "file_manifest.json")
    if os.path.exists(manifest_path):
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

    return jsonify({"success": True, "data": {"removed": removed, "file": file_path}})


@app.route("/api/kb/remove-file", methods=["POST"])
def remove_file_index():
    """Remove a single file's index entries from FAISS + SQLite + manifest."""
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

    # Remove from manifest
    manifest_path = os.path.join(config.INDEX_DIR, kb_name, "file_manifest.json")
    if os.path.exists(manifest_path):
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

    return jsonify({"success": True, "data": {"removed": removed, "file": file_path}})


@app.route("/api/kb/retry-file", methods=["POST"])
def retry_file():
    """Re-parse and re-index a single file."""
    data = request.get_json() or {}
    file_path = data.get("file_path", "").strip()
    kb_name = data.get("kb_name", config.CURRENT_KB)
    api_key = data.get("api_key", config.DEEPSEEK_API_KEY)
    embedding_api_key = data.get("embedding_api_key", "")

    if not file_path:
        return jsonify({"success": False, "error": "未指定文件路径"})
    if not os.path.exists(file_path):
        return jsonify({"success": False, "error": f"文件不存在: {file_path}"})

    embed_keys = [k.strip() for k in embedding_api_key.split(",") if k.strip()] if embedding_api_key else [api_key]

    store = get_store(kb_name)
    if store.index is None or store.index.ntotal == 0:
        store.load()

    # Remove old index entries
    store.remove_by_source_files([file_path])
    store.save()

    # Remove from manifest
    manifest_path = os.path.join(config.INDEX_DIR, kb_name, "file_manifest.json")
    if os.path.exists(manifest_path):
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

    # Re-parse
    try:
        parsed = _parse_via_subprocess(file_path)
    except Exception as e:
        return jsonify({"success": False, "error": f"解析失败: {str(e)}"})

    # Re-chunk
    chunks = chunk_document(parsed)
    if not chunks:
        return jsonify({"success": False, "error": "解析结果为空，无法生成文本块"})

    # Re-embed
    from services.vector_store import VectorStore
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

    embed_key = embed_keys[0]
    vectors = store.embed_chunks(chunk_dicts, embed_key)
    if vectors is None:
        return jsonify({"success": False, "error": "嵌入 API 返回空"})

    store.add_embedded_chunks(chunk_dicts, vectors)
    store.save()

    # Update manifest
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
            manifest = {"files": []}
    else:
        manifest = {"files": []}

    manifest["files"].append({"path": file_path, "hash": file_hash})
    os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return jsonify({
        "success": True,
        "data": {
            "file": os.path.basename(file_path),
            "chunks": len(chunks),
            "message": "文件重新解析并索引成功",
        }
    })


@app.route("/api/kb/clear", methods=["POST"])
def clear_index():
    kb_name = _get_kb_name()
    store = get_store(kb_name)
    store.clear()
    # Also clean up manifest and checkpoint
    kb_dir = os.path.join(config.INDEX_DIR, kb_name)
    for fname in ["file_manifest.json", "index_checkpoint.json", "dedup_cache.json", "parsed_chunks.json", "failed_files.json"]:
        fpath = os.path.join(kb_dir, fname)
        if os.path.exists(fpath):
            try:
                os.remove(fpath)
            except Exception:
                pass
    with _chat_histories_lock:
        _chat_histories[kb_name] = []
    _save_chat_history(kb_name)
    return jsonify({"success": True, "data": {"message": f"「{kb_name}」索引已清除"}})


# ── Open File ──────────────────────────────────────────


@app.route("/api/kb/open-file", methods=["POST"])
def open_file():
    data = request.get_json() or {}
    file_path = data.get("path", "")
    if not file_path or not os.path.exists(file_path):
        return jsonify({"success": False, "error": "文件不存在或路径无效"})
    kb_name = data.get("kb_name", config.CURRENT_KB)
    store = get_store(kb_name)
    if store.index is None or store.index.ntotal == 0:
        store.load()
    # Verify file is in this store's index
    all_sources = set()
    for f in store.get_file_list():
        all_sources.add(f["path"])
    if file_path not in all_sources:
        return jsonify({"success": False, "error": "文件不在当前知识库的索引中"})
    try:
        os.startfile(file_path)
        return jsonify({"success": True, "data": {"message": "文件已打开"}})
    except Exception as e:
        return jsonify({"success": False, "error": f"打开文件失败: {str(e)}"})


# ── Search (FTS5 + hybrid) ──────────────────────────


@app.route("/api/kb/search", methods=["POST"])
def search_kb():
    data = request.get_json() or {}
    query = data.get("query", "").strip()
    mode = data.get("mode", "hybrid")
    top_k = int(data.get("top_k", 100))
    kb_name = data.get("kb_name", config.CURRENT_KB)
    api_key = data.get("api_key", config.DEEPSEEK_API_KEY)
    embedding_api_key = data.get("embedding_api_key", "")
    if embedding_api_key:
        config.EMBEDDING_API_KEY = embedding_api_key
    # Use embedding key for vector search; fall back to chat key if not set
    embed_key = embedding_api_key.split(",")[0].strip() if embedding_api_key else api_key

    if not query:
        return jsonify({"success": False, "error": "请输入搜索关键词"})

    store = get_store(kb_name)
    if store.index is None or store.index.ntotal == 0:
        store.load()

    try:
        if mode == "keyword":
            results = store.keyword_search(query, top_k)
        elif mode == "vector":
            results = store.search(query, embed_key, top_k)
        else:
            results = store.hybrid_search(query, embed_key, top_k)

        return jsonify({
            "success": True,
            "data": {
                "results": [
                    {
                        "chunk_id": r[0]["chunk_id"],
                        "text": r[0]["text"],
                        "source_file": r[0]["source_file"],
                        "file_type": r[0]["file_type"],
                        "chunk_index": r[0]["chunk_index"],
                        "total_chunks": r[0]["total_chunks"],
                        "metadata": r[0]["metadata"],
                        "score": round(r[1], 4),
                        "match_type": r[0].pop("_match_type", None),
                    }
                    for r in results
                ],
                "mode": mode,
                "total": len(results),
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": f"搜索失败: {str(e)}"})


# ── Evidence Matrix ──────────────────────────────


@app.route("/api/kb/evidence-matrix", methods=["POST"])
def evidence_matrix():
    data = request.get_json() or {}
    audit_points = data.get("audit_points", [])
    kb_name = data.get("kb_name", config.CURRENT_KB)
    api_key = data.get("api_key", config.DEEPSEEK_API_KEY)

    if not audit_points or not isinstance(audit_points, list):
        return jsonify({"success": False, "error": "请提供审计要点列表"})
    if not api_key:
        return jsonify({"success": False, "error": "请先配置 API 密钥"})

    embedding_api_key = data.get("embedding_api_key", "")
    if embedding_api_key:
        config.EMBEDDING_API_KEY = embedding_api_key
    embed_key = embedding_api_key.split(",")[0].strip() if embedding_api_key else api_key

    store = get_store(kb_name)
    if store.index is None or store.index.ntotal == 0:
        store.load()

    # Check if KB has data
    total_chunks = store.count()
    if total_chunks == 0:
        return jsonify({"success": False, "error": "当前知识库为空，请先构建索引"})

    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        topic_file_map = {}
        file_meta = {}
        valid_points = [p for p in audit_points if p and p.strip()]

        def search_point(point):
            try:
                results = store.hybrid_search(point, embed_key, top_k=30)
            except Exception:
                try:
                    results = store.keyword_search(point, top_k=30)
                except Exception:
                    results = []
            topic_map = {}
            local_meta = {}
            for chunk_data, score in results:
                fpath = chunk_data["source_file"]
                if fpath not in local_meta:
                    local_meta[fpath] = {
                        "file": os.path.basename(fpath),
                        "file_path": fpath,
                        "file_type": chunk_data["file_type"],
                    }
                if fpath not in topic_map:
                    topic_map[fpath] = {"score": 0.0, "chunks": 0}
                topic_map[fpath]["score"] = max(topic_map[fpath]["score"], score)
                topic_map[fpath]["chunks"] += 1
            return point, topic_map, local_meta

        workers = min(config.EVIDENCE_MATRIX_WORKERS, max(1, len(valid_points)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(search_point, p) for p in valid_points]
            for future in as_completed(futures):
                point, topic_map, local_meta = future.result()
                topic_file_map[point] = topic_map
                file_meta.update(local_meta)

        matrix = []
        for fpath, meta in file_meta.items():
            topics_list = []
            max_score = 0.0
            for point in audit_points:
                t = topic_file_map.get(point, {}).get(fpath, {"score": 0.0, "chunks": 0})
                topics_list.append({
                    "topic": point,
                    "score": round(t["score"], 4),
                    "chunks": t["chunks"],
                })
                max_score = max(max_score, t["score"])
            matrix.append({**meta, "topics": topics_list, "max_score": round(max_score, 4)})

        matrix.sort(key=lambda x: x["max_score"], reverse=True)

        coverage = {}
        for point in audit_points:
            fm = topic_file_map.get(point, {})
            scores = [v["score"] for v in fm.values()]
            coverage[point] = {
                "files": len(fm),
                "total_files": len(file_meta),
                "avg_score": round(sum(scores) / len(scores), 4) if scores else 0,
                "max_score": round(max(scores), 4) if scores else 0,
            }

        return jsonify({
            "success": True,
            "data": {
                "matrix": matrix,
                "summary": {
                    "total_files": len(file_meta),
                    "topic_coverage": coverage,
                },
                "audit_points": audit_points,
            }
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        err = str(e)
        if "401" in err or "Invalid token" in err or "Unauthorized" in err:
            return jsonify({"success": False, "error": "Embedding API 密钥无效或未配置，请在设置中配置正确的 Embedding API 密钥，或仅使用关键词搜索模式"})
        return jsonify({"success": False, "error": f"证据矩阵生成失败: {err}"})


# ── Chat ────────────────────────────────────────────────


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json() or {}
    query = data.get("query", "").strip()
    api_key = data.get("api_key", config.DEEPSEEK_API_KEY)
    kb_name = data.get("kb_name", config.CURRENT_KB)

    if not query:
        return jsonify({"success": False, "error": "请输入问题"})
    if not api_key:
        return jsonify({"success": False, "error": "请先配置 DeepSeek API 密钥"})

    embedding_api_key = data.get("embedding_api_key", "")
    if embedding_api_key:
        config.EMBEDDING_API_KEY = embedding_api_key
    embed_key = embedding_api_key.split(",")[0].strip() if embedding_api_key else api_key
    context = data.get("context", "")

    try:
        history = _get_chat_history(kb_name)
        recent = history[-50:] if history else None
        result = ask_question(query, api_key, embed_key=embed_key, history=recent, kb_name=kb_name, context=context)

        # Log to audit session
        session = get_or_create_active_session(kb_name)
        session.add_turn("user", query)
        session.add_turn("assistant", result["answer"], result.get("sources", []))
        save_sessions(kb_name)

        history.append({"role": "user", "content": query})
        history.append({"role": "assistant", "content": result["answer"], "sources": result["sources"]})
        if len(history) > 200:
            with _chat_histories_lock:
                _chat_histories[kb_name] = history[-200:]
        _save_chat_history(kb_name)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "error": f"问答失败: {str(e)}"})


@app.route("/api/chat/upload", methods=["POST"])
def upload_temp_file():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "请选择文件"})
    file = request.files["file"]
    if not file.filename:
        return jsonify({"success": False, "error": "请选择文件"})

    ext = os.path.splitext(file.filename)[1].lower()
    tmpdir = os.path.join(config.BASE_DIR, "temp_uploads")
    os.makedirs(tmpdir, exist_ok=True)
    tmp_path = os.path.join(tmpdir, file.filename)
    file.save(tmp_path)

    try:
        plain_text_exts = {".txt", ".csv", ".md", ".py", ".json", ".xml", ".html", ".htm", ".log"}
        if ext in plain_text_exts:
            with open(tmp_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        elif ext in config.SUPPORTED_EXTENSIONS:
            result = parse_file(tmp_path)
            content = result.content
        else:
            return jsonify({"success": False, "error": f"不支持的文件格式: {ext}"})
        return jsonify({"success": True, "data": {"file_name": file.filename, "content": content}})
    except Exception as e:
        return jsonify({"success": False, "error": f"文件解析失败: {str(e)}"})
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


@app.route("/api/kb/test-file", methods=["POST"])
def test_single_file():
    data = request.get_json() or {}
    fpath = data.get("path", "").strip()
    if not fpath:
        return jsonify({"success": False, "error": "文件路径为空"})
    if not os.path.exists(fpath):
        return jsonify({"success": False, "error": f"文件不存在: {fpath}"})

    fname = os.path.basename(fpath)
    ext = os.path.splitext(fpath)[1].lower()
    if ext not in config.SUPPORTED_EXTENSIONS and ext not in {".txt", ".csv", ".md"}:
        return jsonify({"success": False, "error": f"不支持的文件格式: {ext}"})

    _logger.info("TestFile: 开始解析: %s", fname)
    t0 = time.time()
    try:
        parsed = _parse_via_subprocess(fpath)
    except Exception as e:
        elapsed = time.time() - t0
        _logger.error("TestFile: %s 解析失败 (%.1fs): %s", fname, elapsed, e)
        return jsonify({"success": False, "error": str(e)})
    elapsed = time.time() - t0
    chunks = chunk_document(parsed)

    _logger.info("TestFile: %s 解析完成: %d 字符, %d 块, 耗时 %.1fs", fname, len(parsed.content), len(chunks), elapsed)
    return jsonify({"success": True, "data": {
        "file_name": parsed.file_name,
        "file_type": parsed.file_type,
        "content_length": len(parsed.content),
        "chunks": len(chunks),
        "content_preview": parsed.content[:2000],
        "pages": parsed.pages,
        "elapsed": round(elapsed, 1),
    }})


@app.route("/api/chat/history", methods=["GET"])
def get_chat_history():
    kb_name = request.args.get("kb_name", config.CURRENT_KB)
    history = _get_chat_history(kb_name)
    return jsonify({"success": True, "data": history[-200:]})


@app.route("/api/chat/clear", methods=["POST"])
def clear_chat_history():
    data = request.get_json(silent=True) or {}
    kb_name = data.get("kb_name", config.CURRENT_KB)
    with _chat_histories_lock:
        _chat_histories[kb_name] = []
    _save_chat_history(kb_name)
    return jsonify({"success": True, "data": {"message": f"「{kb_name}」对话已清除"}})


# ── Audit Trail ─────────────────────────────────────


@app.route("/api/kb/audit-log", methods=["GET"])
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


@app.route("/api/kb/export-session", methods=["POST"])
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


@app.route("/api/kb/session/new", methods=["POST"])
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


@app.route("/api/kb/session/switch", methods=["POST"])
def switch_audit_session():
    data = request.get_json() or {}
    session_id = data.get("session_id")
    kb_name = data.get("kb_name", config.CURRENT_KB)
    if switch_session(kb_name, session_id):
        return jsonify({"success": True, "data": {"message": "已切换会话"}})
    return jsonify({"success": False, "error": "会话不存在"})


@app.route("/api/kb/session/delete", methods=["POST"])
def delete_audit_session():
    data = request.get_json() or {}
    session_id = data.get("session_id")
    kb_name = data.get("kb_name", config.CURRENT_KB)
    if not session_id:
        return jsonify({"success": False, "error": "缺少 session_id"})
    if delete_session(kb_name, session_id):
        return jsonify({"success": True, "data": {"message": "会话已删除"}})
    return jsonify({"success": False, "error": "会话不存在"})


# ── Summarize ──────────────────────────────────────────


@app.route("/api/kb/summarize", methods=["POST"])
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


# ── Material Gen ───────────────────────────────────────


@app.route("/api/material/templates", methods=["GET"])
def get_templates():
    return jsonify({"success": True, "data": {k: v for k, v in TEMPLATES.items()}})


@app.route("/api/material/report-templates", methods=["GET"])
def get_report_templates():
    audit_templates = {k: v for k, v in TEMPLATES.items() if k.startswith("审计")}
    return jsonify({"success": True, "data": audit_templates})


@app.route("/api/material/generate", methods=["POST"])
def generate():
    data = request.get_json() or {}
    framework = data.get("framework", "").strip()
    api_key = data.get("api_key", config.DEEPSEEK_API_KEY)
    kb_name = data.get("kb_name", config.CURRENT_KB)

    embedding_api_key = data.get("embedding_api_key", "")
    if embedding_api_key:
        config.EMBEDDING_API_KEY = embedding_api_key
    embed_key = embedding_api_key.split(",")[0].strip() if embedding_api_key else api_key

    if not framework:
        return jsonify({"success": False, "error": "请提供框架内容"})
    if not api_key:
        return jsonify({"success": False, "error": "请先配置 DeepSeek API 密钥"})

    try:
        result = generate_material(framework, api_key, embed_key=embed_key, kb_name=kb_name)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "error": f"生成失败: {str(e)}"})


@app.route("/api/material/export", methods=["POST"])
def export_material():
    data = request.get_json() or {}
    text = data.get("text", "")
    fmt = data.get("format", "md")
    if fmt == "md":
        return jsonify({"success": True, "data": {"text": text, "mime": "text/markdown"}})
    return jsonify({"success": True, "data": {"text": text, "mime": "text/plain"}})


# ── Config ─────────────────────────────────────────────


@app.route("/api/config", methods=["GET", "POST"])
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
    if "chunk_size" in data:
        config.CHUNK_SIZE = int(data["chunk_size"])
    if "top_k" in data:
        config.TOP_K_RESULTS = int(data["top_k"])
    return jsonify({"success": True, "data": {"message": "配置已更新"}})


@app.route("/api/shutdown", methods=["POST"])
def shutdown():
    """Gracefully shut down the Flask server."""
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return jsonify({"success": True, "data": {"message": "服务器正在关闭..."}})


@app.route("/api/config/test", methods=["POST"])
def test_connection():
    import urllib.request
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


@app.route("/api/config/test-embedding", methods=["POST"])
def test_embedding_keys():
    """Test each comma-separated embedding key individually."""
    import urllib.request
    data = request.get_json() or {}
    raw_keys = data.get("embedding_keys", "")
    keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
    if not keys:
        return jsonify({"success": False, "error": "请提供至少一个 Embedding API 密钥"})

    results = []
    for key in keys:
        masked = key[:8] + "..." + key[-4:] if len(key) > 14 else key[:6] + "..."
        try:
            test_data = json.dumps({
                "model": config.EMBEDDING_MODEL,
                "input": ["connection test"],
            }).encode()
            req = urllib.request.Request(
                f"{config.EMBEDDING_BASE_URL}/embeddings", data=test_data,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read())
            dim = len(result["data"][0]["embedding"]) if result.get("data") else "?"
            results.append({"key": masked, "ok": True, "dim": dim})
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:200]
            results.append({"key": masked, "ok": False, "error": f"HTTP {e.code}: {body}"})
        except Exception as e:
            results.append({"key": masked, "ok": False, "error": str(e)[:200]})

    ok_count = sum(1 for r in results if r["ok"])
    fail_count = len(results) - ok_count
    return jsonify({
        "success": True,
        "data": {
            "total": len(results),
            "ok": ok_count,
            "fail": fail_count,
            "results": results,
        },
    })


# ── Helpers ────────────────────────────────────────────


def _history_path(kb_name):
    return os.path.join(config.INDEX_DIR, kb_name, "chat_history.json")


def _read_chat_history_from_disk(kb_name):
    """Pure read from disk — no lock, no side effects. Returns list or None."""
    path = _history_path(kb_name)
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data[-200:]
    except Exception as e:
        _logger.warning("聊天历史加载失败 (%s): %s", kb_name, e)
    return None


def _load_chat_history(kb_name=None):
    kb_name = kb_name or config.CURRENT_KB
    history_data = _read_chat_history_from_disk(kb_name)
    with _chat_histories_lock:
        if history_data is not None:
            _chat_histories[kb_name] = history_data
        if kb_name not in _chat_histories:
            _chat_histories[kb_name] = []


def _save_chat_history(kb_name=None):
    kb_name = kb_name or config.CURRENT_KB
    path = _history_path(kb_name)
    with _chat_histories_lock:
        history = list(_chat_histories.get(kb_name, []))
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history[-200:], f, ensure_ascii=False, indent=2)
    except Exception as e:
        _logger.warning("聊天历史保存失败 (%s): %s", kb_name, e)


def _get_chat_history(kb_name=None):
    kb_name = kb_name or config.CURRENT_KB
    with _chat_histories_lock:
        if kb_name in _chat_histories:
            return list(_chat_histories[kb_name])

    # Cache miss: load from disk without holding lock
    history_data = _read_chat_history_from_disk(kb_name)

    # Double-check: another thread may have loaded the same kb while we did I/O
    with _chat_histories_lock:
        if kb_name not in _chat_histories:
            _chat_histories[kb_name] = history_data if history_data is not None else []
        return list(_chat_histories[kb_name])


def _format_size(size):
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def start_server(host="127.0.0.1", port=5000, debug=False):
    import webbrowser
    os.makedirs(config.INDEX_DIR, exist_ok=True)
    _ensure_libraries()
    _load_chat_history(config.CURRENT_KB)
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
