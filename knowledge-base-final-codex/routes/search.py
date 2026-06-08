"""Search routes — keyword, vector, hybrid search + evidence matrix."""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Blueprint, request, jsonify

import config
from services.vector_store import get_store
from services.embed_key_router import pick_key

search_bp = Blueprint("search", __name__)


@search_bp.route("/api/kb/search", methods=["POST"])
def search_kb():
    data = request.get_json() or {}
    query = data.get("query", "").strip()
    mode = data.get("mode", "hybrid")
    top_k = int(data.get("top_k", 100))
    kb_name = data.get("kb_name", config.CURRENT_KB)
    api_key = data.get("api_key", config.DEEPSEEK_API_KEY)
    embedding_api_key = data.get("embedding_api_key", "")
    if embedding_api_key:
        config.EMBEDDING_API_KEY = embedding_api_key
    embed_key = pick_key(embedding_api_key)

    if not query:
        return jsonify({"success": False, "error": "请输入搜索关键词"})

    store = get_store(kb_name)
    if store.index is None or store.index.ntotal == 0:
        store.load()

    try:
        if mode == "keyword":
            results = store.keyword_search(query, top_k)
        elif mode == "vector":
            results = store.search(query, embed_key, top_k)
        else:
            results = store.hybrid_search(query, embed_key, top_k)

        return jsonify({
            "success": True,
            "data": {
                "results": [
                    {
                        "chunk_id": r[0]["chunk_id"],
                        "text": r[0]["text"],
                        "source_file": r[0]["source_file"],
                        "file_type": r[0]["file_type"],
                        "chunk_index": r[0]["chunk_index"],
                        "total_chunks": r[0]["total_chunks"],
                        "metadata": r[0]["metadata"],
                        "score": round(r[1], 4),
                        "match_type": r[0].pop("_match_type", None),
                    }
                    for r in results
                ],
                "mode": mode,
                "total": len(results),
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": f"搜索失败: {str(e)}"})


@search_bp.route("/api/kb/evidence-matrix", methods=["POST"])
def evidence_matrix():
    data = request.get_json() or {}
    audit_points = data.get("audit_points", [])
    kb_name = data.get("kb_name", config.CURRENT_KB)
    api_key = data.get("api_key", config.DEEPSEEK_API_KEY)

    if not audit_points or not isinstance(audit_points, list):
        return jsonify({"success": False, "error": "请提供审计要点列表"})
    if not api_key:
        return jsonify({"success": False, "error": "请先配置 API 密钥"})

    embedding_api_key = data.get("embedding_api_key", "")
    if embedding_api_key:
        config.EMBEDDING_API_KEY = embedding_api_key
    embed_key = pick_key(embedding_api_key)

    store = get_store(kb_name)
    if store.index is None or store.index.ntotal == 0:
        store.load()

    total_chunks = store.count()
    if total_chunks == 0:
        return jsonify({"success": False, "error": "当前知识库为空，请先构建索引"})

    try:
        topic_file_map = {}
        file_meta = {}
        valid_points = [p for p in audit_points if p and p.strip()]

        def search_point(point):
            try:
                results = store.hybrid_search(point, embed_key, top_k=30)
            except Exception:
                try:
                    results = store.keyword_search(point, top_k=30)
                except Exception:
                    results = []
            topic_map = {}
            local_meta = {}
            for chunk_data, score in results:
                fpath = chunk_data["source_file"]
                if fpath not in local_meta:
                    local_meta[fpath] = {
                        "file": os.path.basename(fpath),
                        "file_path": fpath,
                        "file_type": chunk_data["file_type"],
                    }
                if fpath not in topic_map:
                    topic_map[fpath] = {"score": 0.0, "chunks": 0}
                topic_map[fpath]["score"] = max(topic_map[fpath]["score"], score)
                topic_map[fpath]["chunks"] += 1
            return point, topic_map, local_meta

        workers = min(config.EVIDENCE_MATRIX_WORKERS, max(1, len(valid_points)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(search_point, p) for p in valid_points]
            for future in as_completed(futures):
                point, topic_map, local_meta = future.result()
                topic_file_map[point] = topic_map
                file_meta.update(local_meta)

        matrix = []
        for fpath, meta in file_meta.items():
            topics_list = []
            max_score = 0.0
            for point in audit_points:
                t = topic_file_map.get(point, {}).get(fpath, {"score": 0.0, "chunks": 0})
                topics_list.append({
                    "topic": point,
                    "score": round(t["score"], 4),
                    "chunks": t["chunks"],
                })
                max_score = max(max_score, t["score"])
            matrix.append({**meta, "topics": topics_list, "max_score": round(max_score, 4)})

        matrix.sort(key=lambda x: x["max_score"], reverse=True)

        coverage = {}
        for point in audit_points:
            fm = topic_file_map.get(point, {})
            scores = [v["score"] for v in fm.values()]
            coverage[point] = {
                "files": len(fm),
                "total_files": len(file_meta),
                "avg_score": round(sum(scores) / len(scores), 4) if scores else 0,
                "max_score": round(max(scores), 4) if scores else 0,
            }

        return jsonify({
            "success": True,
            "data": {
                "matrix": matrix,
                "summary": {
                    "total_files": len(file_meta),
                    "topic_coverage": coverage,
                },
                "audit_points": audit_points,
            }
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        err = str(e)
        if "401" in err or "Invalid token" in err or "Unauthorized" in err:
            return jsonify({"success": False, "error": "Embedding API 密钥无效或未配置，请在设置中配置正确的 Embedding API 密钥，或仅使用关键词搜索模式"})
        return jsonify({"success": False, "error": f"证据矩阵生成失败: {err}"})
