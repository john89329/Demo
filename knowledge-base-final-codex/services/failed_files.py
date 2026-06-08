"""Failed files tracking — group, persist, load.

Extracted from app.py.
"""

import os
import json

import config


def build_failed_groups(fss, errors):
    """Build grouped failed-files structure from file_statuses and errors."""
    groups = {"parse_error": [], "embed_error": [], "duplicate": [], "cancelled": []}
    for idx, fs in enumerate(fss):
        st = fs.get("status", "pending")
        if st == "embedded":
            continue
        entry = {
            "index": idx,
            "name": fs.get("name", ""),
            "path": fs.get("path", ""),
            "status": st,
            "error": fs.get("error", ""),
        }
        if st == "error":
            matched = False
            fp = fs.get("path", "")
            for e in errors:
                if e.get("path") == fp:
                    if e.get("type") == "embed_error":
                        groups["embed_error"].append(entry)
                    else:
                        groups["parse_error"].append(entry)
                    matched = True
                    break
            if not matched:
                groups["parse_error"].append(entry)
        elif st in ("duplicate",):
            groups["duplicate"].append(entry)
        elif st in ("cancelled", "cancelling"):
            groups["cancelled"].append(entry)
        elif st in ("pending", "parsing", "parsed", "embedding"):
            groups["parse_error"].append({**entry, "status": "incomplete", "error": "索引中断，未完成"})

    failed = []
    for g in ["parse_error", "embed_error", "duplicate", "cancelled"]:
        failed.extend(groups[g])

    return {"total": len(failed), "failed": failed, "groups": groups}


def _failed_files_path(kb_name):
    return os.path.join(config.INDEX_DIR, kb_name, "failed_files.json")


def save_failed_files(kb_name, idx_status):
    """Persist failed files info to disk so it survives restarts."""
    fss = idx_status.get("file_statuses", [])
    errors = idx_status.get("errors", [])
    if not fss:
        return
    try:
        result = build_failed_groups(fss, errors)
        path = _failed_files_path(kb_name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
    except Exception:
        pass


def load_failed_files(kb_name):
    path = _failed_files_path(kb_name)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def remove_file_from_failed(kb_name, file_path):
    """Remove a single file from the persisted failed-files record after retry."""
    path = _failed_files_path(kb_name)
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return

    norm = os.path.normpath(file_path)
    modified = False

    # Remove from the flat failed list
    failed = data.get("failed", [])
    data["failed"] = [f for f in failed if os.path.normpath(f.get("path", "")) != norm]

    # Remove from each group
    for group_name in data.get("groups", {}):
        entries = data["groups"][group_name]
        data["groups"][group_name] = [
            e for e in entries if os.path.normpath(e.get("path", "")) != norm
        ]
        if len(data["groups"][group_name]) != len(entries):
            modified = True

    if data["failed"] != failed:
        modified = True

    if modified:
        data["total"] = len(data["failed"])
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass
