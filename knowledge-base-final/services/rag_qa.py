import json
import urllib.request
import urllib.error
import time

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, CHAT_MODEL, TOP_K_RESULTS
from services.vector_store import get_store


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
        results = store.search(query, emb_key, top_k)
    except Exception:
        results = store.keyword_search(query, top_k)

    context_parts = []
    sources = []
    seen_files = set()

    for chunk, score in results:
        file_name = chunk.get("source_file", "").split("\\")[-1].split("/")[-1]
        page_info = ""
        if chunk.get("metadata") and chunk["metadata"].get("page"):
            page_info = f", Page {chunk['metadata']['page']}"
        elif chunk.get("chunk_index") is not None:
            page_info = f", Chunk {chunk['chunk_index'] + 1}/{chunk.get('total_chunks', '?')}"

        context_parts.append(f"[{len(context_parts) + 1}] (Source: {file_name}{page_info})\n{chunk['text'][:3000]}")

        file_key = chunk.get("source_file", "")
        if file_key not in seen_files:
            seen_files.add(file_key)
            sources.append({
                "file": file_name,
                "file_path": file_key,
                "snippet": chunk["text"][:200],
                "score": round(float(score), 3),
            })

    # Use provided context, or build from search results
    ctx = context if context else ("\n\n".join(context_parts) if context_parts else "")

    if ctx:
        system_content = (
            "你是一个知识库助手。回答时遵循以下原则：\n"
            "1. 注意对话历史，结合上下文理解用户的问题\n"
            "2. 优先使用以下知识库内容回答，并用 [Source: 文件名] 标注来源\n"
            "3. 如果以下内容不足以回答，可以结合对话历史或你自己的知识补充\n"
            "4. 回答请使用中文\n\n"
            f"【知识库相关参考】\n{ctx}"
        )
    else:
        system_content = (
            "你是一个知识库助手。回答时遵循以下原则：\n"
            "1. 注意对话历史，结合上下文理解用户的问题\n"
            "2. 回答请使用中文"
        )

    messages = [{"role": "system", "content": system_content}]

    if history:
        for turn in history[-50:]:
            msg = {"role": turn["role"], "content": turn.get("content", "")}
            if msg["role"] in ("user", "assistant"):
                messages.append(msg)

    messages.append({"role": "user", "content": query})

    answer_text = _call_chat_api(messages, api_key)

    if not answer_text:
        return {"answer": "API 调用失败，请检查网络连接和 API 密钥。", "sources": sources}

    return {"answer": answer_text, "sources": sources}


def _call_chat_api(messages, api_key, temperature=0.3):
    retries = 3
    for attempt in range(retries):
        try:
            data = json.dumps({
                "model": CHAT_MODEL,
                "messages": messages,
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

            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            return result["choices"][0]["message"]["content"]

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if attempt < retries - 1 and e.code in (429, 500, 502, 503):
                time.sleep(2 ** attempt)
                continue
            raise Exception(f"Chat API error {e.code}: {body}")
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise

    return None
