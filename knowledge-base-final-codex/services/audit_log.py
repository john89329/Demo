"""Audit session storage — SQLite-backed with multi-active-session support.

Replaces the old JSON-file approach.  Uses the same WAL-mode SQLite pattern
as vector_store.py for consistency.  Sessions are stored per KB alongside
the vector index.
"""

import os
import json
import time
import uuid
import sqlite3
import threading
from contextlib import contextmanager
from typing import Optional, List

import config
from utils import get_logger

_logger = get_logger()
_sessions_lock = threading.Lock()

MAX_SESSIONS = 200
MAX_TURNS_PER_SESSION = 500


class AuditSession:
    """A single audit conversation session (in-memory representation)."""
    def __init__(self, session_id=None, topic="", start_time=None):
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.topic = topic
        self.start_time = start_time or time.strftime("%Y-%m-%d %H:%M:%S")
        self.turns = []

    def add_turn(self, role, content, sources=None):
        self.turns.append({
            "role": role,
            "content": content,
            "sources": sources or [],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        })

    def to_dict(self):
        return {
            "session_id": self.session_id,
            "topic": self.topic,
            "start_time": self.start_time,
            "turns": self.turns,
            "turn_count": len(self.turns),
        }

    @classmethod
    def from_dict(cls, d):
        s = cls(session_id=d.get("session_id"), topic=d.get("topic", ""),
                start_time=d.get("start_time"))
        s.turns = d.get("turns", [])
        return s


# ── SQLite helpers ─────────────────────────────────────────

def _db_path(kb_name=None):
    kb_name = kb_name or config.CURRENT_KB
    kb_dir = os.path.join(config.INDEX_DIR, kb_name)
    os.makedirs(kb_dir, exist_ok=True)
    return os.path.join(kb_dir, "sessions.db")


