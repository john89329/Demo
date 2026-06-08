"""Deep analysis engine — two audit modes.

Mode A (focus): user selects files → system checks against task.
Mode B (scan):  user provides framework → system scans all files → fills framework.
"""

import json
import re
import time
import concurrent.futures as cf
from concurrent.futures import ThreadPoolExecutor

from config import DEEPSEEK_API_KEY
from services.vector_store import get_store, segment_chinese_query
from services.llm_client import chat_completion
from utils import get_logger

_logger = get_logger()

# ── helpers ──────────────────────────────────────────────────

def _build_file_context(store, file_paths, api_key=None):
    """Get all chunks for given files, with file-level MapReduce for large files.

    Normalizes path separators and tries both exact-path and basename matching
    to handle path-format differences between frontend and database.

    When api_key is provided and a file has many chunks, the file content is
    first summarized via internal MapReduce so the LLM sees a complete summary
    rather than a truncated subset.
    """
    # Normalize input paths to use backslashes (matching DB format)
    normalized = [p.replace('/', '\\') for p in file_paths]

    # Try exact match first
    chunks = store.get_chunks_by_source(normalized)

    # If no results, try matching by filename (basename)
    if not chunks:
        import os as _os
        conn = store._get_db()
        try:
            all_files = conn.execute(
                "SELECT DISTINCT source_file FROM chunks"
            ).fetchall()
        finally:
            conn.close()

        # Build mapping from basename to full path
        name_to_path = {}
        for (fp,) in all_files:
            name_to_path[_os.path.basename(fp)] = fp

        matched_paths = []
        for p in file_paths:
            basename = _os.path.basename(p.replace('/', '\\'))
            if basename in name_to_path:
                matched_paths.append(name_to_path[basename])

        if matched_paths:
            chunks = store.get_chunks_by_source(matched_paths)

    by_file = {}
    for c in chunks:
        f = c["source_file"]
        by_file.setdefault(f, []).append(c)

    parts = []
    for fpath, clist in by_file.items():
        fname = fpath.split("\\")[-1].split("/")[-1]

        # ── File-level MapReduce for large files ──
        MAX_CHUNKS_PER_FILE = 30
        if api_key and len(clist) > MAX_CHUNKS_PER_FILE:
            # Sort chunks by index for coherent reading
            clist_sorted = sorted(clist, key=lambda c: c.get("chunk_index", 0))

            # Batch the chunks
            file_batches = [
                clist_sorted[i:i + MAX_CHUNKS_PER_FILE]
                for i in range(0, len(clist_sorted), MAX_CHUNKS_PER_FILE)
            ]

            # Map phase: extract key points from each batch in parallel
            from concurrent.futures import ThreadPoolExecutor as _Pool
            from concurrent.futures import as_completed as _ac

            def _map_batch(bi, batch):
                batch_text = "\n\n".join(
                    f"[Chunk {c.get('chunk_index', 0) + 1}/{c.get('total_chunks', '?')}]\n{c['text'][:2000]}"
                    for c in batch
                )
                if len(batch_text) > 15000:
                    batch_text = batch_text[:15000] + "\n...(truncated)"

                map_prompt = (
                    f"你正在阅读文档「{fname}」的第 {bi + 1}/{len(file_batches)} 部分。"
                    f"请提取这部分的核心要点、关键数据和重要结论。\n\n"
                    f"内容：\n{batch_text}\n\n"
                    f"请用要点列表输出，保留具体数据、日期、条款编号。"
                )
                try:
                    s = chat_completion(
                        prompt=map_prompt, api_key=api_key,
                        temperature=0.2, max_tokens=4096, timeout=180,
                        system_prompt="你是一个专业的文档分析助手，负责从长文档中提取要点。",
                    )
                    return (bi, s if s else f"（第{bi+1}部分无内容）")
                except Exception:
                    return (bi, f"（第{bi+1}部分提取失败）")

            _map_results = {}
            # Use up to 4 workers for file-internal parallelism
            _file_workers = min(4, len(file_batches))
            with _Pool(max_workers=_file_workers) as _pool:
                _futures = {
                    _pool.submit(_map_batch, bi, batch): bi
                    for bi, batch in enumerate(file_batches)
                }
                for _f in _ac(_futures):
                    try:
                        _bi, _text = _f.result(timeout=300)
                        _map_results[_bi] = _text
                    except Exception:
                        _bi = _futures[_f]
                        _map_results[_bi] = f"（第{_bi+1}部分提取失败）"

            batch_summaries = [_map_results[i] for i in sorted(_map_results)]

            # Reduce phase: synthesize a complete file summary
            if len(batch_summaries) <= 1:
                text = batch_summaries[0] if batch_summaries else "(内容为空)"
            else:
                combined_parts = "\n\n".join(
                    f"[第 {i+1} 部分要点]\n{s}"
                    for i, s in enumerate(batch_summaries)
                )
                reduce_prompt = (
                    f"以下是文档「{fname}」各部分的要点摘要，请整合为一份完整的文档概要：\n\n"
                    f"{combined_parts}\n\n"
                    f"请按以下结构输出：\n"
                    f"1. 文档主题与结构概述\n"
                    f"2. 各章节/部分的核心内容（按文档结构排列）\n"
                    f"3. 关键数据、条款、日期汇总"
                )
                try:
                    text = chat_completion(
                        prompt=reduce_prompt, api_key=api_key,
                        temperature=0.2, max_tokens=8192, timeout=300,
                        system_prompt="你是一个专业的文档分析助手。",
                    )
                    text = text or "\n\n".join(batch_summaries)
                except Exception:
                    text = "\n\n".join(batch_summaries)

            text = text if text else "(内容为空)"
            # Trim if still too large
            if len(text) > 15000:
                text = text[:15000] + "\n...(truncated)"
        else:
            # ── Small file: keep original behavior ──
            text = "\n\n".join(c["text"][:2000] for c in clist[:MAX_CHUNKS_PER_FILE])
            if len(text) > 12000:
                text = text[:12000] + "\n...(truncated)"

        parts.append(f"=== {fname} ===\n{text}")

    return "\n\n".join(parts), by_file


