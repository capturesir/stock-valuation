"""OpenAI-compatible Chat Completions client.

Any AI step in this system must go through LLMClient. Point at OpenAI, Azure-
compatible endpoints, vLLM, Ollama, LM Studio, llama.cpp server, etc. by
changing base_url + model + API key (env only).
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    ok: bool
    data: Optional[dict]
    raw_text: str = ""
    source: str = ""          # "llm" | "rules_fallback"
    error: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    usage: dict = field(default_factory=dict)


def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return default if v is None or str(v).strip() == "" else str(v).strip()


class LLMClient:
    def __init__(self, cfg: Optional[dict] = None):
        cfg = cfg or {}
        llm = cfg.get("llm") or {}
        self.base_url = _env("LLM_BASE_URL", llm.get("base_url") or "").rstrip("/")
        self.model = _env("LLM_MODEL", llm.get("model") or "")
        key_env = llm.get("api_key_env") or "LLM_API_KEY"
        self.api_key = _env("LLM_API_KEY") or _env(key_env)
        self.timeout = float(llm.get("timeout_seconds") or 90)
        self.max_retries = int(llm.get("max_retries") or 3)
        self.temperature = float(llm.get("temperature") or 0)
        self.json_mode = bool(llm.get("json_mode", True))

    def available(self) -> bool:
        # A local server may not need a key, but we still require a base_url and a model.
        # If the user points at a cloud API, a key is required.
        if not self.base_url or not self.model:
            return False
        local = self._is_local(self.base_url)
        if not local and not self.api_key:
            return False
        return True

    @staticmethod
    def _is_local(url: str) -> bool:
        u = (url or "").lower()
        return any(s in u for s in ("127.0.0.1", "localhost", "0.0.0.0", "::1"))

    def chat_json(self, system: str, user: str, tag: str = "") -> LLMResult:
        """Send a chat request and parse a JSON object. On any failure, ok=False.

        The caller must fall back to rules and set assumption_source=rules_fallback.
        """
        if not self.available():
            reason = "LLM_BASE_URL 或 LLM_MODEL 未設定" if not (self.base_url and self.model) else "雲端 API 未提供 LLM_API_KEY"
            return LLMResult(ok=False, data=None, source="rules_fallback", error=reason,
                             model=self.model or None, base_url=self.base_url or None)
        url = self.base_url + "/chat/completions"
        if not url.startswith("http"):
            return LLMResult(ok=False, data=None, source="rules_fallback",
                             error=f"無效的 LLM_BASE_URL: {self.base_url}", model=self.model, base_url=self.base_url)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
        }
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        last_err = None
        for attempt in range(self.max_retries):
            try:
                req = urllib.request.Request(url, data=body, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                parsed = json.loads(raw)
                text = (((parsed.get("choices") or [{}])[0].get("message") or {}).get("content")) or ""
                data = _extract_json(text)
                if data is None:
                    return LLMResult(ok=False, data=None, raw_text=text, source="rules_fallback",
                                     error=f"{tag} 無法從模型回覆解析 JSON", model=self.model,
                                     base_url=self.base_url, usage=parsed.get("usage") or {})
                return LLMResult(ok=True, data=data, raw_text=text, source="llm",
                                 model=self.model, base_url=self.base_url, usage=parsed.get("usage") or {})
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code}: {e.read()[:400].decode('utf-8', 'replace') if hasattr(e, 'read') else e}"
                # 400 on json_mode: retry once without response_format
                if e.code == 400 and "response_format" in payload:
                    payload.pop("response_format", None)
                    body = json.dumps(payload).encode("utf-8")
                    continue
                if e.code in (429, 500, 502, 503, 504) and attempt + 1 < self.max_retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                break
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                if attempt + 1 < self.max_retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                break
        return LLMResult(ok=False, data=None, source="rules_fallback",
                         error=f"{tag} LLM 請求失敗：{last_err}", model=self.model, base_url=self.base_url)


def _extract_json(text: str) -> Optional[dict]:
    if not text or not str(text).strip():
        return None
    s = str(text).strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.S)
    if m:
        try:
            obj = json.loads(m.group(1))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            pass
    # first balanced {...}
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    for i, ch in enumerate(s[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(s[start:i + 1])
                    return obj if isinstance(obj, dict) else None
                except json.JSONDecodeError:
                    return None
    return None
