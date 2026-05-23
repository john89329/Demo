import os
import json
import time
import uuid

import config


class AuditSession:
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
        }

    @classmethod
    def from_dict(cls, d):
        s = cls(session_id=d.get("session_id"), topic=d.get("topic", ""), start_time=d.get("start_time"))
        s.turns = d.get("turns", [])
        return s


def _sessions_path(kb_name):
    kb_name = kb_name or config.CURRENT_KB
    kb_dir = os.path.join(config.INDEX_DIR, kb_name)
    os.makedirs(kb_dir, exist_ok=True)
    return os.path.join(kb_dir, "audit_sessions.json")


def _load_sessions(kb_name):
    path = _sessions_path(kb_name)
    if not os.path.exists(path):
        return {"active_session_id": None, "sessions": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        sessions = [AuditSession.from_dict(d) for d in data.get("sessions", [])]
        return {"active_session_id": data.get("active_session_id"), "sessions": sessions}
    except Exception:
        return {"active_session_id": None, "sessions": []}


def save_sessions(kb_name):
    data = _load_sessions(kb_name)
    path = _sessions_path(kb_name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "active_session_id": data["active_session_id"],
            "sessions": [s.to_dict() for s in data["sessions"]],
        }, f, ensure_ascii=False, indent=2)


def get_or_create_active_session(kb_name):
    data = _load_sessions(kb_name)
    active_id = data["active_session_id"]
    if active_id:
        for s in data["sessions"]:
            if s.session_id == active_id:
                return s
    session = AuditSession()
    data["sessions"].append(session)
    data["active_session_id"] = session.session_id
    save_sessions(kb_name)
    return session


def create_new_session(kb_name, topic=""):
    data = _load_sessions(kb_name)
    session = AuditSession(topic=topic)
    data["sessions"].append(session)
    data["active_session_id"] = session.session_id
    save_sessions(kb_name)
    return session


def switch_session(kb_name, session_id):
    data = _load_sessions(kb_name)
    for s in data["sessions"]:
        if s.session_id == session_id:
            data["active_session_id"] = session_id
            save_sessions(kb_name)
            return True
    return False


def delete_session(kb_name, session_id):
    data = _load_sessions(kb_name)
    new_sessions = [s for s in data["sessions"] if s.session_id != session_id]
    if len(new_sessions) == len(data["sessions"]):
        return False
    data["sessions"] = new_sessions
    if data["active_session_id"] == session_id:
        data["active_session_id"] = new_sessions[-1].session_id if new_sessions else None
    save_sessions(kb_name)
    return True


def list_sessions(kb_name):
    data = _load_sessions(kb_name)
    result = [s.to_dict() for s in data["sessions"]]
    return result


def get_session(kb_name, session_id):
    data = _load_sessions(kb_name)
    for s in data["sessions"]:
        if s.session_id == session_id:
            return s
    return None


def set_topic(kb_name, session_id, topic):
    session = get_session(kb_name, session_id)
    if session:
        session.topic = topic
        save_sessions(kb_name)
        return True
    return False


def export_session_markdown(session):
    if isinstance(session, dict):
        session = AuditSession.from_dict(session)
    lines = []
    lines.append(f"# 审计记录")
    lines.append(f"")
    lines.append(f"- **会话ID**: {session.session_id}")
    lines.append(f"- **主题**: {session.topic or '未设置'}")
    lines.append(f"- **开始时间**: {session.start_time}")
    lines.append(f"- **问答轮次**: {len(session.turns)}")
    lines.append(f"")
    for i, turn in enumerate(session.turns, 1):
        role_label = "用户" if turn["role"] == "user" else "AI"
        lines.append(f"## [{i}] {role_label} ({turn['timestamp']})")
        lines.append(f"")
        lines.append(turn["content"])
        lines.append(f"")
        if turn.get("sources"):
            lines.append(f"**参考资料:**")
            for src in turn["sources"]:
                if isinstance(src, dict):
                    lines.append(f"- {src.get('source_file', src.get('file', ''))}")
                else:
                    lines.append(f"- {src}")
            lines.append(f"")
    return "\n".join(lines)