def _exhaustive_search(store, query):
    """FTS5 full-text search returning ALL matching files with their chunks."""
    search_query = segment_chinese_query(query)
    conn = store._get_db()
    try:
        try:
            rows = conn.execute(
                "SELECT c.chunk_id, c.text, c.source_file, c.file_type, "
                "c.chunk_index, c.total_chunks, c.metadata "
                "FROM chunks c JOIN chunks_fts fts ON c.id = fts.rowid "
                "WHERE chunks_fts MATCH ?",
                (search_query,),
            ).fetchall()
        except Exception:
            rows = conn.execute(
                "SELECT c.chunk_id, c.text, c.source_file, c.file_type, "
                "c.chunk_index, c.total_chunks, c.metadata "
                "FROM chunks c JOIN chunks_fts fts ON c.id = fts.rowid "
                "WHERE chunks_fts MATCH ?",
                (query,),
            ).fetchall()
    finally:
        conn.close()

    by_file = {}
    for row in rows:
        chunk = {
            "chunk_id": row[0], "text": row[1], "source_file": row[2],
            "file_type": row[3], "chunk_index": row[4], "total_chunks": row[5],
            "metadata": json.loads(row[6]) if row[6] else {},
        }
        f = chunk["source_file"]
        by_file.setdefault(f, []).append(chunk)

    return by_file


def _parse_framework(framework_text):
    """Parse user's framework text into a list of (section_title, section_detail) pairs."""
    lines = framework_text.strip().split("\n")
    sections = []
    current_title = ""
    current_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Detect section headers: numbered or key-pattern lines
        is_header = bool(re.match(
            r'^[（(]?[一二三四五六七八九十\d]+[）).、]|^[第].*[节章条]|^[A-Z][.!]',
            stripped
        ))
        if is_header:
            if current_title:
                sections.append((current_title, "\n".join(current_lines)))
            current_title = stripped
            current_lines = []
        else:
            current_lines.append(stripped)

    if current_title:
        sections.append((current_title, "\n".join(current_lines)))

    # If no structured headers found, treat entire input as one section
    if not sections:
        sections = [("分析任务", framework_text.strip())]

    return sections


# ── Mode A: Focus Analysis (MapReduce-enabled) ───────────────

