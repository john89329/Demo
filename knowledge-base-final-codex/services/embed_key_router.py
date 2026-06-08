"""Embedding API key round-robin router.

Single helper shared by all endpoints so that every feature (search, chat,
index, retry-file, material-gen) benefits from multi-key distribution,
not just the pipeline.
"""

import threading
import config

_KEY_INDEX = 0
_KEY_LOCK = threading.Lock()


def pick_key(embedding_api_key: str = "") -> str:
    """Pick an embedding API key via round-robin, falling back to server config.

    Args:
        embedding_api_key: Comma-separated keys from the frontend (may be "").

    Returns:
        A single API key string suitable for ``Authorization: Bearer <key>``.
    """
    global _KEY_INDEX

    if embedding_api_key and embedding_api_key.strip():
        keys = [k.strip() for k in embedding_api_key.split(",") if k.strip()]
    elif config.EMBEDDING_API_KEY:
        keys = [k.strip() for k in config.EMBEDDING_API_KEY.split(",") if k.strip()]
    else:
        return ""

    if not keys:
        return ""

    with _KEY_LOCK:
        idx = _KEY_INDEX % len(keys)
        _KEY_INDEX += 1
        return keys[idx]
