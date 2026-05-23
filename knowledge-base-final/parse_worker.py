"""Subprocess worker for file parsing with timeout isolation.

Usage: python parse_worker.py <file_path> [--skip-ocr]
Writes JSON result to stdout (UTF-8 bytes), errors to stderr.
"""
import sys
import json
import os
import traceback

# Ensure the project root is on sys.path so we can import services.*
_project_root = os.path.dirname(os.path.abspath(__file__))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Suppress noisy library logging in subprocess
os.environ.setdefault("GLOG_minloglevel", "3")
os.environ.setdefault("PADDLEOCR_DISABLE_AUTO_LOGGING_CONFIG", "1")


def _write_json(obj):
    """Write JSON to stdout as UTF-8 bytes, bypassing GBK console encoding."""
    json_str = json.dumps(obj, ensure_ascii=False)
    sys.stdout.buffer.write(json_str.encode("utf-8"))
    sys.stdout.buffer.flush()


def main():
    if len(sys.argv) < 2:
        _write_json({"status": "error", "error": "Usage: parse_worker.py <file_path> [--skip-ocr]"})
        sys.exit(1)

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
        sys.exit(1)


if __name__ == "__main__":
    main()