def _focus_single_batch(file_items, task, api_key, store,
                        batch_idx=None, total_batches=None):
    """Analyze one batch of files against the audit task.

    Args:
        file_items: list of (fpath, chunks_list) tuples for this batch.
        task: audit task description.
        api_key: DeepSeek API key.
        store: VectorStore instance (already loaded).
        batch_idx: optional 1-based batch number.
        total_batches: optional total batch count.

    Returns:
        {"batch_index": int, "result_markdown": str, "sources": list,
         "files_analyzed": int}
    """
    fpaths = [fp for fp, _ in file_items]
    by_file = {fp: chunks for fp, chunks in file_items}

    # Build context for this batch (same logic as original _build_file_context)
    parts = []
    for fpath, clist in by_file.items():
        fname = fpath.split("\\")[-1].split("/")[-1]
        text = "\n\n".join(c["text"][:2000] for c in clist[:30])
        if len(text) > 12000:
            text = text[:12000] + "\n...(truncated)"
        parts.append(f"=== {fname} ===\n{text}")

    context = "\n\n".join(parts)
    if not context:
        return {
            "batch_index": batch_idx or 1,
            "result_markdown": "（此批次无可分析文本内容）",
            "sources": [], "files_analyzed": 0,
        }

    sources = []
    for fpath in fpaths:
        fname = fpath.split("\\")[-1].split("/")[-1]
        sources.append({"file": fname, "file_path": fpath})

    batch_header = ""
    if batch_idx and total_batches and total_batches > 1:
        batch_header = f"（分析批次 {batch_idx}/{total_batches}）\n"

    system = (
        "你是一名专业审计师，服务于国有企业审计项目。"
        "请根据提供的文件内容执行审计任务。"
    )
    prompt = (
        f"{batch_header}"
        f"【审计任务】\n{task}\n\n"
        f"【待分析文件】（本批次 {len(fpaths)} 个文件）\n{context}\n\n"
        "请按要求输出。如果需要对比，使用表格。"
        "如果发现差异、矛盾或合规问题，请明确标注 ⚠️。"
        "每个结论必须注明来源文件。"
    )

    result = chat_completion(
        prompt=prompt, system_prompt=system,
        api_key=api_key, temperature=0.2, max_tokens=16384,
    )

    return {
        "batch_index": batch_idx or 1,
        "result_markdown": result or "分析未能完成，请重试。",
        "sources": sources,
        "files_analyzed": len(fpaths),
    }


def _focus_synthesize(batch_results, task, api_key, total_files, total_batches):
    """Synthesize multiple batch analysis results into one unified audit report.

    Args:
        batch_results: list of dicts from _focus_single_batch, sorted by batch_index.
        task: original audit task.
        api_key: DeepSeek API key.
        total_files: total file count across all batches.
        total_batches: number of batches.

    Returns:
        {"result_markdown": str, "sources": list, "files_analyzed": int}
    """
    if total_batches <= 1:
        r = batch_results[0]
        return {
            "result_markdown": r["result_markdown"],
            "sources": r["sources"],
            "files_analyzed": r["files_analyzed"],
        }

    parts = []
    for br in batch_results:
        parts.append(
            f"### 批次 {br['batch_index']} 分析结果 "
            f"（{br['files_analyzed']} 个文件）\n\n{br['result_markdown']}"
        )
    combined = "\n\n---\n\n".join(parts)

    system = (
        "你是一名高级审计经理。你的任务是将多个独立分析批次的发现"
        "整合为一份完整、统一、无冗余的审计报告。"
        "合并相同发现，标注跨批次模式，按重要性排序。"
    )
    prompt = (
        f"【原始审计任务】\n{task}\n\n"
        f"以下是对 {total_files} 个文件分 {total_batches} 批次独立分析的结果。"
        f"请整合为一份完整的最终审计报告。\n\n"
        f"{combined}\n\n"
        "请按以下结构输出最终报告：\n"
        "1. **执行摘要** — 审计范围概述（文件数、关键发现数）\n"
        "2. **主要发现** — 合并去重后按重要性排列，每个发现注明来源文件和批次\n"
        "3. **跨批次模式** — 在多个批次中反复出现的问题或模式\n"
        "4. **按主题/风险领域分类的详细发现**（按主题重组，不要按批次分组）\n"
        "5. **风险评估与建议**\n\n"
        "要求：\n"
        "- 不要简单拼接或按批次组织报告，必须按主题重新组织\n"
        "- 合并相同发现，标注所有来源文件\n"
        "- 如有矛盾之处，标注 ⚠️ 并优先报告\n"
    )

    result = chat_completion(
        prompt=prompt, system_prompt=system,
        api_key=api_key, temperature=0.2, max_tokens=24576,
    )

    all_sources = []
    seen = set()
    for br in batch_results:
        for s in br.get("sources", []):
            if s["file_path"] not in seen:
                seen.add(s["file_path"])
                all_sources.append(s)

    return {
        "result_markdown": result or "综合分析未能完成，请重试。",
        "sources": all_sources,
        "files_analyzed": total_files,
    }


