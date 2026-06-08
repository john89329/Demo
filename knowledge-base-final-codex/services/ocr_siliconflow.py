import os
import re
import json
import time
import random
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed

import fitz  # PyMuPDF — PDF page splitting
import config
from utils import get_logger
from services.http_client import get_http_client, HttpError

_logger = get_logger()

_REF_PATTERN = re.compile(r"<\|ref\|>(.*?)<\|/ref\|>", re.DOTALL)


def _call_ocr_api(data: bytes, mime: str) -> str:
    """Send OCR request to DeepSeek-OCR via SiliconFlow. Returns extracted text or ""."""
    raw_key = config.OCR_API_KEY
    if not raw_key:
        _logger.warning("OCR API key not configured")
        return ""

    key_pool = [k.strip() for k in raw_key.split(",") if k.strip()]
    if not key_pool:
        _logger.warning("OCR API key list is empty")
        return ""

    b64 = base64.b64encode(data).decode("ascii")
    payload = {
        "model": config.OCR_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": "<image>\n<|grounding|>OCR this image."},
            ]
        }],
        "max_tokens": 4096,
        "temperature": 0,
    }

    http = get_http_client()
    url = f"{config.OCR_BASE_URL}/chat/completions"

    for attempt in range(min(3, len(key_pool))):
        api_key = key_pool[attempt % len(key_pool)]
        try:
            headers = {"Authorization": f"Bearer {api_key}"}
            result = http.post_json(url, payload, headers=headers, timeout=120)
            content = result["choices"][0]["message"]["content"]
            parts = _REF_PATTERN.findall(content)
            if parts:
                return "\n".join(p.strip() for p in parts if p.strip())
            return content.strip()
        except HttpError as e:
            if e.status in (429, 502, 503) and attempt < len(key_pool) - 1:
                time.sleep(2 ** attempt)
                continue
            _logger.warning("OCR API HTTP %s (key %s...): %s", e.status, api_key[:10], e.body[:200])
            return ""
        except Exception as e:
            err_str = str(e)
            if ("timed out" in err_str or "connection" in err_str.lower() or
                "reset" in err_str.lower()) and attempt < min(2, len(key_pool) - 1):
                time.sleep(2 ** attempt)
                continue
            _logger.warning("OCR API error: %s", err_str)
            return ""
    return ""


def ocr_image(path: str) -> str:
    try:
        ext = os.path.splitext(path)[1].lower()
        mime_map = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".bmp": "image/bmp", ".tiff": "image/tiff", ".tif": "image/tiff",
        }
        with open(path, "rb") as f:
            return _call_ocr_api(f.read(), mime_map.get(ext, "image/png"))
    except Exception:
        return ""


def ocr_pdf(file_path: str, max_pages: int = None):
    """Split PDF into chunks, render at reduced DPI, OCR in parallel via cloud API.

    Large scanned PDFs are split and sent concurrently.  Pages are re-rendered
    at lower DPI to reduce upload size without sacrificing OCR quality (testing
    showed DPI=100 gives identical results to DPI=200 while halving file size).

    Returns (merged_text, per_chunk_texts) tuple where per_chunk_texts is a
    list of OCR results for each chunk in page order.
    """
    try:
        doc = fitz.open(file_path)
    except Exception:
        return "", []

    total = len(doc)
    if max_pages:
        total = min(total, max_pages)

    # Dynamic chunk sizing + DPI based on total pages (validated by real tests)
    if total > 200:
        chunk_size = 10
        dpi = 72
    elif total > 50:
        chunk_size = 20
        dpi = 100
    else:
        chunk_size = 50
        dpi = 100

    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)

    # Phase 1: prepare all chunks in memory with reduced-DPI rendering
    chunks = []  # list of (start_page, pdf_bytes)
    try:
        for start in range(0, total, chunk_size):
            end = min(start + chunk_size, total)
            chunk = fitz.open()
            for i in range(start, end):
                page = doc[i]
                pix = page.get_pixmap(matrix=matrix)
                new_page = chunk.new_page(width=pix.width, height=pix.height)
                new_page.insert_image(new_page.rect, pixmap=pix)
            chunks.append((start, chunk.tobytes()))
            chunk.close()
    finally:
        doc.close()

    # Phase 2: OCR all chunks in parallel, capped to avoid write timeout.
    raw_key = config.OCR_API_KEY or ""
    key_count = max(1, len([k for k in raw_key.split(",") if k.strip()]))
    max_workers = min(5, key_count, len(chunks))
    _logger.info("OCR PDF: %d pages → %d chunks (size=%d, dpi=%d), %d keys → %d workers",
                 total, len(chunks), chunk_size, dpi, key_count, max_workers)
    results = {}  # start_page → text
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_call_ocr_api, pdf_bytes, "application/pdf"): start
            for start, pdf_bytes in chunks
        }
        for f in as_completed(futures):
            start = futures[f]
            try:
                text = f.result()
                if text:
                    results[start] = text
            except Exception:
                pass

    # Phase 3: merge in page order
    all_text = [results[s] for s in sorted(results) if results[s]]
    merged = "\n\n".join(all_text)
    return merged, all_text
