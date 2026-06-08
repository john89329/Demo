"""Unified LLM client — single implementation for all chat completion calls.

Used by rag_qa, material_gen, and summarizer.  Accepts either ``messages``
(list of dicts) or ``prompt`` + optional ``system_prompt`` for simpler callers.
"""

import json
import time

from config import DEEPSEEK_BASE_URL, CHAT_MODEL
from utils import get_logger
from services.http_client import get_http_client, HttpError

_logger = get_logger()


def chat_completion(
    messages=None,
    prompt=None,
    system_prompt=None,
    api_key=None,
    temperature=0.6,
    max_tokens=8192,
    timeout=120,
    retries=3,
):
    """Unified chat completion via DeepSeek API.

    Args:
        messages: List of {"role", "content"} dicts (mutually exclusive with prompt).
        prompt: Plain text prompt (used with optional system_prompt to build messages).
        system_prompt: System message when using prompt mode.
        api_key: DeepSeek API key.
        temperature: Model temperature (0.0-1.0).
        max_tokens: Max tokens for the response.
        timeout: HTTP request timeout in seconds.
        retries: Number of retry attempts on transient errors.

    Returns:
        Response text string, or None on failure after all retries.
    """
    if messages is None:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if prompt:
            messages.append({"role": "user", "content": prompt})

    if not messages:
        _logger.warning("LLM call with empty messages")
        return None

    # ── Token budget warning (defense-in-depth) ──
    from utils import estimate_message_tokens
    import config as _cfg
    budget = _cfg.MAX_INPUT_TOKENS
    estimated = estimate_message_tokens(messages)
    if estimated > budget:
        _logger.warning(
            "Input estimated at %d tokens exceeds budget of %d (%.0f%% over). "
            "Caller should batch this input to avoid context-window errors.",
            estimated, budget, (estimated / budget - 1) * 100
        )

    http = get_http_client()
    url = f"{DEEPSEEK_BASE_URL}/chat/completions"
    last_error = None

    for attempt in range(retries):
        try:
            payload = {
                "model": CHAT_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            }
            headers = {"Authorization": f"Bearer {api_key}"}
            result = http.post_json(url, payload, headers=headers, timeout=timeout)
            return result["choices"][0]["message"]["content"]

        except HttpError as e:
            last_error = f"HTTP {e.status}: {e.body[:500]}"
            if attempt < retries - 1 and e.status in (429, 500, 502, 503):
                time.sleep(2 ** attempt)
                continue
            break
        except Exception as e:
            last_error = str(e)[:500]
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            break

    _logger.warning("LLM call failed after %d retries: %s", retries, last_error)
    return None