def focus_analysis(file_paths, task, api_key=None, kb_name=None):
    """Analyze specific files according to the user's audit task.

    When file count exceeds FOCUS_ANALYSIS_BATCH_FILES, automatically splits
    into batches (Map phase), analyzes each independently, then synthesizes
    findings into a unified report (Reduce phase).

    Args:
        file_paths: list of absolute file paths to analyze.
        task: natural-language description of the audit task.
        api_key: DeepSeek API key.
        kb_name: knowledge base name.

    Returns:
        {"result_markdown": str, "sources": list, "files_analyzed": int,
         "batches_used": int (if multiple)}
    """
    import config as _cfg

    api_key = api_key or DEEPSEEK_API_KEY
    store = get_store(kb_name)
    store.load()

    t0 = time.time()
    _logger.info("Focus analysis: %d files, task=%s", len(file_paths), task[:80])

    # Get context from specified files (reuse _build_file_context for lookup)
    _, by_file = _build_file_context(store, file_paths, api_key=api_key)
    if not by_file:
        return {
            "result_markdown": "选定文件中没有可分析的文本内容。",
            "sources": [], "files_analyzed": 0,
        }

    file_items = sorted(by_file.items(), key=lambda x: x[0])
    total_files = len(file_items)
    batch_size = _cfg.FOCUS_ANALYSIS_BATCH_FILES
    max_workers = _cfg.FOCUS_ANALYSIS_WORKERS

    # ── Fast path: single batch ──
    if total_files <= batch_size:
        result = _focus_single_batch(file_items, task, api_key, store)
        elapsed = time.time() - t0
        _logger.info("Focus analysis done: %d files, %.1fs", total_files, elapsed)
        return {
            "result_markdown": result["result_markdown"],
            "sources": result["sources"],
            "files_analyzed": total_files,
            "elapsed": round(elapsed, 1),
        }

    # ── MapReduce path ──
    batches = []
    for i in range(0, total_files, batch_size):
        batches.append(file_items[i:i + batch_size])
    total_batches = len(batches)

    _logger.info("Focus MapReduce: %d files → %d batches × ~%d files/批",
                 total_files, total_batches, batch_size)

    # Phase 1: MAP — analyze each batch in parallel
    batch_results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {}
        for idx, batch in enumerate(batches):
            batch_num = idx + 1
            f = pool.submit(
                _focus_single_batch, batch, task, api_key, store,
                batch_idx=batch_num, total_batches=total_batches,
            )
            futures[f] = batch_num

        for future in cf.as_completed(futures):
            batch_num = futures[future]
            try:
                result = future.result(timeout=600)
                batch_results.append(result)
                _logger.info("Focus batch %d/%d done: %d files",
                             batch_num, total_batches,
                             result.get("files_analyzed", 0))
            except Exception as e:
                _logger.error("Focus batch %d/%d FAILED: %s",
                              batch_num, total_batches, e)
                batch_results.append({
                    "batch_index": batch_num,
                    "result_markdown": f"（批次 {batch_num} 分析失败：{str(e)[:200]}）",
                    "sources": [],
                    "files_analyzed": len(batches[batch_num - 1]),
                })

    # Phase 2: REDUCE — synthesize
    batch_results.sort(key=lambda r: r["batch_index"])
    final = _focus_synthesize(batch_results, task, api_key,
                              total_files, total_batches)

    elapsed = time.time() - t0
    _logger.info("Focus MapReduce done: %d files, %d batches, %.1fs",
                 total_files, total_batches, elapsed)

    return {
        "result_markdown": final["result_markdown"],
        "sources": final["sources"],
        "files_analyzed": total_files,
        "elapsed": round(elapsed, 1),
        "batches_used": total_batches,
    }


# ── Mode B: Scan Analysis ────────────────────────────────────

