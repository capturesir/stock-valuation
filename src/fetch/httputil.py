from __future__ import annotations
import json, ssl, time, urllib.error, urllib.request
from typing import Optional

DEFAULT_UA = "Mozilla/5.0 (ValuationSystem; +https://localhost) research"

_SSL_CONTEXT: Optional[ssl.SSLContext] = None


def _ssl_context() -> Optional[ssl.SSLContext]:
    """HTTPS context with a working CA bundle.

    The python.org 3.10 install on this Mac ships without cert.pem, so the
    default context fails every HTTPS call with CERTIFICATE_VERIFY_FAILED.
    Prefer certifi (always present in the venv); fall back to the system
    default when certifi is unavailable.
    """
    global _SSL_CONTEXT
    if _SSL_CONTEXT is None:
        try:
            import certifi
            _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            _SSL_CONTEXT = ssl.create_default_context()
    return _SSL_CONTEXT


def get(url: str, headers: Optional[dict] = None, timeout: float = 30, retries: int = 3) -> tuple[int, bytes, dict]:
    """GET with retries. Never lets a network-level error escape as-is.

    A DNS blip, TLS hiccup or connection reset used to bubble up to the HTTP
    layer and surface as a bare 500 Internal Server Error in the web UI. We
    now convert it into a normal (status=0, error body) tuple so callers can
    report a readable message instead of crashing the request.
    """
    h = {"User-Agent": DEFAULT_UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=h, method="GET")
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
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

    # Retries exhausted: return a status-0 result carrying the reason instead
    # of raising, so /api/lookup can answer 502 with a readable message.
    reason = f"{type(last).__name__}: {last}" if last else "unknown network error"
    body = json.dumps({"error": reason}).encode("utf-8")
    return 0, body, {}


def get_json(url: str, headers: Optional[dict] = None, timeout: float = 30) -> tuple[int, object]:
    status, body, _ = get(url, headers=headers, timeout=timeout)
    if not body:
        return status, None
    try:
        return status, json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return status, None
