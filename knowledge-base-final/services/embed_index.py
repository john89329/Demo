import os
import json
import numpy as np
import urllib.request
import urllib.error
import time

import config


def _migrate_legacy_index(kb_name=None):
    """将旧版 index/ 根目录下的索引文件迁移到 index/{kb_name}/。"""
    import shutil

    kb_name = kb_name or config.CURRENT_KB
    kb_dir = os.path.join(config.INDEX_DIR, kb_name)
    os.makedirs(kb_dir, exist_ok=True)
    migrated = []
    for filename in ("chunks.json", "embeddings.npy", "file_manifest.json"):
        legacy = os.path.join(config.INDEX_DIR, filename)
        target = os.path.join(kb_dir, filename)
        if os.path.exists(legacy) and not os.path.exists(target):
            shutil.move(legacy, target)
            migrated.append(filename)
    return migrated


class EmbeddingIndex:
    def __init__(self, kb_name=None):
        self.kb_name = kb_name or config.CURRENT_KB
        self.chunks = []
        self.embeddings = None
        self.dimension = 0

    def _kb_dir(self):
        return os.path.join(config.INDEX_DIR, self.kb_name)

    def load(self):
        _migrate_legacy_index(self.kb_name)
        chunks_path = os.path.join(self._kb_dir(), "chunks.json")
        embeddings_path = os.path.join(self._kb_dir(), "embeddings.npy")

        if os.path.exists(chunks_path) and os.path.exists(embeddings_path):
            try:
                with open(chunks_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not data:
                    return False
                self.chunks = data
                self.embeddings = np.load(embeddings_path, allow_pickle=True)
                if self.embeddings.ndim < 2 or self.embeddings.shape[0] != len(self.chunks):
                    return False
                self.dimension = self.embeddings.shape[1] if self.embeddings.ndim > 1 else 0
                return True
            except Exception:
                self.chunks = []
                self.embeddings = None
                self.dimension = 0
                return False
        return False

    def save(self):
        if not self.chunks or self.embeddings is None:
            return

        kb_dir = self._kb_dir()
        os.makedirs(kb_dir, exist_ok=True)
        chunks_path = os.path.join(kb_dir, "chunks.json")
        embeddings_path = os.path.join(kb_dir, "embeddings.npy")

        serializable = []
        seen = set()
        for c in self.chunks:
            cid = c.get("chunk_id", "")
            if cid not in seen:
                seen.add(cid)
                serializable.append(c)

        with open(chunks_path, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)
        np.save(embeddings_path, self.embeddings)

    def clear(self):
        self.chunks = []
        self.embeddings = None
        self.dimension = 0
        kb_dir = self._kb_dir()
        for f in ["chunks.json", "embeddings.npy", "file_manifest.json"]:
            fp = os.path.join(kb_dir, f)
            if os.path.exists(fp):
                os.remove(fp)

    def add_chunks(self, chunk_dicts, api_key):
        if not chunk_dicts:
            return 0

        texts = [c["text"] for c in chunk_dicts]
        new_embeddings = self._get_embeddings(texts, api_key)

        if new_embeddings is None or len(new_embeddings) == 0:
            return 0

        self.chunks.extend(chunk_dicts)

        if self.embeddings is None:
            self.embeddings = np.array(new_embeddings, dtype=np.float32)
        else:
            self.embeddings = np.vstack([self.embeddings, np.array(new_embeddings, dtype=np.float32)])

        self.dimension = self.embeddings.shape[1]
        return len(chunk_dicts)

    def search(self, query, api_key, top_k=5):
        if self.embeddings is None or len(self.chunks) == 0:
            return []

        query_emb = self._get_embeddings([query], api_key)
        if query_emb is None or len(query_emb) == 0:
            return self._keyword_fallback(query, top_k)

        query_vec = np.array(query_emb[0], dtype=np.float32)
        query_norm = query_vec / (np.linalg.norm(query_vec) + 1e-10)
        stored_norm = self.embeddings / (np.linalg.norm(self.embeddings, axis=1, keepdims=True) + 1e-10)
        similarities = np.dot(stored_norm, query_norm)

        top_indices = np.argsort(similarities)[-top_k:][::-1]
        results = []
        for idx in top_indices:
            score = float(similarities[idx])
            if score < 0.1:
                break
            results.append((self.chunks[idx], score))
        return results

    def _keyword_fallback(self, query, top_k):
        query_lower = query.lower()
        query_words = set(query_lower.split())
        scored = []
        for i, chunk in enumerate(self.chunks):
            text_lower = chunk["text"].lower()
            score = sum(1 for w in query_words if w in text_lower)
            if score > 0:
                scored.append((i, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        results = []
        for idx, score in scored[:top_k]:
            results.append((self.chunks[idx], score / max(len(query_words), 1)))
        return results

    @staticmethod
    def _truncate_text(text, max_chars=3000):
        """截断文本以防止超出 API token 限制 (bge-m3 限 8192 tokens)。"""
        if len(text) > max_chars:
            return text[:max_chars]
        return text

    def _get_embeddings(self, texts, api_key):
        if not api_key:
            return None

        embedding_key = config.EMBEDDING_API_KEY or api_key
        embedding_url = config.EMBEDDING_BASE_URL

        texts = [self._truncate_text(t) for t in texts]

        all_embeddings = []
        batch_size = 20

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            retries = 3
            for attempt in range(retries):
                try:
                    data = json.dumps({
                        "model": config.EMBEDDING_MODEL,
                        "input": batch,
                    }).encode("utf-8")

                    req = urllib.request.Request(
                        url=f"{embedding_url}/embeddings",
                        data=data,
                        headers={
                            "Authorization": f"Bearer {embedding_key}",
                            "Content-Type": "application/json",
                        },
                        method="POST",
                    )

                    with urllib.request.urlopen(req, timeout=60) as resp:
                        result = json.loads(resp.read().decode("utf-8"))

                    batch_embs = [item["embedding"] for item in result["data"]]
                    all_embeddings.extend(batch_embs)
                    break

                except urllib.error.HTTPError as e:
                    body = e.read().decode("utf-8", errors="replace")
                    if e.code == 413 and len(batch) > 0:
                        half = [t[:len(t)//2] for t in batch]
                        print(f"[WARN] 413 文本过长，截半重试 ({len(batch)} 条)")
                        try:
                            data2 = json.dumps({
                                "model": config.EMBEDDING_MODEL,
                                "input": half,
                            }).encode("utf-8")
                            req2 = urllib.request.Request(
                                url=f"{embedding_url}/embeddings",
                                data=data2,
                                headers={
                                    "Authorization": f"Bearer {embedding_key}",
                                    "Content-Type": "application/json",
                                },
                                method="POST",
                            )
                            with urllib.request.urlopen(req2, timeout=60) as resp2:
                                result = json.loads(resp2.read().decode("utf-8"))
                            batch_embs = [item["embedding"] for item in result["data"]]
                            all_embeddings.extend(batch_embs)
                            break
                        except Exception:
                            pass
                    if attempt < retries - 1 and e.code in (429, 500, 502, 503):
                        time.sleep(2 ** attempt)
                        continue
                    raise Exception(f"Embedding API error {e.code}: {body}")
                except Exception as e:
                    if attempt < retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    raise

        return all_embeddings


_indices = {}


def get_index(kb_name=None):
    if kb_name is None:
        kb_name = config.CURRENT_KB
    if kb_name not in _indices:
        _indices[kb_name] = EmbeddingIndex(kb_name)
    return _indices[kb_name]