def scan_analysis(framework, api_key=None, kb_name=None):
    """Scan all indexed files and fill in the user's framework.

    Args:
        framework: user-provided outline text (sections with bullet points)
        api_key: DeepSeek API key
        kb_name: knowledge base name

    Returns:
        {"result_markdown": str, "sources": list, "files_scanned": int, "sections": list}
    """
    api_key = api_key or DEEPSEEK_API_KEY
    store = get_store(kb_name)
    store.load()

    t0 = time.time()
    sections = _parse_framework(framework)
    _logger.info(
        "Scan analysis: %d sections, kb=%s", len(sections), kb_name or "default"
    )

    # ── Round 1 (per-section): find relevant files ──
    section_files = {}     # section_title → [file_paths]
    section_details = {}   # section_title → detail text
    all_hit_files = set()

    for title, detail in sections:
        query = f"{title} {detail}"
        hits = _exhaustive_search(store, query)
        # Sort by hit count, no hard cap — MapReduce handles scale
        top_files = sorted(hits.keys(), key=lambda f: len(hits[f]), reverse=True)
        section_files[title] = top_files
        section_details[title] = detail
        all_hit_files.update(top_files)

    if not all_hit_files:
        return {
            "result_markdown": "未在知识库中找到与框架相关的文件内容。",
            "sources": [], "files_scanned": 0, "sections": [],
        }

    # ── Helper: analyze one batch of files for a section ──
    def _analyze_section_batch(title, detail, fpaths, batch_idx, total_batches):
        context, _ = _build_file_context(store, fpaths, api_key=api_key)
        if not context:
            return {"batch_index": batch_idx, "result": "（此批次无可分析内容）",
                    "files": fpaths}

        import config as _cfg2
        batch_size = _cfg2.FOCUS_ANALYSIS_BATCH_FILES
        batch_hint = ""
        if total_batches > 1:
            batch_hint = f"（分析批次 {batch_idx}/{total_batches}，{len(fpaths)} 个文件）\n"

        system = (
            "你是一名专业审计师。请根据提供的文件内容，"
            "提取与以下主题相关的关键信息。"
            "输出要简洁、结构化，每条信息注明来源文件。"
            "如果多个文件记载不一致，标注 ⚠️。"
        )
        prompt = (
            f"{batch_hint}"
            f"【提取主题】\n{title}\n\n"
            f"【待提取内容】\n{detail}\n\n"
            f"【文件内容】\n{context}\n\n"
            "请提取相关信息，用 bullet points 输出，每条标注 [来源文件名]。"
        )
        result = chat_completion(
            prompt=prompt, system_prompt=system,
            api_key=api_key, temperature=0.2, max_tokens=16384,
        )
        return {"batch_index": batch_idx,
                "result": result or "（提取失败）",
                "files": fpaths}

    # ── Round 2 (per-section): MapReduce extraction ──
    import config as _cfg2

    def process_section(title, fpaths):
        if not fpaths:
            return title, "（未找到相关文件）", []

        detail = section_details.get(title, "")
        batch_size = _cfg2.FOCUS_ANALYSIS_BATCH_FILES

        # ── Fast path: single batch ──
        if len(fpaths) <= batch_size:
            br = _analyze_section_batch(title, detail, fpaths, 1, 1)
            return title, br["result"], fpaths

        # ── MapReduce path ──
        batches = [fpaths[i:i + batch_size]
                    for i in range(0, len(fpaths), batch_size)]
        total_batches = len(batches)
        _logger.info("Section MapReduce [%s]: %d files → %d batches",
                     title[:40], len(fpaths), total_batches)

        batch_results = []
        with ThreadPoolExecutor(max_workers=_cfg2.FOCUS_ANALYSIS_WORKERS) as pool:
            futures = {}
            for idx, batch in enumerate(batches):
                batch_num = idx + 1
                f = pool.submit(_analyze_section_batch,
                                title, detail, batch, batch_num, total_batches)
                futures[f] = batch_num
            for future in cf.as_completed(futures):
                batch_num = futures[future]
                try:
                    batch_results.append(future.result(timeout=600))
                except Exception as e:
                    _logger.error("Section batch %d/%d FAILED: %s",
                                  batch_num, total_batches, e)
                    batch_results.append({
                        "batch_index": batch_num,
                        "result": f"（批次 {batch_num} 提取失败：{str(e)[:200]}）",
                        "files": batches[batch_num - 1],
                    })

        batch_results.sort(key=lambda r: r["batch_index"])

        # Synthesize per-section batch results
        if total_batches == 1:
            final = batch_results[0]["result"]
        else:
            parts = []
            for br in batch_results:
                parts.append(
                    f"### 批次 {br['batch_index']} 提取结果 "
                    f"（{len(br.get('files', []))} 个文件）\n\n{br['result']}"
                )
            combined = "\n\n---\n\n".join(parts)
            synth_prompt = (
                f"【提取主题】\n{title}\n\n"
                f"【待提取内容】\n{detail}\n\n"
                f"以下是对 {len(fpaths)} 个文件分 {total_batches} 批次的提取结果，"
                f"请整合为一份结构化总结：\n\n{combined}\n\n"
                "要求：按关键信息点组织，合并去重，不要按批次分组。"
                "每条信息标注来源文件名。"
            )
            final = chat_completion(
                prompt=synth_prompt, api_key=api_key,
                temperature=0.2, max_tokens=16384,
                system_prompt="你是一名专业审计师，擅长整合多批次分析结果。",
            )
            final = final or "\n\n".join(br["result"] for br in batch_results)

        return title, final, fpaths

    section_results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(process_section, title, section_files.get(title, [])): title
            for title, _ in sections
        }
        for future in cf.as_completed(futures):
            section_results.append(future.result())

    # ── Build final report ──
    md_parts = []
    all_sources = []
    seen_files = set()

    # Sort by original framework order
    order = {title: i for i, (title, _) in enumerate(sections)}
    section_results.sort(key=lambda r: order.get(r[0], 999))

    for title, content, fpaths in section_results:
        md_parts.append(f"## {title}\n\n{content}\n")

        for fp in fpaths:
            if fp not in seen_files:
                seen_files.add(fp)
                fname = fp.split("\\")[-1].split("/")[-1]
                all_sources.append({"file": fname, "file_path": fp})

    elapsed = time.time() - t0
    _logger.info(
        "Scan analysis done: %d sections, %d files, %.1fs",
        len(sections), len(all_sources), elapsed,
    )

    return {
        "result_markdown": "\n\n".join(md_parts),
        "sources": all_sources,
        "files_scanned": len(all_hit_files),
        "files_hit": len(all_sources),
        "sections": [{"title": r[0], "files_found": len(r[2])} for r in section_results],
        "elapsed": round(elapsed, 1),
    }


