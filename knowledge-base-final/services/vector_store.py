import os
import json
import time
import sqlite3
import threading
import numpy as np
import urllib.request
import urllib.error

import faiss
import config


class VectorStore:
    def __init__(self, kb_name=None):
        self.kb_name = kb_name or config.CURRENT_KB
        self._kb_dir = os.path.join(config.INDEX_DIR, self.kb_name)
        self._index_path = os.path.join(self._kb_dir, "vectors.faiss")
        self._db_path = os.path.join(self._kb_dir, "chunks.db")
        self.index = None
        self.dimension = 0
        self._lock = threading.Lock()

    def load(self):
        os.makedirs(self._kb_dir, exist_ok=True)
        if not os.path.exists(self._index_path) or not os.path.exists(self._db_path):
            return False
        try:
            import tempfile, shutil
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".faiss", prefix="idx_load_")
            os.close(tmp_fd)
            try:
                shutil.copy2(self._index_path, tmp_path)
                self.index = faiss.read_index(tmp_path)
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            self.dimension = self.index.d
            return True
        except Exception:
            self.index = None
            self.dimension = 0
            return False

    def save(self):
        if self.index is None:
            return
        os.makedirs(self._kb_dir, exist_ok=True)
        with self._lock:
            # FAISS C++ fopen() fails on Windows with non-ASCII paths.
            # Write to a temp file in the project's base dir (ASCII-safe), then move.
            import tempfile, shutil
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".faiss", prefix="idx_tmp_")
            os.close(tmp_fd)
            try:
                faiss.write_index(self.index, tmp_path)
                shutil.move(tmp_path, self._index_path)
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

    def clear(self):
        with self._lock:
            self.index = None
            self.dimension = 0
            if os.path.exists(self._db_path):
                os.remove(self._db_path)
            if os.path.exists(self._index_path):
                os.remove(self._index_path)
            manifest_path = os.path.join(self._kb_dir, "file_manifest.json")
            if os.path.exists(manifest_path):
                os.remove(manifest_path)
            checkpoint_path = os.path.join(self._kb_dir, "index_checkpoint.json")
            if os.path.exists(checkpoint_path):
                os.remove(checkpoint_path)
            parsed_cache_path = os.path.join(self._kb_dir, "parsed_chunks.json")
            if os.path.exists(parsed_cache_path):
                os.remove(parsed_cache_path)

    def _init_index(self, dim):
        self.dimension = dim
        self.index = faiss.IndexFlatIP(dim)

    def _get_db(self):
        os.makedirs(self._kb_dir, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chunk_id TEXT UNIQUE,
                text TEXT,
                source_file TEXT,
                file_type TEXT,
                chunk_index INTEGER,
                total_chunks INTEGER,
                metadata TEXT
            )"""
        )
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, source_file, content=chunks, content_rowid=id)"
        )
        conn.execute(
            "CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN "
            "INSERT INTO chunks_fts(rowid, text, source_file) VALUES (new.id, new.text, new.source_file); END"
        )
        conn.execute(
            "CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN "
            "INSERT INTO chunks_fts(chunks_fts, rowid, text, source_file) VALUES ('delete', old.id, old.text, old.source_file); END"
        )
        conn.commit()
        return conn

    def embed_chunks(self, chunks, api_key):
        if not chunks or not api_key:
            return None
        texts = [c["text"] for c in chunks]
        return self._get_embeddings(texts, api_key)

    def add_embedded_chunks(self, chunks, vectors):
        if not chunks or vectors is None or len(vectors) == 0:
            return 0
        dim = len(vectors[0])
        with self._lock:
            if self.index is None:
                self._init_index(dim)

            vec_array = np.array(vectors, dtype=np.float32)
            faiss.normalize_L2(vec_array)
            self.index.add(vec_array)

            conn = self._get_db()
            try:
                for c in chunks:
                    conn.execute(
                        "INSERT OR REPLACE INTO chunks (chunk_id, text, source_file, file_type, chunk_index, total_chunks, metadata) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            c["chunk_id"],
                            c["text"],
                            c["source_file"],
                            c["file_type"],
                            c["chunk_index"],
                            c["total_chunks"],
                            json.dumps(c.get("metadata", {}), ensure_ascii=False),
                        ),
                    )
                conn.commit()
            finally:
                conn.close()
        return len(chunks)

    def search(self, query, api_key, top_k=5):
        if self.index is None or self.index.ntotal == 0:
            return []
        query_vecs = self._get_embeddings([query], api_key)
        if query_vecs is None or len(query_vecs) == 0:
            return self.keyword_search(query, top_k)

        q = np.array([query_vecs[0]], dtype=np.float32)
        faiss.normalize_L2(q)
        distances, indices = self.index.search(q, min(top_k, self.index.ntotal))

        conn = self._get_db()
        try:
            results = []
            for dist, idx in zip(distances[0], indices[0]):
                if idx < 0:
                    continue
                row = conn.execute(
                    "SELECT chunk_id, text, source_file, file_type, chunk_index, total_chunks, metadata FROM chunks WHERE rowid = ?",
                    (int(idx) + 1,),
                ).fetchone()
                if row:
                    results.append((
                        {
                            "chunk_id": row[0], "text": row[1], "source_file": row[2],
                            "file_type": row[3], "chunk_index": row[4], "total_chunks": row[5],
                            "metadata": json.loads(row[6]) if row[6] else {},
                        },
                        float(dist),
                    ))
            return results
        finally:
            conn.close()

    def keyword_search(self, query, top_k=5):
        conn = self._get_db()
        try:
            rows = conn.execute(
                "SELECT c.chunk_id, c.text, c.source_file, c.file_type, c.chunk_index, c.total_chunks, c.metadata "
                "FROM chunks c JOIN chunks_fts fts ON c.id = fts.rowid "
                "WHERE chunks_fts MATCH ? LIMIT ?",
                (query, top_k),
            ).fetchall()
            results = []
            for row in rows:
                d = {
                    "chunk_id": row[0], "text": row[1], "source_file": row[2],
                    "file_type": row[3], "chunk_index": row[4], "total_chunks": row[5],
                    "metadata": json.loads(row[6]) if row[6] else {},
                    "_match_type": "keyword",
                }
                results.append((d, 1.0))
            return results
        finally:
            conn.close()

    def hybrid_search(self, query, api_key, top_k=5):
        vec_results = self.search(query, api_key, top_k)
        fts_results = self.keyword_search(query, top_k)

        merged = {}
        for r, score in fts_results:
            cid = r.get("chunk_id", "")
            r["_match_type"] = "keyword"
            merged[cid] = (r, score + 1.0)

        for r, score in vec_results:
            cid = r.get("chunk_id", "")
            r["_match_type"] = r.get("_match_type", "vector")
            if cid not in merged:
                merged[cid] = (r, score)
            else:
                _, existing_score = merged[cid]
                merged[cid] = (r, score + existing_score)

        sorted_results = sorted(merged.values(), key=lambda x: x[1], reverse=True)
        return sorted_results[:top_k]

    def count(self):
        if self.index is None:
            return 0
        return self.index.ntotal

    def get_file_list(self):
        conn = self._get_db()
        try:
            rows = conn.execute(
                "SELECT source_file, COUNT(*) as cnt FROM chunks GROUP BY source_file ORDER BY source_file"
            ).fetchall()
            results = []
            for r in rows:
                path = r[0]
                # Derive file extension for type classification
                ext = os.path.splitext(path)[1].lower()
                type_map = {
                    '.docx': 'word', '.doc': 'word',
                    '.xlsx': 'excel', '.xls': 'excel', '.csv': 'excel',
                    '.pdf': 'pdf',
                    '.vsdx': 'visio',
                    '.zip': 'archive', '.rar': 'archive',
                    '.png': 'image', '.jpg': 'image', '.jpeg': 'image', '.bmp': 'image', '.tiff': 'image', '.tif': 'image',
                    '.pptx': 'ppt', '.ppt': 'ppt',
                    '.dxf': 'cad',
                    '.txt': 'text', '.md': 'text',
                }
                results.append({
                    "path": path,
                    "name": os.path.basename(path),
                    "chunks": r[1],
                    "type": type_map.get(ext, 'other'),
                })
            return results
        finally:
            conn.close()

    def remove_by_source_files(self, file_paths):
        if not file_paths:
            return 0
        conn = self._get_db()
        try:
            rowids = []
            for fpath in file_paths:
                rows = conn.execute(
                    "SELECT id FROM chunks WHERE source_file = ?", (fpath,)
                ).fetchall()
                rowids.extend([r[0] for r in rows])
            if rowids:
                placeholders = ",".join("?" for _ in rowids)
                conn.execute(
                    f"DELETE FROM chunks WHERE id IN ({placeholders})", rowids
                )
                conn.commit()

            if self.index is not None and rowids:
                self._rebuild_faiss_from_db(conn)
        finally:
            conn.close()
        return len(file_paths)

    def _rebuild_faiss_from_db(self, conn):
        rows = conn.execute("SELECT text FROM chunks ORDER BY id").fetchall()
        if not rows:
            self.index = None
            self.dimension = 0
            return
        self.index = None
        self.dimension = 0

    def train_pq_if_needed(self):
        pass

    def _get_embeddings(self, texts, api_key):
        if not api_key:
            return None
        embedding_key = api_key or config.EMBEDDING_API_KEY
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
                    if attempt < retries - 1 and e.code in (429, 500, 502, 503):
                        time.sleep(2 ** attempt)
                        continue
                    body = e.read().decode("utf-8", errors="replace")
                    raise Exception(f"Embedding API error {e.code}: {body}")
                except Exception as e:
                    if attempt < retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    raise
        return all_embeddings

    @staticmethod
    def _truncate_text(text, max_chars=3000):
        if len(text) > max_chars:
            return text[:max_chars]
        return text


_stores = {}


def get_store(kb_name=None):
    if kb_name is None:
        kb_name = config.CURRENT_KB
    if kb_name not in _stores:
        _stores[kb_name] = VectorStore(kb_name)
    return _stores[kb_name]
