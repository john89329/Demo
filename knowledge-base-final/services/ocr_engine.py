import threading
import fitz  # PyMuPDF

_ocr_instance = None
_ocr_lock = threading.Lock()


def _get_ocr():
    global _ocr_instance
    if _ocr_instance is None:
        with _ocr_lock:
            if _ocr_instance is None:
                from rapidocr_onnxruntime import RapidOCR
                _ocr_instance = RapidOCR()
    return _ocr_instance


def _ocr_bytes(data):
    """OCR from bytes (PNG/JPEG image data in memory)."""
    ocr = _get_ocr()
    result, _ = ocr(data)
    if not result:
        return ""
    lines = []
    for line in result:
        text = line[1] if line[1] else ""
        text = text.replace(" ", "")
        if text.strip():
            lines.append(text.strip())
    return "\n".join(lines)


def ocr_image(path):
    with open(path, "rb") as f:
        return _ocr_bytes(f.read())


def ocr_pdf(file_path, max_pages=None):
    doc = fitz.open(file_path)
    all_text = []
    pages_to_process = min(len(doc), max_pages or len(doc))
    for i in range(pages_to_process):
        page = doc[i]
        pix = page.get_pixmap(dpi=150)
        png_bytes = pix.tobytes("png")
        text = _ocr_bytes(png_bytes)
        if text:
            all_text.append(f"--- Page {i + 1} ---\n{text}")
    doc.close()
    return "\n\n".join(all_text)
