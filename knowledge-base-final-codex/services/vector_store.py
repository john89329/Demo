import os
import json
import re
import time
import logging
import sqlite3
import threading
import numpy as np
import urllib.request
import urllib.error

import faiss

_logger = logging.getLogger("kb.vector_store")
import config


def segment_chinese_query(query):
    """Pre-process Chinese text into bigrams for FTS5 matching.

    FTS5's default tokenizer does not segment CJK characters, so a query
    like "质量管理体系" would only match that exact phrase.  We convert
    Chinese runs into character bigrams (e.g. "质量管理体系" becomes
    ``"质量" OR "量管" OR "管理" OR "理体" OR "体系"``) to improve recall.

    Non-Chinese tokens (ASCII words) are preserved as-is.
    """
    # Split into Chinese runs and non-Chinese runs
    parts = re.split(r'([一-鿿]+)', query)
    result = []
    for part in parts:
        if not part:
            continue
        if re.match(r'^[一-鿿]+$', part):
            # Generate bigrams for Chinese segments
            for i in range(len(part) - 1):
                result.append(part[i:i + 2])
        else:
            # Keep ASCII words as-is — FTS5 handles those natively
            tokens = part.strip().split()
            result.extend(tokens)
    if not result:
        return query
    return ' OR '.join(f'"{t}"' for t in result)


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
        self.index = faiss.IndexIDMap(faiss.IndexFlatIP(dim))

    def _ensure_id_index(self):
        if self.index is None:
            return
        if isinstance(self.index, faiss.IndexIDMap):
            return
        base_index = self.index
        if base_index.ntotal == 0:
            self.index = faiss.IndexIDMap(faiss.IndexFlatIP(self.dimension))
            return
        # Legacy index with data: rebuild as IndexIDMap from the DB vector store
        # instead of discarding all data.
        conn = self._get_db()
        try:
            self._rebuild_faiss_from_db(conn)
        finally:
            conn.close()

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
                metadata TEXT,
                vector BLOB
            )"""
        )
        cols = {row[1] for row in conn.execute("PRAGMA table_info(chunks)").fetchall()}
        if "vector" not in cols:
            conn.execute("ALTER TABLE chunks ADD COLUMN vector BLOB")
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
        if len(chunks) != len(vectors):
            raise ValueError(f"Chunk/vector count mismatch: {len(chunks)} chunks, {len(vectors)} vectors")
        dim = len(vectors[0])
        with self._lock:
            if self.index is not None and self.dimension != dim:
                raise ValueError(f"Embedding dimension changed: {self.dimension} -> {dim}")
            if self.index is None:
                self._init_index(dim)
            else:
                self._ensure_id_index()
                if self.index is None:
                    self._init_index(dim)

            vec_array = np.array(vectors, dtype=np.float32)
            faiss.normalize_L2(vec_array)

            conn = self._get_db()
            try:
                rowids = []
                for c, vec in zip(chunks, vec_array):
                    existing = conn.execute(
                        "SELECT id FROM chunks WHERE chunk_id = ?", (c["chunk_id"],)
                    ).fetchone()
                    if existing and self.index is not None:
                        self.index.remove_ids(np.array([existing[0]], dtype=np.int64))
                    cur = conn.execute(
                        "INSERT OR REPLACE INTO chunks "
                        "(chunk_id, text, source_file, file_type, chunk_index, total_chunks, metadata, vector) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            c["chunk_id"],
                            c["text"],
                            c["source_file"],
                            c["file_type"],
                            c["chunk_index"],
                            c["total_chunks"],
                            json.dumps(c.get("metadata", {}), ensure_ascii=False),
                            vec.astype(np.float32).tobytes(),
                        ),
                    )
                    rowids.append(cur.lastrowid or (existing[0] if existing else None))
                conn.commit()
                ids = np.array([rid for rid in rowids if rid is not None], dtype=np.int64)
                if len(ids) != len(vec_array):
                    raise RuntimeError("Could not map every chunk to a SQLite row id")
                self.index.add_with_ids(vec_array, ids)
            finally:
                conn.close()
        return len(chunks)

    def search(self, query, api_key, top_k=5):
        if self.index is None or self.index.ntotal == 0:
            return []
        self._ensure_id_index()
        if self.index is None:
            conn = self._get_db()
            try:
                self._rebuild_faiss_from_db(conn)
            finally:
                conn.close()
        if self.index is None or self.index.ntotal == 0:
            return self.keyword_search(query, top_k)
        query_vecs = self._get_embeddings([query], api_key)
        if query_vecs is None or len(query_vecs) == 0:
            return self.keyword_search(query, top_k)

        q = np.array([query_vecs[0]], dtype=np.float32)
        faiss.normalize_L2(q)
        # Fetch more candidates than top_k for better diversity
        fetch_k = max(top_k * 5, min(200, self.index.ntotal))
        distances, indices = self.index.search(q, fetch_k)

        conn = self._get_db()
        try:
            results = []
            for dist, idx in zip(distances[0], indices[0]):
                if idx < 0:
                    continue
                row = conn.execute(
                    "SELECT chunk_id, text, source_file, file_type, chunk_index, total_chunks, metadata FROM chunks WHERE id = ?",
                    (int(idx),),
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

    # ── Chinese bigram segmentation ──────────────────────────

    def keyword_search(self, query, top_k=5):
        # Fetch more candidates for better recall; hybrid_search will trim to top_k
        fetch_k = max(top_k * 5, 200)
        search_query = segment_chinese_query(query)
        conn = self._get_db()
        try:
            try:
                rows = conn.execute(
                    "SELECT c.chunk_id, c.text, c.source_file, c.file_type, c.chunk_index, c.total_chunks, c.metadata "
                    "FROM chunks c JOIN chunks_fts fts ON c.id = fts.rowid "
                    "WHERE chunks_fts MATCH ? LIMIT ?",
                    (search_query, fetch_k),
                ).fetchall()
            except Exception:
                # FTS5 syntax error — fall back to raw query
                rows = conn.execute(
                    "SELECT c.chunk_id, c.text, c.source_file, c.file_type, c.chunk_index, c.total_chunks, c.metadata "
                    "FROM chunks c JOIN chunks_fts fts ON c.id = fts.rowid "
                    "WHERE chunks_fts MATCH ? LIMIT ?",
                    (query, fetch_k),
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
        """Hybrid search with file-diversity re-ranking.

        Fetches more candidates from both vector and keyword searches, then
        applies a diversity pass to ensure results span multiple source files
        rather than clustering on a few high-scoring documents.
        """
        # Fetch more candidates for broader recall
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

        # Score-sort all candidates
        all_candidates = sorted(merged.values(), key=lambda x: x[1], reverse=True)

        # ── File-diversity pass ──
        # First, take the best result from each unique source file.
        # Then fill remaining slots with the best remaining results.
        seen_files = set()
        diverse = []
        rest = []
        for chunk, score in all_candidates:
            f = chunk.get("source_file", "")
            if f and f not in seen_files:
                seen_files.add(f)
                diverse.append((chunk, score))
            else:
                rest.append((chunk, score))

        # Combine: diversity-first results, then fill with best remaining
        if len(diverse) >= top_k:
            return diverse[:top_k]
        return diverse + rest[:top_k - len(diverse)]

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

    def get_chunks_by_source(self, source_files=None):
        """Return all chunk dicts, optionally filtered by source_file.

        Returns list of chunk dicts sorted by (source_file, chunk_index).
        """
        conn = self._get_db()
        try:
            if source_files:
                placeholders = ",".join("?" for _ in source_files)
                rows = conn.execute(
                    f"SELECT chunk_id, text, source_file, file_type, chunk_index, "
                    f"total_chunks, metadata FROM chunks "
                    f"WHERE source_file IN ({placeholders}) "
                    f"ORDER BY source_file, chunk_index",
                    source_files,
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT chunk_id, text, source_file, file_type, chunk_index, "
                    "total_chunks, metadata FROM chunks "
                    "ORDER BY source_file, chunk_index"
                ).fetchall()
            return [
                {
                    "chunk_id": row[0], "text": row[1], "source_file": row[2],
                    "file_type": row[3], "chunk_index": row[4], "total_chunks": row[5],
                    "metadata": json.loads(row[6]) if row[6] else {},
                }
                for row in rows
            ]
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
                # 1. Remove from FAISS incrementally (O(removed), not O(total))
                with self._lock:
                    if self.index is not None and self.index.ntotal > 0:
                        self._ensure_id_index()
                        try:
                            self.index.remove_ids(np.array(rowids, dtype=np.int64))
                        except Exception:
                            # Fallback: if remove_ids fails (e.g. unsupported index type),
                            # rebuild the entire FAISS index from the DB
                            _logger.warning(
                                "FAISS remove_ids failed for %d ids, falling back to full rebuild",
                                len(rowids),
                            )
                            self._rebuild_faiss_from_db(conn)

                # 2. Remove from SQLite (FTS5 triggers handle FTS cleanup)
                placeholders = ",".join("?" for _ in rowids)
                conn.execute(
                    f"DELETE FROM chunks WHERE id IN ({placeholders})", rowids
                )
                conn.commit()
        finally:
            conn.close()
        return len(file_paths)

    def _rebuild_faiss_from_db(self, conn):
        rows = conn.execute("SELECT id, vector FROM chunks WHERE vector IS NOT NULL ORDER BY id").fetchall()
        if not rows:
            self.index = None
            self.dimension = 0
            return
        ids = []
        vectors = []
        for rowid, blob in rows:
            vec = np.frombuffer(blob, dtype=np.float32).copy()
            if vec.size == 0:
                continue
            ids.append(rowid)
            vectors.append(vec)
        if not vectors:
            self.index = None
            self.dimension = 0
            return
        dim = len(vectors[0])
        self._init_index(dim)
        vec_array = np.vstack(vectors).astype(np.float32)
        faiss.normalize_L2(vec_array)
        self.index.add_with_ids(vec_array, np.array(ids, dtype=np.int64))

    def train_pq_if_needed(self):
        """Switch from IndexFlatIP to IndexIVFPQ when vector count exceeds threshold.

        For small indexes (< FAISS_TRAIN_THRESHOLD) this is a no-op — brute-force
        FlatIP is fast enough.  Above the threshold we train a Product Quantizer
        to compress vectors and accelerate search ~20× with ~10× less memory.
        """
        threshold = config.FAISS_TRAIN_THRESHOLD
        if self.index is None or self.index.ntotal < threshold:
            return False

        self._ensure_id_index()
        # Check whether already quantized
        base = self.index
        if hasattr(base, "index"):
            base = base.index
        if hasattr(base, "quantizer"):
            return False  # Already a quantized index type

        dim = self.dimension
        nlist = min(config.FAISS_NLIST, max(4, int(self.index.ntotal ** 0.5)))
        quantizer = faiss.IndexFlatIP(dim)
        pq_index = faiss.IndexIVFPQ(quantizer, dim, nlist,
                                    config.FAISS_PQ_M, config.FAISS_PQ_BITS)
        pq_index.nprobe = config.FAISS_NPROBE

        # Reconstruct all vectors from SQLite for training
        conn = self._get_db()
        try:
            rows = conn.execute(
                "SELECT id, vector FROM chunks WHERE vector IS NOT NULL ORDER BY id"
            ).fetchall()
            ids = []
            vectors = []
            for rowid, blob in rows:
                vec = np.frombuffer(blob, dtype=np.float32).copy()
                if vec.size == dim:
                    ids.append(rowid)
                    vectors.append(vec)
            if len(vectors) < threshold:
                return False
            train_vecs = np.vstack(vectors).astype(np.float32)
            faiss.normalize_L2(train_vecs)
            pq_index.train(train_vecs)
            pq_index.add_with_ids(train_vecs, np.array(ids, dtype=np.int64))
            self.index = faiss.IndexIDMap(pq_index)
            _logger.info(
                "PQ trained: nlist=%d pq_m=%d pq_bits=%d nprobe=%d vectors=%d",
                nlist, config.FAISS_PQ_M, config.FAISS_PQ_BITS, pq_index.nprobe,
                len(ids),
            )
            return True
        finally:
            conn.close()

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
                    body = e.read().decode("utf-8", errors="replace")
                    if e.code == 413 and len(batch) > 0:
                        half = [t[:len(t)//2] for t in batch]
                        _logger.warning("413 text too long, retrying with half-length (%d items)", len(batch))
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
