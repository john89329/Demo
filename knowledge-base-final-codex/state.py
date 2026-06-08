"""Shared indexing state — globals and their accessors.

Extracted from app.py so route modules and pipeline can import the same
state without circular dependencies.
"""

import os
import threading

import config

LIBS_FILE = os.path.join(config.INDEX_DIR, "libraries.json")

_indexing_status = {}  # kb_name -> status dict
_indexing_status_lock = threading.Lock()

_indexing_stop_events = {}  # kb_name -> threading.Event, set to request stop
_indexing_stop_events_lock = threading.Lock()


def default_index_status():
    return {"running": False, "progress": "", "total": 0, "current": 0,
            "errors": [], "file_statuses": []}


def get_index_status(kb_name):
    with _indexing_status_lock:
        if kb_name not in _indexing_status:
            _indexing_status[kb_name] = default_index_status()
        return _indexing_status[kb_name]
