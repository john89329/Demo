"""Subprocess worker for file parsing with timeout isolation.

Usage: python parse_worker.py <file_path> [--skip-ocr]
Writes JSON result to stdout (UTF-8 bytes), errors to stderr.
"""
import sys
import json
import os
import logging
import atexit
import traceback

# Ensure the project root is on sys.path so we can import services.*
_project_root = os.path.dirname(os.path.abspath(__file__))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
    

# Suppress noisy library logging in subprocess
os.environ.setdefault("GLOG_minloglevel", "3")
logging.getLogger().setLevel(logging.WARNING)
logging.getLogger("xlrd").setLevel(logging.ERROR)

# Redirect text-mode stdout to devnull to catch library warnings (e.g. xlrd
# OLE2 warnings). Save the original buffer so _write_json still works.
sys.stdout = open(os.devnull, "w", encoding="utf-8")


def _write_json(obj):
    """Write JSON to stdout as UTF-8 bytes, bypassing GBK console encoding."""
    json_str = json.dumps(obj, ensure_ascii=False)
    # Write directly to the original stdout buffer (bypassing the redirected
    # text-mode wrapper used to suppress library warnings).
    sys.__stdout__.buffer.write(json_str.encode("utf-8"))
    sys.__stdout__.buffer.flush()


def main():
    if len(sys.argv) < 2:
        _write_json({"status": "error", "error": "Usage: parse_worker.py <file_path> [--skip-ocr]"})
        os._exit(1)

    fpath = sys.argv[1]
    skip_ocr = "--skip-ocr" in sys.argv

    try:
        from services.document_parser import parse_file

        parsed = parse_file(fpath, skip_ocr=skip_ocr)

        _write_json({
            "status": "ok",
            "data": {
                "file_path": parsed.file_path,
                "file_name": parsed.file_name,
                "file_type": parsed.file_type,
                "content": parsed.content,
                "pages": parsed.pages,
                "metadata": parsed.metadata,
            },
        })
    except Exception as e:
        _write_json({
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc(),
        })
        os._exit(1)


if __name__ == "__main__":
    main()
