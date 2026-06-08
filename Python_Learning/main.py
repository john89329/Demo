"""Python 自学互动工具 — 入口文件

运行方式:
    python main.py
"""

import sys
import os


def _setup_windows_encoding():
    """Fix encoding and ANSI escape support on Windows terminals."""
    if sys.platform != "win32":
        return

    # Switch console codepage to UTF-8
    os.system("chcp 65001 > nul" if hasattr(os, "devnull") else "chcp 65001 > NUL")

    # Reconfigure stdio for UTF-8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore

    # Enable ANSI escape sequences (VT processing) on Windows 10+
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        # STD_OUTPUT_HANDLE = -11
        handle = kernel32.GetStdHandle(-11)
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        mode = ctypes.c_uint32()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass  # Not critical if this fails


_setup_windows_encoding()

# Ensure the project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.engine import Engine


def main():
    engine = Engine()
    engine.run()


if __name__ == "__main__":
    main()
