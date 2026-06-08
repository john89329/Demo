import time
import fitz
from rapidocr_onnxruntime import RapidOCR

PDF_PATH = r"D:\云盘\249679522\WPS云盘\10-项目文档\74-北京排水评价手册修订及2026年监督评价\10-已提供资料\产业集团\2.决策管理\1-2-1 董事会记录\3-第一届第五十四次董事会2025-4-27\决议1-54（含授权）.pdf"


def profile_ocr():
    # ============================================================
    # Step 0: Init RapidOCR (one-time cost, lazily done)
    # ============================================================
    t0 = time.time()
    ocr = RapidOCR()
    t_init_ocr = time.time() - t0
    print(f"[0] RapidOCR init: {t_init_ocr:.3f}s")

    # ============================================================
    # Step 1: Open PDF
    # ============================================================
    t0 = time.time()
    doc = fitz.open(PDF_PATH)
    t_open_pdf = time.time() - t0
    print(f"[1] Open PDF ({len(doc)} pages): {t_open_pdf:.3f}s")

    # ============================================================
    # Step 2: Per-page breakdown
    # ============================================================
    total_render = 0.0
    total_to_bytes = 0.0
    total_ocr_call = 0.0
    total_text_extract = 0.0
    all_text = []

    for i in range(len(doc)):
        print(f"\n--- Page {i + 1}/{len(doc)} ---")
        page = doc[i]
        page_rect = page.rect
        print(f"  Page size: {page_rect.width:.0f}x{page_rect.height:.0f} pts")

        # 2a: Render page to pixmap
        t0 = time.time()
        pix = page.get_pixmap(dpi=150)
        t_render = time.time() - t0
        total_render += t_render
        print(f"  [2a] get_pixmap(dpi=150) -> {pix.width}x{pix.height}px: {t_render:.3f}s")

        # 2b: Convert to PNG bytes
        t0 = time.time()
        png_bytes = pix.tobytes("png")
        t_bytes = time.time() - t0
        total_to_bytes += t_bytes
        print(f"  [2b] tobytes(png) -> {len(png_bytes) / 1024:.1f}KB: {t_bytes:.3f}s")

        # 2c: OCR call
        t0 = time.time()
        result, _ = ocr(png_bytes)
        t_ocr = time.time() - t0
        total_ocr_call += t_ocr
        num_lines = len(result) if result else 0
        print(f"  [2c] RapidOCR call ({num_lines} lines detected): {t_ocr:.3f}s")

        # 2d: Extract text from result
        t0 = time.time()
        if result:
            lines = []
            for line in result:
                text = line[1] if line[1] else ""
                text = text.replace(" ", "")
                if text.strip():
                    lines.append(text.strip())
            page_text = "\n".join(lines)
            all_text.append(f"--- Page {i + 1} ---\n{page_text}")
        t_extract = time.time() - t0
        total_text_extract += t_extract
        # Only print if non-trivial
        if t_extract > 0.001:
            print(f"  [2d] Text extraction: {t_extract:.3f}s")

    doc.close()

    # ============================================================
    # Summary
    # ============================================================
    total = t_init_ocr + t_open_pdf + total_render + total_to_bytes + total_ocr_call + total_text_extract
    print(f"\n{'=' * 55}")
    print(f"TIMING SUMMARY")
    print(f"{'=' * 55}")
    print(f"  Init RapidOCR:     {t_init_ocr:8.3f}s  ({t_init_ocr / total * 100:5.1f}%)")
    print(f"  Open PDF:          {t_open_pdf:8.3f}s  ({t_open_pdf / total * 100:5.1f}%)")
    print(f"  Render pages:      {total_render:8.3f}s  ({total_render / total * 100:5.1f}%)")
    print(f"  PNG encode:        {total_to_bytes:8.3f}s  ({total_to_bytes / total * 100:5.1f}%)")
    print(f"  OCR inference:     {total_ocr_call:8.3f}s  ({total_ocr_call / total * 100:5.1f}%)")
    print(f"  Text extraction:   {total_text_extract:8.3f}s  ({total_text_extract / total * 100:5.1f}%)")
    print(f"  {'─' * 45}")
    print(f"  TOTAL:             {total:8.3f}s  (100.0%)")

    # Show extracted text preview
    full_text = "\n\n".join(all_text)
    print(f"\n{'=' * 55}")
    print(f"EXTRACTED TEXT PREVIEW ({len(full_text)} chars total)")
    print(f"{'=' * 55}")
    print(full_text[:2000])


if __name__ == "__main__":
    profile_ocr()
