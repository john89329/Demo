import os
import zipfile
import tempfile
import shutil
import traceback
from collections import namedtuple

import fitz  # PyMuPDF — text extraction + OCR rendering
from docx import Document as DocxDocument

ParsedDocument = namedtuple("ParsedDocument", ["file_path", "file_name", "file_type", "content", "pages", "metadata"])


def parse_file(file_path, **kwargs):
    ext = os.path.splitext(file_path)[1].lower()
    parser = PARSER_REGISTRY.get(ext)
    if parser is None:
        raise ValueError(f"Unsupported format: {ext}")
    return parser(file_path, **kwargs)


def _detect_docx_format(file_path):
    """Read file header to determine actual format.

    Returns 'docx' (ZIP/OpenXML), 'doc' (OLE2), or None (unknown).
    .docx files created by WPS Office are sometimes OLE2 binary format
    (.doc) saved with a .docx extension.
    """
    try:
        with open(file_path, "rb") as f:
            header = f.read(8)
    except Exception:
        return None
    if header[:2] == b"PK":
        return "docx"
    if header[:4] == b"\xd0\xcf\x11\xe0":
        return "doc"
    return None


def _extract_text_from_ole2(file_path):
    """Extract readable text from OLE2 (.doc) binary format via olefile.

    Reads the WordDocument stream and extracts UTF-16LE text sequences.
    This is a best-effort fallback for WPS-created files that are actually
    legacy .doc format despite having a .docx extension.
    """
    import olefile

    try:
        ole = olefile.OleFileIO(file_path)
    except Exception:
        return ""

    data = b""
    # Try the main text stream (WordDocument), then fall back to any stream
    for stream_name in ("WordDocument", "1Table", "0Table"):
        if ole.exists(stream_name):
            try:
                data = ole.openstream(stream_name).read()
                if data:
                    break
            except Exception:
                continue
    ole.close()

    if not data:
        return ""

    # Word .doc text is stored as UTF-16LE.  Walk through the byte stream,
    # collecting sequences of valid BMP characters, inserting newlines at gaps.
    text_parts = []
    i = 0
    while i < len(data) - 1:
        code = data[i] | (data[i + 1] << 8)
        if 0x20 <= code <= 0xFFFD and code not in (0xFFFE, 0xFFFF):
            text_parts.append(chr(code))
            i += 2
        else:
            # Non-text byte — insert paragraph break if we just had text
            if text_parts and text_parts[-1] != "\n":
                text_parts.append("\n")
            i += 2

    raw = "".join(text_parts)
    # Collapse whitespace and filter noise lines (keep lines with >30% CJK/ASCII)
    lines = []
    for line in raw.split("\n"):
        stripped = line.strip()
        if len(stripped) < 2:
            continue
        # Count printable CJK + ASCII vs total characters
        printable = sum(1 for c in stripped if "一" <= c <= "鿿" or "　" <= c <= "〿" or "＀" <= c <= "￯" or c.isascii())
        if printable >= len(stripped) * 0.5:
            lines.append(stripped)
    return "\n".join(lines)


def parse_docx(file_path, **kwargs):
    # Detect OLE2 files misnamed as .docx (common WPS Office behavior)
    fmt = _detect_docx_format(file_path)
    if fmt == "doc":
        text = _extract_text_from_ole2(file_path)
        if text:
            return ParsedDocument(
                file_path, os.path.basename(file_path), "word",
                text, [text],
                {"ole2_parsed": True, "warning": "WPS OLE2 .doc 格式，扩展名为 .docx，已尽力提取文本"},
            )
        # OLE2 extraction failed — try python-docx anyway (might be a
        # malformed header), then give up
        try:
            doc = DocxDocument(file_path)
        except Exception:
            return ParsedDocument(
                file_path, os.path.basename(file_path), "word",
                f"[无法解析: {os.path.basename(file_path)} — WPS 旧版 .doc 格式伪装为 .docx，"
                f"请用 WPS 另存为标准 .docx 格式后重试]",
                [],
                {"error": "ole2_doc_misnamed_as_docx"},
            )
    else:
        doc = DocxDocument(file_path)

    content_parts = []
    pages = []
    current_page = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            current_page.append(text)
        if len("\n".join(current_page)) > 3000:
            pages.append("\n".join(current_page))
            current_page = []
    if current_page:
        pages.append("\n".join(current_page))

    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            content_parts.append(text)

    for table in doc.tables:
        rows = []
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            rows.append(" | ".join(cells))
        content_parts.append("\n".join(rows))

    content = "\n".join(content_parts)
    if not pages:
        pages = [content]

    meta = {"paragraphs": len(doc.paragraphs), "tables": len(doc.tables)}
    return ParsedDocument(file_path, os.path.basename(file_path), "word", content, pages, meta)


