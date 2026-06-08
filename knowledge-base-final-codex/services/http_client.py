"""Lightweight HTTP client — zero extra dependencies.

Uses http.client.HTTPSConnection directly, creating a fresh connection
per request to avoid thread-safety issues with connection pooling.
"""

import json
import time
import http.client


class HttpClient:

    def request(self, url: str, data: bytes = None, headers: dict = None,
                method: str = "POST", timeout: int = 120):
        """Make an HTTP request with a fresh connection each time."""
        host = url.split("/")[2]
        port = 443
        if ":" in host:
            host, port_str = host.split(":", 1)
            port = int(port_str)
        path = "/" + url.split("/", 3)[-1]
        if headers is None:
            headers = {}
        if "Content-Type" not in headers and data:
            headers["Content-Type"] = "application/json"

        last_exc = None
        for attempt in range(2):
            conn = http.client.HTTPSConnection(host, port, timeout=timeout)
            try:
                conn.request(method, path, body=data, headers=headers)
                resp = conn.getresponse()
                body = resp.read()
                return resp.status, body
            except (http.client.RemoteDisconnected,
                    ConnectionResetError, ConnectionAbortedError,
                    TimeoutError, OSError) as exc:
                last_exc = exc
                try:
                    conn.close()
                except Exception:
                    pass
                if attempt == 0:
                    time.sleep(0.5)
                    continue
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                raise
        raise last_exc

    def post_json(self, url: str, payload: dict, headers: dict = None,
                  timeout: int = 120) -> dict:
        """POST JSON and return parsed response dict."""
        data = json.dumps(payload).encode("utf-8")
        if headers is None:
            headers = {}
        if "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"
        status, body = self.request(url, data=data, headers=headers,
                                     method="POST", timeout=timeout)
        if status >= 400:
            raise HttpError(status, body.decode("utf-8", errors="replace"))
        return json.loads(body.decode("utf-8"))


class HttpError(Exception):
    def __init__(self, status, body):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body[:200]}")


# Module-level singleton
_client = HttpClient()


def get_http_client() -> HttpClient:
    return _client
