from config import DEEPSEEK_API_KEY, TOP_K_RESULTS
from services.vector_store import get_store
from services.llm_client import chat_completion
import os


def _find_matching_files(store, query):
    """Check if any query terms match indexed file names. Returns list of file paths.

    Uses 6+ char substring matching to avoid overly broad matches (e.g. '2026'
    appearing in hundreds of filenames). Limits to 10 files to prevent context
    from being overwhelmed by targeted results.
    """
    conn = store._get_db()
    try:
        all_files = conn.execute(
            "SELECT DISTINCT source_file FROM chunks"
        ).fetchall()
    finally:
        conn.close()

    matches = []
    query_lower = query.lower()
    for (fpath,) in all_files:
        fname = os.path.basename(fpath).lower()
        name_no_ext = os.path.splitext(fname)[0]

        # Exact match of full filename (without extension) — highest priority
        if len(name_no_ext) >= 6 and name_no_ext in query_lower:
            matches.append(fpath)
            continue

        # 6+ char substring match — balances recall vs precision
        matched = False
        for i in range(len(fname) - 5):
            seg = fname[i:i+6]
            if seg in query_lower:
                matched = True
                break
        if matched:
            matches.append(fpath)

        if len(matches) >= 10:
            break

    return matches


def ask_question(query, api_key=DEEPSEEK_API_KEY, top_k=TOP_K_RESULTS, history=None,
                 embed_key=None, kb_name=None, context=None):
    if not api_key:
        return {"answer": "请先在设置中配置 DeepSeek API 密钥。", "sources": []}

    store = get_store(kb_name)
    if store.index is None or store.index.ntotal == 0:
        store.load()

    # Use embed_key for embedding search, fall back to api_key
    emb_key = embed_key or api_key
    try:
        results = store.hybrid_search(query, emb_key, top_k)
    except Exception:
        results = store.keyword_search(query, top_k)

    # ── File-name targeted retrieval ──
    # If the query mentions specific file names, also pull chunks from those files
    # to ensure they appear in the context even if hybrid search missed them.
    targeted_files = _find_matching_files(store, query)
    if targeted_files:
        targeted_chunks = {}
        for fpath in targeted_files:
            file_chunks = store.get_chunks_by_source([fpath])
            if file_chunks:
                targeted_chunks[fpath] = file_chunks

        # Add targeted chunks to results with high scores so they rank well.
        # Must be > max hybrid score (keyword 1.0 + 1.0 boost + vector 0-1 = up to 3.0)
        targeted_score = 5.0
        existing_ids = {r[0].get("chunk_id", "") for r in results}
        for fpath, chunks in targeted_chunks.items():
            # Take up to 3 representative chunks per targeted file
            step = max(1, len(chunks) // 3)
            for i in range(0, len(chunks), step):
                c = chunks[i]
                if c["chunk_id"] not in existing_ids:
                    c["_match_type"] = "targeted"
                    results.append((c, targeted_score))
                    existing_ids.add(c["chunk_id"])
                if len([r for r in results if r[0].get("source_file") == fpath]) >= 5:
                    break

        # Re-sort after adding targeted results
        results = sorted(results, key=lambda x: x[1], reverse=True)

    # ── Build sources (deterministic, disambiguated) ──
    # Group chunks by source_file for dedup detection and stable ordering.
    import os as _os

    _file_chunks = {}  # file_key → list of (chunk, score)
    for chunk, score in results:
        file_key = chunk.get("source_file", "")
        # Bug C: guard against empty file_key — use chunk_id as fallback
        if not file_key:
            file_key = "__orphan__" + chunk.get("chunk_id", str(id(chunk)))
        _file_chunks.setdefault(file_key, []).append((chunk, score))

    # Detect duplicate basenames for disambiguation (Bug D)
    _basename_counts = {}
    for fkey in _file_chunks:
        bn = _os.path.basename(fkey) if "__orphan__" not in fkey else fkey[:40]
        _basename_counts[bn] = _basename_counts.get(bn, 0) + 1

    # Sort by basename then full path for deterministic numbering (Bug A)
    _sorted_keys = sorted(_file_chunks.keys(), key=lambda k: (
        _os.path.basename(k).lower() if "__orphan__" not in k else "",
        k.lower(),
    ))

    sources = []
    file_source_idx = {}  # file_key → source number (1-based)
    for file_key in _sorted_keys:
        chunks = _file_chunks[file_key]
        is_orphan = "__orphan__" in file_key
        bn = _os.path.basename(file_key) if not is_orphan else "（来源未知）"

        # Bug D: disambiguate identical basenames with parent folder
        if not is_orphan and _basename_counts.get(bn, 0) > 1:
            parent = _os.path.basename(_os.path.dirname(file_key))
            display_name = f"{parent}/{bn}" if parent else bn
        else:
            display_name = bn

        file_source_idx[file_key] = len(sources) + 1
        sources.append({
            "file": display_name,
            "file_path": file_key if not is_orphan else "",
            "snippet": chunks[0][0]["text"][:200],
            "score": round(float(chunks[0][1]), 3),
        })

    # Build context referencing source numbers
    context_parts = []
    for file_key in _sorted_keys:
        for chunk, score in _file_chunks[file_key]:
            is_orphan = "__orphan__" in file_key
            file_name = _os.path.basename(file_key) if not is_orphan else "（来源未知）"
            if not is_orphan and _basename_counts.get(file_name, 0) > 1:
                parent = _os.path.basename(_os.path.dirname(file_key))
                file_name = f"{parent}/{file_name}" if parent else file_name
            src_num = file_source_idx.get(file_key, 1)

            page_info = ""
            if chunk.get("metadata") and chunk["metadata"].get("page"):
                page_info = f", Page {chunk['metadata']['page']}"
            elif chunk.get("chunk_index") is not None:
                page_info = f", Chunk {chunk['chunk_index'] + 1}/{chunk.get('total_chunks', '?')}"

            context_parts.append(f"[{src_num}] (Source: {file_name}{page_info})\n{chunk['text'][:3000]}")

    # Use provided context, or build from search results
    ctx = context if context else ("\n\n".join(context_parts) if context_parts else "")

    if ctx:
        system_content = (
            "你是一个严谨的专业知识库助手，服务于审计/合规场景。回答时请严格遵循以下原则：\n"
            "1. 结合对话历史理解用户的真实意图\n"
            "2. 【强制要求】优先使用下方【知识库参考内容】回答，每个从知识库获取的事实、数据、条款都必须"
            "在句末标注来源编号，如 [1]、[2]、[3] 等（每个编号对应一个参考文件，同一文件的不同内容用同一个编号）\n"
            "   ⚠️ 来源编号必须严格使用上方参考内容中已标注的编号，不得超过已有编号范围，不得凭空编造编号\n"
            "3. 【强制要求】请提供详细、全面的回答，包含具体数据、条款原文和解释，"
            "避免过于简略的结论，让用户无需查看原文即可理解全部要点\n"
            "4. 【反幻觉要求】如果知识库参考内容不足以回答用户问题，"
            "你必须明确说明「根据当前知识库内容，无法找到关于[具体主题]的充分信息」，"
            "然后可以给出一般性建议（必须标明「以下为一般性知识，非知识库内容」）\n"
            "5. 绝不编造不存在于知识库中的数据、日期、金额、条款编号或具体事实\n"
            "6. 使用中文回答\n\n"
            f"【知识库参考内容】\n{ctx}"
        )
    else:
        system_content = (
            "你是一个专业的知识库助手。回答时请遵循以下原则：\n"
            "1. 结合对话历史理解用户的问题\n"
            "2. 请提供详细、全面的回答\n"
            "3. 使用中文回答"
        )

    # ── Multi-turn citation disambiguation ──
    # Each turn does an independent search, so source numbering changes.
    # Old [N] references in history would clash with new context numbering.
    # Build a legend from the most recent assistant turn so the LLM can
    # correctly resolve user references to old citations.
    import re as _re

    _old_source_map = {}  # int → filename
    if history:
        for turn in reversed(history):
            if turn["role"] == "assistant" and turn.get("sources"):
                for i, src in enumerate(turn["sources"]):
                    fname = (src.get("file") or src.get("file_path", "未知"))
                    fname = fname.split("\\")[-1].split("/")[-1]
                    _old_source_map[i + 1] = fname
                break

    if _old_source_map:
        legend_lines = [f"  [{n}] → {name}" for n, name in sorted(_old_source_map.items())]
        old_source_legend = (
            "\n\n【上轮对话引用映射 — 仅用于理解对话历史】\n"
            "上轮回答中来源编号的含义：\n"
            + "\n".join(legend_lines) +
            "\n\n重要：回答时请使用本轮下方【知识库参考内容】中的新编号标注来源，"
            "不要复用上轮的旧编号。如用户提到旧编号提问（如「[1]是什么文件」），"
            "请根据上表定位对应文件名，然后在本轮知识库中查找该文件相关内容来回答。"
        )
        system_content += old_source_legend

    messages = [{"role": "system", "content": system_content}]

    if history:
        for turn in history[-50:]:
            content = turn.get("content", "")
            role = turn["role"]
            if role not in ("user", "assistant"):
                continue

            # Annotate bare [N] in user messages so the LLM knows what file
            # the user is referring to (based on the previous turn's mapping).
            if role == "user" and _old_source_map:
                def _annotate_old_ref(m):
                    n = int(m.group(1))
                    if n in _old_source_map:
                        return f"[{n}](指上轮「{_old_source_map[n]}」)"
                    return m.group(0)
                content = _re.sub(r'\[(\d+)\]', _annotate_old_ref, content)

            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": query})

    answer_text = chat_completion(messages=messages, api_key=api_key, temperature=0.3)

    if not answer_text:
        return {"answer": "API 调用失败，请检查网络连接和 API 密钥。", "sources": sources}

    return {"answer": answer_text, "sources": sources}