def parse_xlsx(file_path, **kwargs):
    fmt = _detect_table_format(file_path)
    if fmt != "xlsx":
        # Not a real xlsx — delegate to parse_xls which handles CSV fallback
        return parse_xls(file_path, **kwargs)

    from openpyxl import load_workbook
    wb = load_workbook(file_path, read_only=True, data_only=True)
    content_parts = []
    pages = []
    meta = {"sheets": []}

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        meta["sheets"].append(sheet_name)
        sheet_content = [f"=== Sheet: {sheet_name} ==="]
        for row in ws.iter_rows(values_only=True):
            row_vals = [str(c) if c is not None else "" for c in row]
            line = "\t".join(row_vals).strip()
            if line:
                sheet_content.append(line)
        sheet_text = "\n".join(sheet_content)
        content_parts.append(sheet_text)
        pages.append(sheet_text)

    wb.close()
    content = "\n\n".join(content_parts)
    return ParsedDocument(file_path, os.path.basename(file_path), "excel", content, pages, meta)


def parse_vsdx(file_path, **kwargs):
    content_parts = []
    pages = []
    try:
        with zipfile.ZipFile(file_path, "r") as z:
            from lxml import etree

            pages_xml_path = None
            for name in z.namelist():
                if name.endswith("pages.xml") and "visio" in name:
                    pages_xml_path = name
                    break

            if pages_xml_path is None:
                for name in z.namelist():
                    if "pages" in name and name.endswith(".xml"):
                        pages_xml_path = name
                        break

            page_ids = []
            if pages_xml_path:
                pages_xml = z.read(pages_xml_path)
                root = etree.fromstring(pages_xml)
                ns = {"r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
                for page_elem in root.iter("{*}Page"):
                    pid = page_elem.get("{*}ID") or page_elem.get("ID")
                    if pid:
                        page_ids.append(pid)

            if not page_ids:
                for name in z.namelist():
                    if "page" in name and name.endswith(".xml") and "pages" not in name:
                        page_ids.append(name)

            for pid in page_ids:
                page_xml_path = None
                for name in z.namelist():
                    if pid in name and name.endswith(".xml") and "pages" not in name:
                        page_xml_path = name
                        break

                if page_xml_path is None:
                    for name in z.namelist():
                        if "page" in name and name.endswith(".xml") and "pages" not in name:
                            page_xml_path = name
                            break

                if page_xml_path and page_xml_path in z.namelist():
                    xml_content = z.read(page_xml_path)
                    root = etree.fromstring(xml_content)
                    texts = []
                    for elem in root.iter():
                        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                        if tag == "Text" and elem.text and elem.text.strip():
                            texts.append(elem.text.strip())
                    page_text = "\n".join(texts) if texts else ""
                    if page_text:
                        content_parts.append(f"=== Page: {pid} ===\n{page_text}")
                        pages.append(page_text)

    except Exception:
        pass

    content = "\n\n".join(content_parts) if content_parts else f"[Visio diagram: {os.path.basename(file_path)}]"
    return ParsedDocument(file_path, os.path.basename(file_path), "visio", content, pages, {"pages": len(pages)})


def parse_pdf(file_path, skip_ocr=False):
    content_parts = []
    pages = []
    ocr_used = False
    meta = {}
    text_extracted = False

    # Extract text with PyMuPDF (fitz) — far better for Chinese PDFs than PyPDF2
    try:
        doc = fitz.open(file_path)
        for i, page in enumerate(doc):
            text = page.get_text().strip()
            if text:
                pages.append(text)
                content_parts.append(f"--- Page {i + 1} ---\n{text}")
                text_extracted = True
        doc.close()
    except Exception as e:
        meta["text_extraction_error"] = str(e)[:200]

    # OCR fallback for scanned PDFs (no extractable text layer)
    if not text_extracted and not skip_ocr:
        try:
            from services.ocr_siliconflow import ocr_pdf
            ocr_text, ocr_chunks = ocr_pdf(file_path)
            if ocr_text.strip():
                pages = ocr_chunks if ocr_chunks else [ocr_text]
                content_parts = [ocr_text]
                ocr_used = True
        except Exception as e:
            meta["ocr_error"] = str(e)[:200]

    if not content_parts:
        fname = os.path.basename(file_path)
        if meta.get("ocr_error"):
            content_parts.append(f"[PDF OCR 失败: {fname} — {meta['ocr_error']}]")
        elif meta.get("text_extraction_error"):
            content_parts.append(f"[PDF 文本提取失败: {fname} — {meta['text_extraction_error']}]")
        else:
            content_parts.append(f"[PDF: {fname} — 无可提取文本层且 OCR 未启用]")

    content = "\n\n".join(content_parts)
    meta["pages"] = len(pages)
    if ocr_used:
        meta["ocr_used"] = True
    return ParsedDocument(file_path, os.path.basename(file_path), "pdf", content, pages, meta)


def parse_archive(file_path, **kwargs):
    ext = os.path.splitext(file_path)[1].lower()
    content_parts = []
    all_docs = []
    temp_dir = tempfile.mkdtemp()

    try:
        if ext == ".zip":
            with zipfile.ZipFile(file_path, "r", metadata_encoding="utf-8") as z:
                # 逐文件解压，修复 Windows 不兼容的路径问题：
                # 1. 目录名尾部空格 → Windows 文件系统自动去掉 → 路径不匹配
                # 2. WPS/MS Office 临时文件 (~$开头或.~开头) → 跳过
                created_dirs = set()
                for info in z.infolist():
                    # 去掉每个路径组件的首尾空格（Windows 不保留尾部空格）
                    parts = [p.strip() for p in info.filename.split('/') if p.strip()]
                    if not parts:
                        continue
                    # 跳过临时文件（仅检查文件名部分，不影响目录结构）
                    if parts[-1].startswith('.~') or parts[-1].startswith('~$'):
                        continue
                    target = os.path.join(temp_dir, *parts)
                    if info.is_dir():
                        os.makedirs(target, exist_ok=True)
                        created_dirs.add(target)
                    else:
                        if target in created_dirs:
                            continue  # 文件名与已创建的目录同名 → 跳过
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        with z.open(info) as src, open(target, 'wb') as dst:
                            dst.write(src.read())
        elif ext == ".rar":
            try:
                import rarfile
            except ImportError:
                return ParsedDocument(file_path, os.path.basename(file_path), "archive",
                                      f"[Archive: {os.path.basename(file_path)} (rarfile library not available)]",
                                      [], {"error": "rarfile library not available"})
            try:
                with rarfile.RarFile(file_path) as rf:
                    rf.extractall(temp_dir)
            except rarfile.RarCannotExec:
                return ParsedDocument(file_path, os.path.basename(file_path), "archive",
                                      f"[Archive: {os.path.basename(file_path)} (unrar 工具未安装，无法解压)]",
                                      [], {"error": "unrar tool not installed"})

        for root, dirs, files in os.walk(temp_dir):
            for f in files:
                fpath = os.path.join(root, f)
                fext = os.path.splitext(f)[1].lower()
                if fext in PARSER_REGISTRY and fext not in (".zip", ".rar"):
                    try:
                        doc = PARSER_REGISTRY[fext](fpath, **kwargs)
                        all_docs.append(doc)
                        content_parts.append(f"[From archive: {os.path.basename(file_path)}/{f}]\n{doc.content}")
                    except Exception:
                        pass
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    content = "\n\n".join(content_parts) if content_parts else f"[Archive: {os.path.basename(file_path)} (no supported files found)]"
    return ParsedDocument(file_path, os.path.basename(file_path), "archive", content, [content], {"extracted_files": len(all_docs)})


def parse_image(file_path, **kwargs):
    from services.ocr_siliconflow import ocr_image
    text = ocr_image(file_path)
    if not text.strip():
        text = f"[Image: {os.path.basename(file_path)} (no text recognized)]"
    return ParsedDocument(file_path, os.path.basename(file_path), "image", text, [text], {"ocr_used": True})


def _detect_table_format(file_path):
    """Read file header to determine actual spreadsheet format.

    Returns 'xlsx' (ZIP/OpenXML), 'xls' (OLE2), 'csv' (text), or None.
    """
    try:
        with open(file_path, "rb") as f:
            header = f.read(8)
    except Exception:
        return None
    if header[:2] == b"PK":
        return "xlsx"
    if header[:4] == b"\xd0\xcf\x11\xe0":
        return "xls"
    # Looks like text — CSV / TSV
    if all(0x20 <= b <= 0x7E or b in (0x0D, 0x0A, 0x09) for b in header):
        return "csv"
    return None


def _parse_csv_as_table(file_path):
    """Fallback: parse a CSV/TSV text file as tabular data."""
    import csv
    content_parts = []
    # Sniff delimiter
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        sample = f.read(8192)
    sniffer = csv.Sniffer()
    try:
        dialect = sniffer.sniff(sample, delimiters=",\t;|")
    except Exception:
        dialect = csv.excel
    lines = []
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f, dialect)
        for row in reader:
            line = "\t".join(cell.strip() for cell in row if cell.strip())
            if line:
                lines.append(line)
    text = "\n".join(lines)
    return text if text else None


