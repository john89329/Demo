"""Knowledge base library file I/O — load, save, find, resolve.

Extracted from app.py.
"""

import os
import json

import config
from state import LIBS_FILE


def ensure_libraries():
    os.makedirs(config.INDEX_DIR, exist_ok=True)
    if not os.path.exists(LIBS_FILE):
        save_libraries([config.CURRENT_KB])


def load_libraries():
    try:
        with open(LIBS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                libs = data
            else:
                libs = data.get("libraries", [])
        found = find_kb(config.CURRENT_KB, libs)
        if found:
            config.CURRENT_KB = found
        return libs
    except Exception:
        return [config.CURRENT_KB]


def save_libraries(libs):
    os.makedirs(config.INDEX_DIR, exist_ok=True)
    with open(LIBS_FILE, "w", encoding="utf-8") as f:
        json.dump({"libraries": libs}, f, ensure_ascii=False, indent=2)


def find_kb(name, libs):
    """Case-insensitive library name lookup. Returns the actual cased name or None."""
    name_lower = name.strip().lower()
    for lib in libs:
        if lib.lower() == name_lower:
            return lib
    return None


def resolve_kb(name):
    """Resolve a kb_name parameter to the actual cased library name."""
    libs = load_libraries()
    found = find_kb(name, libs)
    return found if found else name
