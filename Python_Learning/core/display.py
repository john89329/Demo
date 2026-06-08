"""Terminal display utilities — ANSI colors, formatting, menus.

Automatically turns off colors if:
- stdout is not a terminal (pipe/redirect)
- NO_COLOR environment variable is set
"""

import os
import sys


def _should_use_color() -> bool:
    """Check whether to emit ANSI escape codes."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if not sys.stdout.isatty():
        return False
    return True


# ── ANSI Escape Codes ────────────────────────────────────────────────────────

_USE = _should_use_color()


class Color:
    RESET = "\033[0m" if _USE else ""
    BOLD = "\033[1m" if _USE else ""
    DIM = "\033[2m" if _USE else ""
    # Foreground
    RED = "\033[31m" if _USE else ""
    GREEN = "\033[32m" if _USE else ""
    YELLOW = "\033[33m" if _USE else ""
    BLUE = "\033[34m" if _USE else ""
    MAGENTA = "\033[35m" if _USE else ""
    CYAN = "\033[36m" if _USE else ""
    WHITE = "\033[37m" if _USE else ""
    # Background
    BG_GREEN = "\033[42m" if _USE else ""
    BG_YELLOW = "\033[43m" if _USE else ""
    BG_BLUE = "\033[44m"


# ── Drawing Helpers ──────────────────────────────────────────────────────────

def clear_screen():
    print("\033[2J\033[H", end="")


def divider(char="─", width=60):
    return Color.DIM + char * width + Color.RESET


def header(text: str):
    line = divider("━")
    print(f"\n{Color.CYAN}{Color.BOLD}{text}{Color.RESET}")
    print(line)


def success(text: str):
    print(f"{Color.GREEN}✓ {text}{Color.RESET}")


def error(text: str):
    print(f"{Color.RED}✗ {text}{Color.RESET}")


def info(text: str):
    print(f"{Color.BLUE}ℹ {text}{Color.RESET}")


def warning(text: str):
    print(f"{Color.YELLOW}⚠ {text}{Color.RESET}")


def highlight(text: str):
    return f"{Color.YELLOW}{Color.BOLD}{text}{Color.RESET}"


def prompt(text: str) -> str:
    return input(f"\n{Color.MAGENTA}▶ {text}{Color.RESET}")


def wait_enter(msg="按回车键继续..."):
    input(f"\n{Color.DIM}{msg}{Color.RESET}")


def section(title: str):
    print(f"\n{Color.BOLD}{Color.YELLOW}▎{title}{Color.RESET}")


def code_block(code: str):
    """Display a formatted code block."""
    print(f"\n{Color.DIM}{'─' * 50}{Color.RESET}")
    for line in code.strip().split("\n"):
        print(f"  {Color.CYAN}{line}{Color.RESET}")
    print(f"{Color.DIM}{'─' * 50}{Color.RESET}")


def menu(items: list[str], prompt_text: str = "请选择") -> int:
    """Display a numbered menu and return user's choice (1-indexed)."""
    print(f"\n{Color.BOLD}{prompt_text}:{Color.RESET}")
    for i, item in enumerate(items, 1):
        print(f"  {Color.YELLOW}[{i}]{Color.RESET} {item}")
    print(f"  {Color.YELLOW}[0]{Color.RESET} 返回/退出")
    print()
    while True:
        try:
            choice = int(input(f"{Color.MAGENTA}▶ 输入数字选择: {Color.RESET}"))
            if 0 <= choice <= len(items):
                return choice
            warning(f"请输入 0-{len(items)} 之间的数字")
        except ValueError:
            warning("请输入有效数字")


def render_content(text: str):
    """Render lesson content with simple markdown-like formatting."""
    for line in text.strip().split("\n"):
        line = line.rstrip()
        if not line:
            print()
        elif line.startswith("### "):
            print(f"\n{Color.BOLD}{Color.YELLOW}{line[4:]}{Color.RESET}")
        elif line.startswith("## "):
            print(f"\n{Color.BOLD}{Color.MAGENTA}{line[3:]}{Color.RESET}")
        elif line.startswith("**") and line.endswith("**"):
            print(f"{Color.BOLD}{line.strip('*')}{Color.RESET}")
        elif line.startswith("- "):
            print(f"  {Color.DIM}•{Color.RESET} {line[2:]}")
        elif line.startswith("```"):
            pass  # handled by content itself
        else:
            print(line)