def _get_db(kb_name=None):
    """Open (or create) the sessions SQLite database. Thread-safe via WAL."""
    path = _db_path(kb_name)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            topic TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            turn_count INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            sources TEXT DEFAULT '[]',
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_turns_session
        ON turns(session_id, id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_updated
        ON sessions(updated_at DESC)
    """)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    return conn


def _migrate_from_json(kb_name=None):
    """One-time migration from old audit_sessions.json to SQLite."""
    kb_name = kb_name or config.CURRENT_KB
    json_path = os.path.join(config.INDEX_DIR, kb_name, "audit_sessions.json")
    if not os.path.exists(json_path):
        return False

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False

    sessions_data = data.get("sessions", [])
    if not sessions_data:
        return False

    conn = _get_db(kb_name)
    try:
        active_ids = {data.get("active_session_id")} if data.get("active_session_id") else set()
        for sd in sessions_data:
            sid = sd.get("session_id", "")
            is_active = 1 if sid in active_ids else 0
            conn.execute(
                "INSERT OR IGNORE INTO sessions (id, topic, created_at, updated_at, is_active, turn_count) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sid, sd.get("topic", ""), sd.get("start_time", ""),
                 sd.get("start_time", ""), is_active, len(sd.get("turns", [])))
            )
            for turn in sd.get("turns", []):
                conn.execute(
                    "INSERT INTO turns (session_id, role, content, sources, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (sid, turn.get("role", ""), turn.get("content", ""),
                     json.dumps(turn.get("sources", []), ensure_ascii=False),
                     turn.get("timestamp", ""))
                )
        conn.commit()
        # Rename old file so we don't migrate twice
        os.rename(json_path, json_path + ".migrated")
        _logger.info("Migrated %d sessions from JSON to SQLite for KB '%s'",
                     len(sessions_data), kb_name)
        return True
    except Exception as e:
        _logger.warning("Session migration failed: %s", e)
        return False
    finally:
        conn.close()


# ── Session CRUD ──────────────────────────────────────────

def _load_session_turns(conn, session_id):
    """Load turns for a session from the DB."""
    rows = conn.execute(
        "SELECT role, content, sources, created_at FROM turns "
        "WHERE session_id = ? ORDER BY id",
        (session_id,)
    ).fetchall()
    return [
        {"role": r[0], "content": r[1], "sources": json.loads(r[2]) if r[2] else [],
         "timestamp": r[3]}
        for r in rows
    ]


def list_sessions(kb_name=None, limit=50, offset=0):
    """List sessions for a KB, most-recently-updated first."""
    _migrate_from_json(kb_name)
    conn = _get_db(kb_name)
    try:
        rows = conn.execute(
            "SELECT id, topic, created_at, updated_at, is_active, turn_count "
            "FROM sessions ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
        active_ids = {
            r[0] for r in conn.execute(
                "SELECT id FROM sessions WHERE is_active = 1"
            ).fetchall()
        }
        result = []
        for row in rows:
            d = {
                "session_id": row[0],
                "topic": row[1],
                "start_time": row[2],
                "updated_at": row[3],
                "active": row[0] in active_ids,
                "turn_count": row[5],
            }
            result.append(d)
        return result
    finally:
        conn.close()


def get_session(kb_name, session_id):
    """Get a full session including all turns."""
    _migrate_from_json(kb_name)
    conn = _get_db(kb_name)
    try:
        row = conn.execute(
            "SELECT id, topic, created_at, updated_at, is_active, turn_count "
            "FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            return None
        s = AuditSession(session_id=row[0], topic=row[1], start_time=row[2])
        s.turns = _load_session_turns(conn, session_id)
        return s
    finally:
        conn.close()


def create_new_session(kb_name, topic=""):
    """Create a new session and mark it active."""
    _migrate_from_json(kb_name)
    with _sessions_lock:
        conn = _get_db(kb_name)
        try:
            session = AuditSession(topic=topic)
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT INTO sessions (id, topic, created_at, updated_at, is_active, turn_count) "
                "VALUES (?, ?, ?, ?, 1, 0)",
                (session.session_id, topic, now, now)
            )
            conn.commit()
            return session
        finally:
            conn.close()


def switch_session(kb_name, session_id):
    """Mark a session as the 'current' one (activate it). Does NOT deactivate others."""
    with _sessions_lock:
        conn = _get_db(kb_name)
        try:
            row = conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if not row:
                return False
            conn.execute("UPDATE sessions SET is_active = 1 WHERE id = ?", (session_id,))
            conn.commit()
            return True
        finally:
            conn.close()


def delete_session(kb_name, session_id):
    """Delete a session and all its turns."""
    with _sessions_lock:
        conn = _get_db(kb_name)
        try:
            conn.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.commit()
            return True
        finally:
            conn.close()


def set_topic(kb_name, session_id, topic):
    """Update session topic/title."""
    with _sessions_lock:
        conn = _get_db(kb_name)
        try:
            cur = conn.execute(
                "UPDATE sessions SET topic = ?, updated_at = ? WHERE id = ?",
                (topic, time.strftime("%Y-%m-%d %H:%M:%S"), session_id)
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def save_turn(kb_name, session_id, role, content, sources=None):
    """Save a single turn to a session."""
    with _sessions_lock:
        conn = _get_db(kb_name)
        try:
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT INTO turns (session_id, role, content, sources, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, role, content,
                 json.dumps(sources or [], ensure_ascii=False), now)
            )
            conn.execute(
                "UPDATE sessions SET updated_at = ?, turn_count = turn_count + 1 WHERE id = ?",
                (now, session_id)
            )
            conn.commit()

            # Enforce per-session turn limit
            row = conn.execute(
                "SELECT turn_count FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row and row[0] > MAX_TURNS_PER_SESSION:
                # Delete oldest turns beyond the limit
                keep = MAX_TURNS_PER_SESSION
                oldest = conn.execute(
                    "SELECT id FROM turns WHERE session_id = ? ORDER BY id DESC "
                    "LIMIT 1 OFFSET ?", (session_id, keep)
                ).fetchone()
                if oldest:
                    conn.execute(
                        "DELETE FROM turns WHERE session_id = ? AND id <= ?",
                        (session_id, oldest[0])
                    )
                    conn.execute(
                        "UPDATE sessions SET turn_count = ? WHERE id = ?",
                        (keep, session_id)
                    )
                    conn.commit()
        finally:
            conn.close()


def enforce_session_limit(kb_name, max_sessions=MAX_SESSIONS):
    """Remove oldest inactive sessions if count exceeds limit."""
    with _sessions_lock:
        conn = _get_db(kb_name)
        try:
            count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            if count > max_sessions:
                excess = count - max_sessions
                old = conn.execute(
                    "SELECT id FROM sessions WHERE is_active = 0 "
                    "ORDER BY updated_at ASC LIMIT ?", (excess,)
                ).fetchall()
                for (sid,) in old:
                    conn.execute("DELETE FROM turns WHERE session_id = ?", (sid,))
                    conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
                conn.commit()
        finally:
            conn.close()


# ── Context manager for chat integration ───────────────────

@contextmanager
def active_session(kb_name, session_id=None):
    """Context manager for chat route — auto-saves turns to SQLite.

    Usage::

        with active_session(kb_name, session_id=req_sid) as session:
            session.add_turn("user", query)
            # ... call RAG ...
            session.add_turn("assistant", answer, sources)
            # turns are saved on each add_turn via save_turn()
    """
    _migrate_from_json(kb_name)

    if session_id:
        session = get_session(kb_name, session_id)
        if session is None:
            session = create_new_session(kb_name)
    else:
        # Find or create an active session
        conn = _get_db(kb_name)
        try:
            row = conn.execute(
                "SELECT id FROM sessions WHERE is_active = 1 ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        if row:
            session = get_session(kb_name, row[0])
        else:
            session = create_new_session(kb_name)

    # Wrap add_turn to auto-persist
    original_add_turn = session.add_turn

    def auto_save_turn(role, content, sources=None):
        original_add_turn(role, content, sources)
        save_turn(kb_name, session.session_id, role, content, sources)
        enforce_session_limit(kb_name)

    session.add_turn = auto_save_turn

    try:
        yield session
    finally:
        pass  # turns already saved individually


# ── Export ────────────────────────────────────────────────

def export_session_markdown(session):
    """Export a session as Markdown text."""
    if isinstance(session, dict):
        session = AuditSession.from_dict(session)
    lines = [
        f"# 审计记录",
        f"",
        f"- **会话ID**: {session.session_id}",
        f"- **主题**: {session.topic or '未设置'}",
        f"- **开始时间**: {session.start_time}",
        f"- **问答轮次**: {len(session.turns)}",
        f"",
    ]
    for i, turn in enumerate(session.turns, 1):
        role_label = "用户" if turn["role"] == "user" else "AI"
        lines.append(f"## [{i}] {role_label} ({turn.get('timestamp', '')})")
        lines.append("")
        lines.append(turn["content"])
        lines.append("")
        if turn.get("sources"):
            lines.append("**参考资料:**")
            for src in turn["sources"]:
                if isinstance(src, dict):
                    lines.append(f"- {src.get('source_file', src.get('file', ''))}")
                else:
                    lines.append(f"- {src}")
            lines.append("")
    return "\n".join(lines)
