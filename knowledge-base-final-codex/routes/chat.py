"""Chat routes — RAG Q&A, file upload, test single file, history."""

import os
import time

from flask import Blueprint, request, jsonify

import config
from services.document_parser import parse_file
from services.text_chunker import chunk_document
from services.rag_qa import ask_question
from services.audit_log import active_session
from services.embed_key_router import pick_key
from utils import get_logger, parse_via_subprocess

chat_bp = Blueprint("chat", __name__)
_logger = get_logger()


@chat_bp.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json() or {}
    query = data.get("query", "").strip()
    api_key = data.get("api_key", config.DEEPSEEK_API_KEY)
    kb_name = data.get("kb_name", config.CURRENT_KB)

    if not query:
        return jsonify({"success": False, "error": "请输入问题"})
    if not api_key:
        return jsonify({"success": False, "error": "请先配置 DeepSeek API 密钥"})

    embedding_api_key = data.get("embedding_api_key", "")
    if embedding_api_key:
        config.EMBEDDING_API_KEY = embedding_api_key
    embed_key = pick_key(embedding_api_key)
    context = data.get("context", "")

    try:
        with active_session(kb_name) as session:
            recent = [{"role": t["role"], "content": t["content"], "sources": t.get("sources", [])}
                      for t in session.turns[-50:]] if session.turns else None
            result = ask_question(query, api_key, embed_key=embed_key, history=recent, kb_name=kb_name, context=context)

            session.add_turn("user", query)
            session.add_turn("assistant", result["answer"], result.get("sources", []))

        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "error": f"问答失败: {str(e)}"})


@chat_bp.route("/api/chat/upload", methods=["POST"])
def upload_temp_file():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "请选择文件"})
    file = request.files["file"]
    if not file.filename:
        return jsonify({"success": False, "error": "请选择文件"})

    ext = os.path.splitext(file.filename)[1].lower()
    tmpdir = os.path.join(config.BASE_DIR, "temp_uploads")
    os.makedirs(tmpdir, exist_ok=True)
    tmp_path = os.path.join(tmpdir, file.filename)
    file.save(tmp_path)

    try:
        plain_text_exts = {".txt", ".csv", ".md", ".py", ".json", ".xml", ".html", ".htm", ".log"}
        if ext in plain_text_exts:
            with open(tmp_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        elif ext in config.SUPPORTED_EXTENSIONS:
            result = parse_file(tmp_path)
            content = result.content
        else:
            return jsonify({"success": False, "error": f"不支持的文件格式: {ext}"})
        return jsonify({"success": True, "data": {"file_name": file.filename, "content": content}})
    except Exception as e:
        return jsonify({"success": False, "error": f"文件解析失败: {str(e)}"})
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


@chat_bp.route("/api/kb/test-file", methods=["POST"])
def test_single_file():
    data = request.get_json() or {}
    fpath = data.get("path", "").strip()
    if not fpath:
        return jsonify({"success": False, "error": "文件路径为空"})
    if not os.path.exists(fpath):
        return jsonify({"success": False, "error": f"文件不存在: {fpath}"})

    fname = os.path.basename(fpath)
    ext = os.path.splitext(fpath)[1].lower()
    if ext not in config.SUPPORTED_EXTENSIONS and ext not in {".txt", ".csv", ".md"}:
        return jsonify({"success": False, "error": f"不支持的文件格式: {ext}"})

    # 检测是否需要 OCR，使用对应的超时时间
    from services.pipeline import _needs_ocr
    needs_ocr = _needs_ocr(fpath)
    timeout = config.OCR_PARSE_TIMEOUT if needs_ocr else config.PARSE_TIMEOUT

    _logger.info("TestFile: 开始解析: %s (OCR=%s, timeout=%ds)", fname, needs_ocr, timeout)
    t0 = time.time()
    try:
        parsed = parse_via_subprocess(fpath, timeout=timeout)
    except Exception as e:
        elapsed = time.time() - t0
        _logger.error("TestFile: %s 解析失败 (%.1fs): %s", fname, elapsed, e)
        return jsonify({"success": False, "error": str(e)})
    elapsed = time.time() - t0
    chunks = chunk_document(parsed)

    _logger.info("TestFile: %s 解析完成: %d 字符, %d 块, 耗时 %.1fs", fname, len(parsed.content), len(chunks), elapsed)
    return jsonify({"success": True, "data": {
        "file_name": parsed.file_name,
        "file_type": parsed.file_type,
        "content_length": len(parsed.content),
        "chunks": len(chunks),
        "content_preview": parsed.content[:2000],
        "pages": parsed.pages,
        "elapsed": round(elapsed, 1),
    }})



@chat_bp.route("/api/chat/history", methods=["GET"])
def get_chat_history():
    kb_name = request.args.get("kb_name", config.CURRENT_KB)
    sid = request.args.get("session_id")
    if sid:
        from services.audit_log import get_session
        session = get_session(kb_name, sid)
        if not session:
            return jsonify({"success": True, "data": []})
        return jsonify({"success": True, "data": [{
            "role": t["role"], "content": t["content"],
            "sources": t.get("sources", [])
        } for t in session.turns]})
    return jsonify({"success": True, "data": []})


@chat_bp.route("/api/chat/clear", methods=["POST"])
def clear_chat_history():
    data = request.get_json(silent=True) or {}
    kb_name = data.get("kb_name", config.CURRENT_KB)
    sid = data.get("session_id")
    if sid:
        from services.audit_log import delete_session
        delete_session(kb_name, sid)
    return jsonify({"success": True, "data": {"message": f"「{kb_name}」对话已清除"}})
