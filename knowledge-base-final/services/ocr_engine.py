import os
import threading
import fitz  # PyMuPDF

_ocr_instance = None
_ocr_lock = threading.Lock()


def _get_ocr():
    global _ocr_instance
    if _ocr_instance is None:
        with _ocr_lock:
            if _ocr_instance is None:
                from paddleocr import PaddleOCR
                _ocr_instance = PaddleOCR(
                    use_angle_cls=True,
                    lang='ch',
                    use_gpu=False,
                    show_log=False,
                )
    return _ocr_instance


def ocr_image(path):
    ocr = _get_ocr()
    result = ocr.ocr(path, cls=True)
    if not result or not result[0]:
        return ""
    lines = []
    for line in result[0]:
        text = line[1][0] if line[1] else ""
        if text.strip():
            lines.append(text.strip())
    return "\n".join(lines)


def ocr_pdf(file_path, max_pages=None):
    doc = fitz.open(file_path)
    ocr = _get_ocr()
    all_text = []
    pages_to_process = min(len(doc), max_pages or len(doc))
    for i in range(pages_to_process):
        page = doc[i]
        pix = page.get_pixmap(dpi=200)
        img_path = f"{file_path}.ocr_page_{i}.png"
        pix.save(img_path)
        try:
            text = ocr_image(img_path)
            if text:
                all_text.append(f"--- Page {i + 1} ---\n{text}")
        finally:
            try:
                os.remove(img_path)
            except Exception:
                pass
    doc.close()
    return "\n\n".join(all_text)