# ── Mode C: Comparative Clause Analysis ───────────────────

def compare_clauses(task, api_key=None, kb_name=None):
    """Phase-2 deep comparison: feed original clause text to LLM for detailed diff.

    Unlike focus_analysis and scan_analysis which use MapReduce summaries,
    this function uses RAW chunk text so the LLM can compare exact wording.

    Args:
        task: natural-language description of what to compare, e.g.
              "对比《董事会授权管理办法》与《总经理授权委托管理办法》的权限划分"
        api_key: DeepSeek API key.
        kb_name: knowledge base name.

    Returns:
        {"result_markdown": str, "sources": list, "files_used": int}
    """
    api_key = api_key or DEEPSEEK_API_KEY
    store = get_store(kb_name)
    store.load()

    t0 = time.time()
    _logger.info("Compare clauses: task=%s", task[:80])

    # ── Phase 2a: targeted search — find ALL matching chunks with raw text ──
    hits = _exhaustive_search(store, task)
    if not hits:
        return {
            "result_markdown": "未在知识库中找到与对比主题相关的文件内容。",
            "sources": [], "files_used": 0,
        }

    # Sort files by hit count, take top files proportional to task scope
    sorted_files = sorted(hits.items(), key=lambda x: len(x[1]), reverse=True)
    total_hit_files = len(sorted_files)

    # Build context with RAW chunk text (no summarization, no MapReduce)
    # Cap: max 50 chunks per file, 4000 chars per chunk, 60000 chars per file
    MAX_CHUNKS = 50
    MAX_CHAR_PER_CHUNK = 4000
    MAX_CHAR_PER_FILE = 60000

    context_parts = []
    sources = []
    seen_files = set()

    for fpath, chunks in sorted_files:
        if fpath in seen_files:
            continue
        seen_files.add(fpath)
        fname = fpath.split("\\")[-1].split("/")[-1]

        # Sort chunks by index for coherent reading
        chunks_sorted = sorted(chunks, key=lambda c: c.get("chunk_index", 0))

        # Take representative samples: beginning, evenly-spaced middle, end
        n = len(chunks_sorted)
        if n <= MAX_CHUNKS:
            selected = chunks_sorted
        else:
            # Smart sampling: first 15, last 15, and evenly-distributed middle
            selected = chunks_sorted[:15] + chunks_sorted[-15:]
            step = max(1, (n - 30) // 20)
            middle = chunks_sorted[15:-15:step]
            # Deduplicate by chunk_id
            seen_ids = {c["chunk_id"] for c in selected}
            for c in middle:
                if c["chunk_id"] not in seen_ids:
                    selected.append(c)
                    seen_ids.add(c["chunk_id"])
            selected.sort(key=lambda c: c.get("chunk_index", 0))

        file_text_parts = []
        file_char_count = 0
        for c in selected:
            chunk_text = c["text"][:MAX_CHAR_PER_CHUNK]
            if file_char_count + len(chunk_text) > MAX_CHAR_PER_FILE:
                file_text_parts.append("\n...(后续内容因长度限制省略)")
                break
            page_tag = ""
            if c.get("metadata") and c["metadata"].get("page"):
                page_tag = f" [第{c['metadata']['page']}页]"
            file_text_parts.append(
                f"--- Chunk {c.get('chunk_index', 0) + 1}/{c.get('total_chunks', '?')}{page_tag} ---\n{chunk_text}"
            )
            file_char_count += len(chunk_text)

        file_text = "\n\n".join(file_text_parts)
        context_parts.append(f"===== {fname} =====\n{file_text}")
        sources.append({"file": fname, "file_path": fpath})

    # Limit total context to ~40 files to keep prompt manageable
    if len(context_parts) > 40:
        context_parts = context_parts[:40]
        context_parts.append(f"\n（共命中 {total_hit_files} 个文件，此处展示最相关的 40 个）")

    context = "\n\n".join(context_parts)

    # ── Phase 2b: detailed clause comparison ──
    system = (
        "你是一名专业审计师，精通制度对比分析。你的任务是将多份制度文件的原文条款"
        "进行逐条比对，找出实质性冲突、权限重叠、定义矛盾、程序不一致等问题。"
        "必须引用原文具体条文作为证据，绝不可凭空推断。"
    )
    prompt = (
        f"【对比任务】\n{task}\n\n"
        f"【制度原文】（共命中 {total_hit_files} 个文件，以下为原文摘录，未经摘要压缩）\n"
        f"{context}\n\n"
        "请按以下结构输出：\n\n"
        "## 一、涉及文件\n列出本次对比涉及的文件及其章节范围\n\n"
        "## 二、逐条对比\n"
        "对每个相关条款进行并排对比，使用以下格式：\n"
        "- **主题**：XXX\n"
        "- **文件A**（文件名）：引用原文\n"
        "- **文件B**（文件名）：引用原文\n"
        "- **分析**：两文件规定是否一致？如有冲突说明具体差异\n\n"
        "## 三、冲突清单\n"
        "用表格列出所有发现的冲突/不一致：\n"
        "| 类型 | 文件A | 文件B | 冲突描述 | 严重程度 |\n"
        "|---|---|---|---|---|\n"
        "（类型：权限冲突 / 定义矛盾 / 程序不一致 / 覆盖重叠 / 条款缺失）\n\n"
        "## 四、合规建议\n"
        "针对每项冲突给出具体修订建议\n\n"
        "要求：\n"
        "- 必须引用文件中的原文具体条文（条款编号、章节名、原文段落）\n"
        "- 如文件中无相关内容，明确说明「该文件未涉及此条款」\n"
        "- 标注 ⚠️ 标记实质性冲突\n"
        "- 标注 ✅ 标记一致的规定"
    )

    result = chat_completion(
        prompt=prompt, system_prompt=system,
        api_key=api_key, temperature=0.2, max_tokens=24576,
    )

    elapsed = time.time() - t0
    _logger.info("Compare clauses done: %d files, %.1fs", len(sources), elapsed)

    return {
        "result_markdown": result or "对比分析未能完成，请重试。",
        "sources": sources,
        "files_used": len(sources),
        "total_hit": total_hit_files,
        "elapsed": round(elapsed, 1),
    }
