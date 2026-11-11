from __future__ import annotations
import json, time, urllib.error, urllib.request
from typing import Optional

DEFAULT_UA = "Mozilla/5.0 (ValuationSystem; +https://localhost) research"

def get(url: str, headers: Optional[dict] = None, timeout: float = 30, retries: int = 3) -> tuple[int, bytes, dict]:
    h = {"User-Agent": DEFAULT_UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=h, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            last = e
            body = e.read() if hasattr(e, "read") else b""
            if e.code in (429, 500, 502, 503, 504) and i + 1 < retries:
                time.sleep(2 * (i + 1))
                continue
            return e.code, body, dict(getattr(e, "headers", {}) or {})
        except Exception as e:
            last = e
            if i + 1 < retries:
                time.sleep(2 * (i + 1))
                continue
            raise
    raise RuntimeError(str(last))


def get_json(url: str, headers: Optional[dict] = None, timeout: float = 30) -> tuple[int, object]:
    status, body, _ = get(url, headers=headers, timeout=timeout)
    if not body:
        return status, None
    try:
        return status, json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return status, None
