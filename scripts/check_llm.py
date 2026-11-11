#!/usr/bin/env python3
"""Check whether the language-model endpoint is wired up.

    python scripts/check_llm.py           # 只看設定，不發請求
    python scripts/check_llm.py --ping    # 真的打一次 API，確認金鑰／base_url／model 都通

Reads the same configuration as every other AI step (LLMClient), so a passing
run here means the valuation pipeline will use the model. It never prints the
key: only whether it is set.

Exit code 0 = ready (and, with --ping, the API answered), 1 = not ready.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.llm.client import LLMClient          # noqa: E402
from src.pipeline.orchestrator import load_config  # noqa: E402

HINT_404 = "base_url 路徑錯：多數服務需要 /v1（DeepSeek 為 https://api.deepseek.com/v1）"
HINT_401 = "金鑰無效或已撤銷，請到供應商後台確認"
HINT_429 = "額度或限流；估值仍會完成，但會走 rules_fallback"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="檢查語言模型設定／連線")
    ap.add_argument("--ping", action="store_true", help="實際發送一次最小請求")
    args = ap.parse_args(argv)

    client = LLMClient(load_config())
    ok = client.available()

    print(f"available : {ok}")
    print(f"base_url  : {client.base_url or '(未設定)'}")
    print(f"model     : {client.model or '(未設定)'}")
    print(f"api_key   : {'已設定（%d 字元）' % len(client.api_key) if client.api_key else '(未設定)'}")
    print(f"json_mode : {client.json_mode}")

    if not ok:
        print()
        print("尚未接上 AI。需要三個環境變數其中至少 base_url + model，雲端服務另需金鑰：")
        print("  LLM_BASE_URL=https://api.deepseek.com/v1")
        print("  LLM_MODEL=deepseek-flash")
        print("  LLM_API_KEY=sk-...")
        print()
        print("本系統不會自動讀 .env；載入方式：set -a; source .env; set +a")
        return 1

    if not args.ping:
        print()
        print("設定齊全。加 --ping 可發送一次真實請求確認連線。")
        return 0

    res = client.chat_json(
        "你是連線測試助手，只回覆 JSON。",
        '只回傳 {"pong": true}，不要其他文字。',
        tag="check_llm",
    )
    print()
    if res.ok:
        print(f"ping      : ok（回覆 {res.data}）")
        if res.usage:
            print(f"usage     : {res.usage}")
        print()
        print("AI 已接通，估值會使用此模型寫假設。")
        return 0

    print(f"ping      : 失敗 — {res.error}")
    err = (res.error or "")
    if "401" in err:
        print(f"            {HINT_401}")
    elif "404" in err:
        print(f"            {HINT_404}")
    elif "429" in err:
        print(f"            {HINT_429}")
    print()
    print("估值仍可進行：沒有可用模型時系統會走規則後備（assumption_source = rules_fallback）。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