def parse_xls(file_path, **kwargs):
    fmt = _detect_table_format(file_path)

    # OLE2 → xlrd
    if fmt == "xls":
        import xlrd
        try:
            wb = xlrd.open_workbook(file_path)
            content_parts = []
            pages = []
            for sheet_name in wb.sheet_names():
                ws = wb.sheet_by_name(sheet_name)
                sheet_content = [f"=== Sheet: {sheet_name} ==="]
                for row_idx in range(ws.nrows):
                    row_vals = [str(ws.cell_value(row_idx, col_idx)) for col_idx in range(ws.ncols)]
                    line = "\t".join(row_vals).strip()
                    if line:
                        sheet_content.append(line)
                sheet_text = "\n".join(sheet_content)
                content_parts.append(sheet_text)
                pages.append(sheet_text)
            content = "\n\n".join(content_parts)
            return ParsedDocument(file_path, os.path.basename(file_path), "excel", content, pages, {"sheets": len(pages)})
        except Exception as e:
            # xlrd failed — fall through to CSV attempt
            pass

    # ZIP / text / unknown → try CSV
    text = _parse_csv_as_table(file_path)
    if text:
        return ParsedDocument(
            file_path, os.path.basename(file_path), "excel", text, [text],
            {"format": fmt or "unknown", "fallback": "csv"},
        )
    return ParsedDocument(file_path, os.path.basename(file_path), "excel",
                          f"[无法解析: {os.path.basename(file_path)} — 格式不支持或文件损坏]",
                          [], {"error": "unsupported_table_format"})


