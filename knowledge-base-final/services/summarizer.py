import json
import urllib.request
import urllib.error
import time
import os

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, CHAT_MODEL


def summarize_documents(file_paths, api_key):
    """MapReduce 全局总结：遍历文件所有 Chunk，分批提取要点后整合为完整报告。"""
    if not api_key:
        return {"error": "请先配置 API 密钥"}

    if not file_paths:
        return {"error": "请选择要总结的文件"}

    from services.embed_index import get_index
    index = get_index()
    if not index.chunks:
        loaded = index.load()
        if not loaded or not index.chunks:
            return {"error": "索引为空，请先构建索引"}

    # 按文件分组 Chunk
    file_chunks = {}
    for chunk in index.chunks:
        source = chunk.get("source_file", "")
        if source in file_paths:
            file_chunks.setdefault(source, []).append(chunk)

    if not file_chunks:
        return {"error": "所选文件在索引中未找到对应的 Chunk"}

    results = []
    for fpath, chunks in file_chunks.items():
        fname = os.path.basename(fpath)
        chunks.sort(key=lambda c: c.get("chunk_index", 0))
        file_result = _summarize_single_file(fname, fpath, chunks, api_key)
        results.append(file_result)

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

        summary = _call_api(prompt, api_key)
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
        final = _call_api(reduce_prompt, api_key) or "\n\n".join(batch_summaries)
    else:
        final = batch_summaries[0]

    return {"file_name": fname, "file_path": fpath, "summary": final, "chunks": len(chunks)}


def _generate_overall_summary(results, api_key):
    """多文件总体整合报告。"""
    parts = []
    for r in results:
        parts.append(f"[{r['file_name']}]\n{r['summary']}")

    combined = "\n\n".join(parts)
    prompt = f"""以下是多份文档的总结内容，请整合为一份跨文档的综合分析报告。

各文档总结：
{combined}

请按以下结构输出：
1. **总体概述** — 所有文档的核心主题
2. **各文档要点对比** — 相同点与差异
3. **共同主题与关键发现**
4. **综合结论**"""
    return _call_api(prompt, api_key)


def _call_api(prompt, api_key, temperature=0.3):
    """调用 DeepSeek 聊天 API。"""
    retries = 3
    for attempt in range(retries):
        try:
            data = json.dumps({
                "model": CHAT_MODEL,
                "messages": [
                    {"role": "system", "content": "你是一个专业的文档分析助手，擅长从长文档中提取要点并生成结构化总结报告。请确保信息完整、准确。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": temperature,
                "max_tokens": 8192,
                "stream": False,
            }).encode("utf-8")

            req = urllib.request.Request(
                url=f"{DEEPSEEK_BASE_URL}/chat/completions",
                data=data,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=180) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            return result["choices"][0]["message"]["content"]

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if attempt < retries - 1 and e.code in (429, 500, 502, 503):
                time.sleep(2 ** attempt)
                continue
            raise Exception(f"Summarizer API error {e.code}: {body}")
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise

    return None
