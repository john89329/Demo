import re
import uuid
from collections import namedtuple

DocumentChunk = namedtuple("DocumentChunk", ["chunk_id", "text", "source_file", "file_type", "chunk_index", "total_chunks", "metadata"])

CHUNK_SIZE = 512
CHUNK_OVERLAP = 64


def estimate_tokens(text):
    """估算 BGE-M3 实际 token 数，系数偏保守以避免超出 8192 限制。"""
    chinese_chars = sum(1 for c in text if '一' <= c <= '鿿')
    english_words = len(re.findall(r'[a-zA-Z]+', text))
    other = len(text) - chinese_chars - sum(len(w) for w in re.findall(r'[a-zA-Z]+', text))
    return int(chinese_chars * 2.5 + english_words * 2.0 + other * 1.0)


def _split_long_paragraph(para, max_tokens, max_chars=3000):
    """将超长段落按句子或字符切分为小块。"""
    sentences = re.split(r'(?<=[。！？!?])', para)
    sentences = [s.strip() for s in sentences if s.strip()]
    if len(sentences) <= 1:
        lines = para.split('\n')
        result = []
        batch, batch_len = [], 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if batch_len + len(line) > max_chars and batch:
                result.append('\n'.join(batch))
                batch, batch_len = [], 0
            batch.append(line)
            batch_len += len(line)
        if batch:
            result.append('\n'.join(batch))
        return result

    result = []
    for i in range(0, len(sentences), 10):
        sub = ''.join(sentences[i:i+10])
        if sub.strip():
            result.append(sub)
    return result


def chunk_document(parsed_doc, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    content = parsed_doc.content
    if not content.strip():
        return []

    paragraphs = re.split(r'\n\s*\n', content)
    chunks = []
    current_chunk = []
    current_tokens = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        para_tokens = estimate_tokens(para)

        # 超长段落：先切碎再逐段处理
        if para_tokens > chunk_size:
            sub_paras = _split_long_paragraph(para, chunk_size)
            for sp in sub_paras:
                sp_tokens = estimate_tokens(sp)
                if current_tokens + sp_tokens > chunk_size and current_chunk:
                    chunk_text = "\n\n".join(current_chunk)
                    chunks.append(chunk_text)

                    overlap_text = []
                    overlap_tokens = 0
                    for p in reversed(current_chunk):
                        pt = estimate_tokens(p)
                        if overlap_tokens + pt > overlap:
                            break
                        overlap_text.insert(0, p)
                        overlap_tokens += pt
                    current_chunk = list(overlap_text)
                    current_tokens = overlap_tokens

                current_chunk.append(sp)
                current_tokens += sp_tokens
            continue

        if current_tokens + para_tokens > chunk_size and current_chunk:
            chunk_text = "\n\n".join(current_chunk)
            chunks.append(chunk_text)

            overlap_text = []
            overlap_tokens = 0
            for p in reversed(current_chunk):
                pt = estimate_tokens(p)
                if overlap_tokens + pt > overlap:
                    break
                overlap_text.insert(0, p)
                overlap_tokens += pt
            current_chunk = list(overlap_text)
            current_tokens = overlap_tokens

        current_chunk.append(para)
        current_tokens += para_tokens

    if current_chunk:
        chunk_text = "\n\n".join(current_chunk)
        chunks.append(chunk_text)

    # 硬限制：每块不超过 3000 字符（BGE-M3 限 8192 tokens，3000字≈7500t 安全）
    MAX_CHUNK_CHARS = 3000
    for i, c in enumerate(chunks):
        if len(c) > MAX_CHUNK_CHARS:
            cut = c.rfind('\n', 0, MAX_CHUNK_CHARS)
            chunks[i] = c[:cut if cut > 0 else MAX_CHUNK_CHARS]

    if not chunks:
        chunks = [content[:MAX_CHUNK_CHARS]]

    result = []
    total = len(chunks)
    for i, text in enumerate(chunks):
        chunk_id = str(uuid.uuid4())
        meta = {}
        if parsed_doc.pages and len(parsed_doc.pages) > 1:
            for pi, pt in enumerate(parsed_doc.pages):
                if text[:100] in pt or pt[:100] in text:
                    meta["page"] = pi + 1
                    break
        if parsed_doc.metadata:
            meta.update(parsed_doc.metadata)

        result.append(DocumentChunk(
            chunk_id=chunk_id,
            text=text.strip(),
            source_file=parsed_doc.file_path,
            file_type=parsed_doc.file_type,
            chunk_index=i,
            total_chunks=total,
            metadata=meta,
        ))

    return result
