import os
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError

from config import DEEPSEEK_API_KEY
from services.llm_client import chat_completion
from utils import get_logger


def summarize_documents(file_paths, api_key, embed_key=None, kb_name=None):
    """MapReduce 全局总结：遍历文件所有 Chunk，分批提取要点后整合为完整报告。"""
    if not api_key:
        return {"error": "请先配置 API 密钥"}

    if not file_paths:
        return {"error": "请选择要总结的文件"}

    from services.vector_store import get_store
    store = get_store(kb_name)
    if store.index is None or store.index.ntotal == 0:
        store.load()
    if store.index is None or store.index.ntotal == 0:
        return {"error": "索引为空，请先构建索引"}

    # 按文件分组 Chunk
    all_chunks = store.get_chunks_by_source(file_paths)
    file_chunks = {}
    for chunk in all_chunks:
        source = chunk.get("source_file", "")
        if source not in file_chunks:
            file_chunks[source] = []
        file_chunks[source].append(chunk)

    if not file_chunks:
        return {"error": "所选文件在索引中未找到对应的 Chunk"}

    results = []
    file_list = list(file_chunks.items())
    file_list.sort(key=lambda x: x[0])

    # Parallel summarization with per-file timeout
    import config as _cfg
    max_workers = min(_cfg.SUMMARIZER_WORKERS, max(1, len(file_list)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {}
        for fpath, chunks in file_list:
            chunks.sort(key=lambda c: c.get("chunk_index", 0))
            fname = os.path.basename(fpath)
            futures[pool.submit(_summarize_single_file, fname, fpath, chunks, api_key)] = fpath

        for future in as_completed(futures, timeout=600):
            try:
                results.append(future.result())
            except Exception:
                pass

    # 多文件时生成总体整合报告
    overall = None
    if len(results) > 1:
        overall = _generate_overall_summary(results, api_key)

    return {"documents": results, "overall": overall}


def _summarize_single_file(fname, fpath, chunks, api_key):
    """MapReduce 总结单个文件。"""
    if not chunks:
        return {"file_name": fname, "file_path": fpath, "summary": "文件为空", "chunks": 0}

    # Map 阶段：分批提取要点
    batch_size = 10
    batch_summaries = []

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        parts = []
        for c in batch:
            idx = c.get("chunk_index", 0) + 1
            text = c.get("text", "")
            parts.append(f"[第 {idx} 段]\n{text}")

        batch_text = "\n\n".join(parts)
        prompt = f"""以下是文档 "{fname}" 的部分内容，请提取其中的核心要点、关键数据和重要结论。

内容：
{batch_text}

请以要点列表形式输出，保留具体数据、日期、金额等关键信息。"""

        summary = chat_completion(
            prompt=prompt, api_key=api_key, temperature=0.3, timeout=180,
            system_prompt="你是一个专业的文档分析助手，擅长从长文档中提取要点并生成结构化总结报告。",
        )
        if summary:
            batch_summaries.append(summary)

    if not batch_summaries:
        return {"file_name": fname, "file_path": fpath, "summary": "内容提取失败", "chunks": len(chunks)}

    # Reduce 阶段：合并所有批次摘要
    if len(batch_summaries) > 1:
        combined = "\n\n".join(
            [f"[第 {i + 1} 部分摘要]\n{s}" for i, s in enumerate(batch_summaries)]
        )
        reduce_prompt = f"""以下是对文档 "{fname}" 各部分的摘要，请整合为一份完整的总结报告。

各部分摘要：
{combined}

请按以下结构输出：
1. **文档概述** — 一句话说明文档主题
2. **核心要点** — 按重要性排列，每条附来源说明
3. **关键数据** — 数字、金额、日期、指标等
4. **重要结论** — 文档最终结论或决策"""
        final = chat_completion(
            prompt=reduce_prompt, api_key=api_key, temperature=0.3, timeout=180,
            system_prompt="你是一个专业的文档分析助手，擅长从长文档中提取要点并生成结构化总结报告。",
        ) or "\n\n".join(batch_summaries)
    else:
        final = batch_summaries[0]

    return {"file_name": fname, "file_path": fpath, "summary": final, "chunks": len(chunks)}


def _generate_overall_summary(results, api_key):
    """Multi-file overall synthesis with hierarchical reduce.

    When there are too many file summaries for a single LLM call,
    recursively combines them in batches of SUMMARIZER_REDUCE_BATCH
    until one final cross-file synthesis report remains.
    """
    import config as _cfg
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if not results:
        return None
    if len(results) == 1:
        return results[0]["summary"]

    batch_size = _cfg.SUMMARIZER_REDUCE_BATCH
    max_workers = min(_cfg.SUMMARIZER_REDUCE_WORKERS, max(1, len(results)))

    # Level 0: individual file summaries
    current_level = [
        {"files": [r["file_name"]], "summary": r["summary"], "level": 0}
        for r in results
    ]

    level = 0
    _reduce_logger = get_logger()
    _reduce_logger.info(
        "Overall summary reduce: %d file summaries, batch_size=%d",
        len(current_level), batch_size,
    )

    while len(current_level) > 1:
        level += 1
        batches = []
        for i in range(0, len(current_level), batch_size):
            batches.append(current_level[i:i + batch_size])

        def _reduce_batch(batch, lvl, batch_idx, batch_count):
            is_final = (batch_count == 1 and len(batch) == len(current_level))
            parts = []
            for item in batch:
                if item["level"] == 0:
                    label = f"[{item['files'][0]}]"
                else:
                    label = f"[{', '.join(item['files'][:5])}{'等' if len(item['files']) > 5 else ''} 的整合摘要]"
                parts.append(f"{label}\n{item['summary']}")

            combined = "\n\n".join(parts)
            file_count = sum(len(item["files"]) for item in batch)

            if is_final:
                prompt = f"""以下是 {len(batch)} 份摘要（覆盖 {file_count} 个文件），请整合为一份跨文档的综合分析报告。

各摘要：
{combined}

请按以下结构输出：
1. **总体概述** — 所有文档的核心主题
2. **各文档要点对比** — 相同点与差异
3. **共同主题与关键发现**
4. **综合结论**"""
            else:
                prompt = f"""以下是 {len(batch)} 份文档摘要（覆盖 {file_count} 个文件），请整合为一份中间整合摘要。

各摘要：
{combined}

请输出一份结构化的中间整合摘要，保留所有关键数据、日期、金额和来源标注。
格式：先列出共同主题，再列出各文件独特要点。"""

            result = chat_completion(
                prompt=prompt, api_key=api_key, temperature=0.3, timeout=300,
                system_prompt="你是一个专业的文档分析助手，擅长整合多份文档摘要并生成结构化报告。",
            )
            return {
                "files": [f for item in batch for f in item["files"]],
                "summary": result or "\n\n".join(item["summary"] for item in batch),
                "level": lvl,
            }

        next_level = []
        if len(batches) > 1:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {}
                for i, batch in enumerate(batches):
                    futures[pool.submit(
                        _reduce_batch, batch, level, i + 1, len(batches)
                    )] = i
                for future in as_completed(futures):
                    try:
                        next_level.append(future.result(timeout=600))
                    except Exception as e:
                        _reduce_logger.error("Reduce batch failed at level %d: %s", level, e)
                        idx = futures[future]
                        batch = batches[idx]
                        next_level.append({
                            "files": [f for item in batch for f in item["files"]],
                            "summary": "\n\n".join(item["summary"] for item in batch),
                            "level": level,
                        })
        else:
            next_level.append(_reduce_batch(batches[0], level, 1, 1))

        current_level = next_level
        _reduce_logger.info("Reduce level %d: %d → %d summaries",
                     level, len(batches), len(current_level))

    return current_level[0]["summary"] if current_level else None
