"""Indexing pipeline — parse, chunk, embed, save.

Extracted from app.py build_index() → index_task().
"""

import os
import json
import hashlib
import shutil
import threading
import time
from queue import Queue
import concurrent.futures as cf
from concurrent.futures import ThreadPoolExecutor

import config
from state import get_index_status
from utils import get_logger, parse_via_subprocess
from services.failed_files import save_failed_files

_logger = get_logger()


def _needs_ocr(fpath):
    """OCR-need detection via proportional text scan.

    Samples up to 20 pages.  If fewer than 30% have extractable text the
    PDF is treated as a scanned document and routed to the OCR pipeline.
    Image files always go to OCR.
    """
    ext = os.path.splitext(fpath)[1].lower()
    if ext in ('.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.tif'):
        return True
    if ext != '.pdf':
        return False
    try:
        import fitz
        doc = fitz.open(fpath)
        total = len(doc)
        sample = min(20, total)
        text_pages = 0
        for i in range(sample):
            if doc[i].get_text().strip():
                text_pages += 1
        doc.close()
        # Require ≥ 30 % text-bearing pages to skip OCR
        return text_pages < sample * 0.3
    except Exception:
        return False


def run_pipeline(kb, flist, store, idx_status, stop_event, api_key, embed_keys):
    """Run the full index pipeline for a list of files.

    This is the core of build_index(). All shared state (idx_status, stop_event,
    etc.) is passed explicitly so the function is self-contained.
    """
    from services.text_chunker import chunk_document

    t_start = time.time()
    n = len(flist)
    parse_errors = 0
    embed_errors = 0
    total_chunks = 0

    cancelled_indices = set()
    idx_status["cancelled_indices"] = cancelled_indices
    idx_status["file_statuses"] = []

    # ── Checkpoint path ──
    checkpoint_path = os.path.join(config.INDEX_DIR, kb, "index_checkpoint.json")
    parsed_cache_path = os.path.join(config.INDEX_DIR, kb, "parsed_chunks.json")
    CHECKPOINT_SAVE_INTERVAL = 10

    resume_from = 0
    cp_status = ""
    if os.path.exists(checkpoint_path):
        try:
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                cp = json.load(f)
            if cp.get("file_list") == flist:
                cp_status = cp.get("status", "")
                if cp_status == "embedding":
                    resume_from = cp.get("completed", 0)
                elif cp_status == "parsing":
                    resume_from = cp.get("parsed", 0)
                else:
                    resume_from = cp.get("completed", 0)
                embed_errors = cp.get("embed_errors", 0)
                parse_errors = cp.get("parse_errors", 0)
                total_chunks = cp.get("total_chunks", 0)
                raw_errors = cp.get("errors", [])
                seen_paths = set()
                normalized = []
                for e in raw_errors:
                    if isinstance(e, dict):
                        if "path" not in e:
                            e["path"] = ""
                        # 去重：同类型+同路径只保留一条
                        key = (e.get("type", ""), e.get("path", ""), e.get("file", ""),
                               e.get("message", "")[:100])
                        if key not in seen_paths:
                            seen_paths.add(key)
                            normalized.append(e)
                    else:
                        s = str(e)
                        if "跳过" in s and "重复" in s:
                            normalized.append({"type": "duplicate", "file": "", "path": "", "message": s})
                        else:
                            normalized.append({"type": "parse_error", "file": "", "path": "", "message": s})
                idx_status["errors"] = normalized
                _logger.info("Checkpoint: resuming from file #%d/%d (status=%s)", resume_from, n, cp_status)
        except Exception as e:
            _logger.warning("Checkpoint load failed, starting fresh: %s", e)

    # ── Crash recovery ──
    index_bak = store._index_path + ".bak"
    db_bak = store._db_path + ".bak"
    if os.path.exists(index_bak) and os.path.exists(db_bak):
        if resume_from == 0:
            shutil.copy2(index_bak, store._index_path)
            shutil.copy2(db_bak, store._db_path)
            store.load()
            _logger.info("Recovery: restored FAISS + SQLite from backup")
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

    # ── Dedup ──
    manifest_path = os.path.join(config.INDEX_DIR, kb, "file_manifest.json")
    dedup_cache_path = os.path.join(config.INDEX_DIR, kb, "dedup_cache.json")
    _logger.info("Dedup: checking %d files for duplicates...", n)

    known_hashes = {}
    path_hash_map = {}
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
    batch_hashes = {}
    path_hash_cache = {}

    if os.path.exists(dedup_cache_path):
        try:
            with open(dedup_cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("file_list") == flist:
                path_hash_cache = cached.get("batch_hashes", {})
                for p, h in path_hash_cache.items():
                    batch_hashes[h] = p
                _logger.info("Dedup: loaded %d cached hashes", len(path_hash_cache))
        except Exception:
            pass

    idx_status["progress"] = f"[{kb}] 检查重复文件..."
    for i in range(resume_from, n):
        fpath = flist[i]
        if fpath in path_hash_map:
            file_hash = path_hash_map[fpath]
        elif fpath in path_hash_cache:
            file_hash = path_hash_cache[fpath]
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
            path_hash_cache[fpath] = file_hash

        # 检查是否已有该文件的错误记录（避免恢复时双计）
        existing_paths = {e.get("path", "") for e in idx_status["errors"]}

        if file_hash in known_hashes:
            dup_path = known_hashes[file_hash]
            if fpath not in existing_paths:
                idx_status["errors"].append({
                    "type": "duplicate", "file": os.path.basename(fpath), "path": fpath,
                    "message": f"跳过（与已索引的 {os.path.basename(dup_path)} 内容重复）"
                })
            flist[i] = None
            dup_count += 1
            idx_status["current"] = i + 1
        elif file_hash in batch_hashes:
            dup_path = batch_hashes[file_hash]
            if fpath not in existing_paths:
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

    try:
        with open(dedup_cache_path, "w", encoding="utf-8") as f:
            json.dump({"file_list": flist, "batch_hashes": path_hash_cache, "dup_count": dup_count}, f)
    except Exception:
        pass

    # Initialize per-file status
    idx_status["file_statuses"] = []
    for i, fpath in enumerate(flist):
        if fpath is None:
            idx_status["file_statuses"].append({"path": "", "name": "", "status": "duplicate", "chunks": 0})
        else:
            idx_status["file_statuses"].append({"path": fpath, "name": os.path.basename(fpath), "status": "pending", "chunks": 0})
    dup_entries = [e for e in idx_status["errors"] if e.get("type") == "duplicate"]
    dup_idx = 0
    for fs in idx_status["file_statuses"]:
        if fs["status"] == "duplicate" and dup_idx < len(dup_entries):
            fs["name"] = dup_entries[dup_idx].get("file", "")
            fs["path"] = dup_entries[dup_idx].get("path", "")
            dup_idx += 1

    if dup_count:
        _logger.info("Dedup: skipped %d duplicate files", dup_count)

    effective_n = n - dup_count
    idx_status["total"] = effective_n
    idx_status["current"] = 0

    # ── Pipeline ──
    parsed_batches = [None] * n
    parse_times = []

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

    parsed_count = resume_from
    embedded_count = resume_from
    if loaded_from_cache:
        parsed_count = max(parsed_count, cp.get("parsed", resume_from))
        if cp_status in ("embedding", "pipelining"):
            embedded_count = max(embedded_count, cp.get("completed", resume_from))

    # Backup before modifying
    index_bak = store._index_path + ".bak"
    db_bak = store._db_path + ".bak"
    if os.path.exists(store._index_path):
        shutil.copy2(store._index_path, index_bak)
    if os.path.exists(store._db_path):
        shutil.copy2(store._db_path, db_bak)

    # Batch-remove old chunks
    t_remove_start = time.time()
    pending_files = [f for f in flist[resume_from:] if f is not None]
    if pending_files:
        store.remove_by_source_files(pending_files)
        store.save()
    t_remove_elapsed = time.time() - t_remove_start

    with open(checkpoint_path, "r+", encoding="utf-8") as f:
        cp = json.load(f)
        cp["parsed"] = parsed_count
        cp["completed"] = embedded_count
        cp["status"] = "pipelining"
        f.seek(0)
        json.dump(cp, f, ensure_ascii=False)
        f.truncate()

    embed_keys_pool = embed_keys if embed_keys else [api_key]

    to_embed_now = []
    valid_before = 0
    for i in range(parsed_count):
        bi = parsed_batches[i]
        if bi is not None and len(bi) > 0 and flist[i] is not None:
            valid_before += 1
            if valid_before > embedded_count - resume_from:
                to_embed_now.append((i, flist[i], bi))

    need_parse = [(i, flist[i]) for i in range(parsed_count, n) if flist[i] is not None]

    ocr_files = [(i, fp) for i, fp in need_parse if _needs_ocr(fp)]
    fast_files = [(i, fp) for i, fp in need_parse if (i, fp) not in set(ocr_files)]

    ocr_count = len(ocr_files)
    fast_count = len(fast_files)
    _logger.info("Pipeline: %d fast-text + %d OCR files -> %d-thread unified pool",
                 fast_count, ocr_count, config.INDEX_WORKERS)
    idx_status["progress"] = f"[{kb}] OCR: {ocr_count}个文件, 快速: {fast_count}个文件, 开始解析..."

    t_parse_elapsed = 0.0

    if not need_parse and not to_embed_now:
        pass
    else:
        chunk_queue = Queue(maxsize=config.PIPELINE_QUEUE_SIZE)
        _SENTINEL = object()
        pipeline_lock = threading.Lock()
        save_lock = threading.Lock()

        pipe_state = {
            "embedded_count": embedded_count,
            "embed_errors": embed_errors,
            "total_chunks": total_chunks,
            "total_embed_api_time": 0.0,
            "total_faiss_write_time": 0.0,
        }

        parsing_done = threading.Event()

        # ── embed_worker ──
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
                if i in cancelled_indices:
                    if i < len(idx_status.get("file_statuses", [])):
                        idx_status["file_statuses"][i]["status"] = "cancelled"
                    with pipeline_lock:
                        pipe_state["embedded_count"] += 1
                    chunk_queue.task_done()
                    continue
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
                        except Exception as e:
                            _logger.warning("Checkpoint save failed: %s", e)
                        try:
                            with open(parsed_cache_path, "w", encoding="utf-8") as f:
                                json.dump(parsed_batches, f, ensure_ascii=False)
                        except Exception as e:
                            _logger.warning("Parsed cache save failed: %s", e)

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

        # ── parse_and_enqueue ──
        def parse_and_enqueue(fpath, i, skip_ocr=False):
            nonlocal parsed_count, parse_errors
            t0 = time.time()
            fname = os.path.basename(fpath)
            is_ocr = not skip_ocr
            tag = "[OCR]" if is_ocr else "[FAST]"
            file_timeout = config.OCR_PARSE_TIMEOUT if is_ocr else config.PARSE_TIMEOUT
            _logger.info("%s 开始解析: %s (超时: %ds)", tag, fname, file_timeout)
            if i < len(idx_status.get("file_statuses", [])):
                idx_status["file_statuses"][i]["status"] = "parsing"
                idx_status["file_statuses"][i]["started_at"] = time.time()
            if i in cancelled_indices:
                _logger.info("%s 已取消: %s", tag, fname)
                if i < len(idx_status.get("file_statuses", [])):
                    idx_status["file_statuses"][i]["status"] = "cancelled"
                with pipeline_lock:
                    parsed_count += 1
                    pipe_state["embedded_count"] += 1
                return
            try:
                parsed = parse_via_subprocess(fpath, skip_ocr=skip_ocr, timeout=file_timeout)
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
                        _logger.warning("Pipeline: queue full, skipping embedding for %s", fname)
                        idx_status["errors"].append({
                            "type": "embed_error", "file": fname, "path": fpath,
                            "message": "队列满超时，跳过嵌入"
                        })
                        with pipeline_lock:
                            pipe_state["embedded_count"] += 1
                            pipe_state["embed_errors"] += 1
                else:
                    with pipeline_lock:
                        pipe_state["embedded_count"] += 1
            except Exception as e:
                parse_errors += 1
                import traceback as _tb
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

        # ── Start embed pool ──
        idx_status["progress"] = f"[{kb}] 管道: 解析+嵌入并行 ({config.INDEX_WORKERS}→{config.EMBED_WORKERS} 线程)..."

        embed_executor = ThreadPoolExecutor(max_workers=config.EMBED_WORKERS)
        embed_futures = [
            embed_executor.submit(embed_worker, embed_keys_pool[j % len(embed_keys_pool)])
            for j in range(config.EMBED_WORKERS)
        ]

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

        if fast_files or ocr_files:
            parse_future_map = {}
            unified_pool = ThreadPoolExecutor(max_workers=config.INDEX_WORKERS)

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
                            pass

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

        parsing_done.set()
        for _ in range(config.EMBED_WORKERS):
            try:
                chunk_queue.put(_SENTINEL, timeout=30)
            except Exception:
                pass

        embed_timeout = max(300, effective_n * 15) + 120
        try:
            for future in cf.as_completed(embed_futures, timeout=embed_timeout):
                try:
                    future.result()
                except Exception:
                    pass
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

        store.train_pq_if_needed()

        embed_errors = pipe_state["embed_errors"]
        total_chunks = pipe_state["total_chunks"]
        total_embed_api_time = pipe_state["total_embed_api_time"]
        total_faiss_write_time = pipe_state["total_faiss_write_time"]

        try:
            with open(parsed_cache_path, "w", encoding="utf-8") as f:
                json.dump(parsed_batches, f, ensure_ascii=False)
        except Exception:
            pass

    store.save()

    # ── Profiling ──
    t_total = time.time() - t_start
    t_pipeline = t_total - t_remove_elapsed
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

    # ── Write manifest ──
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
                    existing_by_path[entry] = {"path": entry}
        except Exception:
            pass

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

    # Cleanup
    for p in [checkpoint_path, parsed_cache_path, dedup_cache_path, index_bak, db_bak]:
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception as e:
            _logger.warning("Cleanup failed for %s: %s", p, e)

    idx_status["running"] = False
    idx_status["progress"] = (
        f"[{kb}] 完成: {n - dup_count} 个文件（跳过 {dup_count} 个重复）, {total_chunks} 个文本块, "
        f"解析错误: {parse_errors}, 嵌入错误: {embed_errors}"
    )

    save_failed_files(kb, idx_status)