def parse_doc(file_path, **kwargs):
    try:
        doc = DocxDocument(file_path)
    except Exception:
        return ParsedDocument(file_path, os.path.basename(file_path), "word",
                              f"[Legacy Word document could not be read: {os.path.basename(file_path)}. Try converting to .docx format.]",
                              [], {"error": "legacy .doc format not supported, convert to .docx"})
    content_parts = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            content_parts.append(text)
    content = "\n".join(content_parts) if content_parts else f"[Legacy Word: {os.path.basename(file_path)} (no text extracted)]"
    return ParsedDocument(file_path, os.path.basename(file_path), "word", content, [content], {"legacy": True})


def parse_pptx(file_path, **kwargs):
    from pptx import Presentation
    prs = Presentation(file_path)
    content_parts = []
    pages = []
    for slide_idx, slide in enumerate(prs.slides):
        slide_texts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    text = para.text.strip()
                    if text:
                        slide_texts.append(text)
            if shape.has_table:
                for row in shape.table.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    slide_texts.append(" | ".join(cells))
        slide_content = "\n".join(slide_texts)
        if slide_content.strip():
            pages.append(slide_content)
            content_parts.append(f"--- Slide {slide_idx + 1} ---\n{slide_content}")
    content = "\n\n".join(content_parts) if content_parts else f"[PowerPoint: {os.path.basename(file_path)} (no text extracted)]"
    return ParsedDocument(file_path, os.path.basename(file_path), "ppt", content, pages, {"slides": len(prs.slides)})


def parse_ppt(file_path, **kwargs):
    try:
        return parse_pptx(file_path)
    except Exception:
        return ParsedDocument(file_path, os.path.basename(file_path), "ppt",
                              f"[Legacy PowerPoint could not be read: {os.path.basename(file_path)}. Try converting to .pptx format.]",
                              [], {"error": "legacy .ppt format not supported, convert to .pptx"})


def parse_dxf(file_path, **kwargs):
    import ezdxf
    try:
        doc = ezdxf.readfile(file_path)
    except Exception:
        return ParsedDocument(file_path, os.path.basename(file_path), "cad",
                              f"[DXF file could not be read: {os.path.basename(file_path)}]",
                              [], {"error": "dxf parse failed"})
    lines = []
    msp = doc.modelspace()
    for entity in msp:
        if entity.dxftype() == "TEXT":
            text = entity.dxf.text.strip()
            if text:
                lines.append(text)
        elif entity.dxftype() == "MTEXT":
            text = entity.text.strip()
            if text:
                lines.append(text)
    content = "\n".join(lines) if lines else f"[DXF CAD file: {os.path.basename(file_path)} (no text entities found)]"
    return ParsedDocument(file_path, os.path.basename(file_path), "cad", content, [content], {"entities": len(lines)})


PARSER_REGISTRY = {
    ".docx": parse_docx,
    ".xlsx": parse_xlsx,
    ".vsdx": parse_vsdx,
    ".pdf": parse_pdf,
    ".zip": parse_archive,
    ".rar": parse_archive,
    ".png": parse_image,
    ".jpg": parse_image,
    ".jpeg": parse_image,
    ".bmp": parse_image,
    ".tiff": parse_image,
    ".tif": parse_image,
    ".xls": parse_xls,
    ".csv": parse_xls,  # parse_xls has CSV fallback via _parse_csv_as_table
    ".doc": parse_doc,
    ".pptx": parse_pptx,
    ".ppt": parse_ppt,
    ".dxf": parse_dxf,
}
