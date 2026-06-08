"""Utility functions — logging, subprocess helpers, misc.

Extracted from app.py.
"""

import os
import re
import sys
import logging
from logging.handlers import RotatingFileHandler

import config
from services.document_parser import ParsedDocument


# ── Token estimation ──────────────────────────────────────

def estimate_tokens(text):
    """Estimate token count for mixed Chinese/English text.

    Conservative coefficients tuned for DeepSeek-family BPE tokenizers:
      - CJK characters × 1.5
      - Other non-whitespace characters × 0.25
    Always rounds up to avoid underestimating.
    """
    if not text:
        return 0
    cjk = 0
    other = 0
    for ch in text:
        if ch.isspace():
            continue
        cp = ord(ch)
        if (0x4E00 <= cp <= 0x9FFF or      # CJK Unified Ideographs
            0x3400 <= cp <= 0x4DBF or      # CJK Extension A
            0x20000 <= cp <= 0x2A6DF or    # CJK Extension B
            0xF900 <= cp <= 0xFAFF or      # CJK Compatibility Ideographs
            0x3040 <= cp <= 0x309F or      # Hiragana
            0x30A0 <= cp <= 0x30FF or      # Katakana
            0xAC00 <= cp <= 0xD7AF):       # Hangul Syllables
            cjk += 1
        else:
            other += 1
    return int(cjk * 1.5 + other * 0.25) + 1


def estimate_message_tokens(messages):
    """Estimate total tokens for a list of {"role", "content"} messages.

    Adds per-message framing overhead (~4 tokens each).
    """
    total = 0
    for msg in messages:
        total += 4
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    total += estimate_tokens(part.get("text", ""))
    return total

# ── Logging setup ────────────────────────────────────────

_log_format = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_file_handler = RotatingFileHandler(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.log"),
    maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8",
)
_file_handler.setFormatter(_log_format)
_file_handler.setLevel(logging.DEBUG)

_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setFormatter(_log_format)
_console_handler.setLevel(logging.INFO)

_logger = logging.getLogger("kb")
_logger.setLevel(logging.DEBUG)
_logger.addHandler(_file_handler)
_logger.addHandler(_console_handler)

# Capture Flask's own logs
logging.getLogger("werkzeug").handlers = []
logging.getLogger("werkzeug").addHandler(_file_handler)


def get_logger():
    return _logger


# ── Python executable ────────────────────────────────────

def get_python_exe():
    """Return python.exe for subprocess launching.

    Prefer the venv311 Python so that subprocess workers have access to
    pymupdf and all other installed packages.
    """
    root_dir = os.path.dirname(os.path.abspath(__file__))
    venv_python = os.path.join(root_dir, "venv311", "Scripts", "python.exe")
    if os.path.exists(venv_python):
        return venv_python
    exe = sys.executable
    if exe.endswith("pythonw.exe"):
        exe = exe[:-len("pythonw.exe")] + "python.exe"
    return exe


# ── Subprocess parser ────────────────────────────────────

def parse_via_subprocess(fpath, skip_ocr=False, timeout=None):
    """Parse a file in a subprocess via parse_worker.py. Returns ParsedDocument or raises."""
    import subprocess
    import json

    root_dir = os.path.dirname(os.path.abspath(__file__))
    worker_path = os.path.join(root_dir, "parse_worker.py")

    if not os.path.exists(worker_path):
        raise RuntimeError(f"parse_worker.py 不存在: {worker_path}")

    python_exe = get_python_exe()
    cmd = [python_exe, worker_path, fpath]
    if skip_ocr:
        cmd.append("--skip-ocr")
    timeout_val = timeout or config.PARSE_TIMEOUT

    # Pass API keys to subprocess so OCR/embedding can access them
    subprocess_env = os.environ.copy()
    if config.OCR_API_KEY:
        subprocess_env["OCR_API_KEY"] = config.OCR_API_KEY
    if config.DEEPSEEK_API_KEY:
        subprocess_env["DEEPSEEK_API_KEY"] = config.DEEPSEEK_API_KEY
    if config.EMBEDDING_API_KEY:
        subprocess_env["EMBEDDING_API_KEY"] = config.EMBEDDING_API_KEY

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            encoding="utf-8",
            timeout=timeout_val,
            cwd=root_dir,
            env=subprocess_env,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except subprocess.TimeoutExpired as e:
        stderr_raw = e.stderr
        if isinstance(stderr_raw, bytes):
            stderr_raw = stderr_raw.decode("utf-8", errors="replace")
        stderr_tail = stderr_raw[-500:] if stderr_raw else ""
        raise RuntimeError(
            f"解析超时（>{timeout_val}秒）。"
            f"手动测试: python parse_worker.py \"{os.path.basename(fpath)}\""
            f"{' --skip-ocr' if skip_ocr else ''}"
            f"\nstderr: {stderr_tail or '无'}"
        )

    stdout_text = proc.stdout.strip() if proc.stdout else ""
    stderr_text = proc.stderr.strip()[:800] if proc.stderr else ""

    if proc.returncode != 0 or not stdout_text:
        raise RuntimeError(
            f"子进程退出码 {proc.returncode}\n"
            f"Python: {python_exe}\n"
            f"stdout: {stdout_text[:500] or '空'}\n"
            f"stderr: {stderr_text or '无'}\n"
            f"手动测试: cd \"{root_dir}\" && python parse_worker.py \"{os.path.basename(fpath)}\""
        )

    try:
        result, _ = json.JSONDecoder().raw_decode(stdout_text)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"JSON解析失败（退出码 {proc.returncode}）：{e}\n"
            f"stdout: {stdout_text[:500]}\nstderr: {stderr_text}"
        )

    if result.get("status") != "ok":
        tb = result.get("traceback", "")
        raise RuntimeError(f"{result.get('error', '未知错误')}\n{tb[:300]}")

    data = result["data"]
    return ParsedDocument(
        file_path=data["file_path"],
        file_name=data["file_name"],
        file_type=data["file_type"],
        content=data["content"],
        pages=data.get("pages", []),
        metadata=data.get("metadata", {}),
    )


# ── Misc helpers ─────────────────────────────────────────

def format_size(size):
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
