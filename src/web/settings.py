"""AI connection settings edited from the browser (R12).

The only page the v1.1 spec allows to be separate is this settings page. Values
are persisted to the project's .env (gitignored) so a restart keeps them, and
pushed into os.environ so the running process picks them up without one.

The key is never returned to the browser: the API reports whether it is set,
how long it is, and the last four characters.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from ..pipeline.orchestrator import ROOT, load_config
from ..llm.client import LLMClient

ENV_PATH = ROOT / ".env"
KEYS = ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")
LINE_RE = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$")
HEADER = ("# 本機密鑰（已被 .gitignore 忽略，切勿提交）\n"
          "# 由網頁「AI 設定」寫入；亦可手動編輯\n")


def _parse_env(path: Path = ENV_PATH) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            m = LINE_RE.match(line.strip())
            if m:
                v = m.group("value").strip().strip('"').strip("'")
                out[m.group("key")] = v
    except FileNotFoundError:
        pass
    return out


def _mask(key: Optional[str]) -> dict:
    if not key:
        return {"set": False, "hint": "未設定"}
    tail = key[-4:] if len(key) >= 4 else key
    return {"set": True, "length": len(key), "tail": tail,
            "hint": f"已設定（{len(key)} 字元，末四碼 {tail}）"}


def current() -> dict:
    """Effective settings: .env values, then environment overrides win."""
    cfg = (load_config().get("llm") or {})
    env = _parse_env()
    base_url = os.environ.get("LLM_BASE_URL") or env.get("LLM_BASE_URL") or cfg.get("base_url") or ""
    model = os.environ.get("LLM_MODEL") or env.get("LLM_MODEL") or cfg.get("model") or ""
    key = os.environ.get("LLM_API_KEY") or env.get("LLM_API_KEY") or ""
    client = LLMClient(load_config())
    return {
        "ok": True,
        "base_url": base_url,
        "model": model,
        "api_key": _mask(key),
        "available": client.available(),
        "local": LLMClient._is_local(base_url),
        "json_mode": cfg.get("json_mode", True),
        "env_path": str(ENV_PATH),
        "hint": ("本地模型（127.0.0.1／localhost）可以不填 API Key；"
                 "雲端服務需要 Key。留空 Key 欄位＝不變更。"),
    }


def save(base_url: str, model: str, api_key: Optional[str], timeout: Optional[float] = None,
         json_mode: Optional[bool] = None) -> dict:
    """Persist connection settings. An empty/None api_key leaves the old key."""
    base_url = (base_url or "").strip().rstrip("/")
    model = (model or "").strip()
    if not base_url or not model:
        raise ValueError("請填寫 API 位址與模型名稱。")
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("API 位址必須以 http:// 或 https:// 開頭。")

    env = _parse_env()
    env["LLM_BASE_URL"] = base_url
    env["LLM_MODEL"] = model
    if api_key is not None and str(api_key).strip():
        env["LLM_API_KEY"] = str(api_key).strip()

    lines = [HEADER.rstrip("\n")]
    for k in KEYS:
        if k in env and env[k]:
            lines.append(f"{k}={env[k]}")
    others = {k: v for k, v in env.items() if k not in KEYS and v}
    for k, v in others.items():
        lines.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(ENV_PATH, 0o600)
    except OSError:
        pass

    # Make it effective for the running process (new LLMClient per run reads env).
    os.environ["LLM_BASE_URL"] = base_url
    os.environ["LLM_MODEL"] = model
    if env.get("LLM_API_KEY"):
        os.environ["LLM_API_KEY"] = env["LLM_API_KEY"]
    return current()


def test_connection() -> dict:
    """One tiny request through the real client, to prove the settings work."""
    cfg = load_config()
    client = LLMClient(cfg)
    if not client.available():
        return {"ok": False,
                "error": "設定不完整：需要 API 位址與模型名稱；雲端服務另需 API Key。"}
    r = client.chat_json("你只回傳 JSON。", '只回傳 {"pong": true}，不要其他文字。', tag="settings_test")
    return {
        "ok": bool(r.ok),
        "error": r.error,
        "model": r.model,
        "base_url": r.base_url,
        "data": r.data,
        "usage": r.usage,
        "usage_summary": client.usage_summary(),
    }
