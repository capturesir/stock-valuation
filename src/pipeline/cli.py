"""Command-line interface.

  python -m src.pipeline.cli run --ticker NVDA
  python -m src.pipeline.cli run --ticker 1830.HK
  python -m src.pipeline.cli check-filings --ticker NVDA
  python -m src.pipeline.cli check-filings --ticker 1830.HK --rerun-if-needed
  python -m src.pipeline.cli export-signals --ticker NVDA --ticker 1830.HK
  python -m src.pipeline.cli run --ticker NVDA --email
  python -m src.pipeline.cli send-report --ticker NVDA
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# allow `python -m src.pipeline.cli` from package root
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from src.pipeline.orchestrator import run_ticker, check_filings, load_config, _norm
    from src.export.email_report import (
        EmailConfigError,
        EmailSendError,
        default_body,
        default_subject,
        find_report_attachments,
        send_report,
    )
    from src.signals.export_signals import (
        GENERATED_BY,
        ProductionPathError,
        SignalExportError,
        SignalValidationError,
        export_signals,
        summarize_signal,
    )
else:
    from .orchestrator import run_ticker, check_filings, load_config, _norm
    from ..export.email_report import (
        EmailConfigError,
        EmailSendError,
        default_body,
        default_subject,
        find_report_attachments,
        send_report,
    )
    from ..signals.export_signals import (
        GENERATED_BY,
        ProductionPathError,
        SignalExportError,
        SignalValidationError,
        export_signals,
        summarize_signal,
    )


def _split_tickers(values) -> list[str]:
    out: list[str] = []
    for v in values or []:
        for part in str(v).split(","):
            t = _norm(part)
            if t and t not in out:
                out.append(t)
    return out


def _export_and_print(
    *,
    tickers=None,
    from_json=None,
    date: str,
    dest: str,
    merge: bool,
    generated_by: str,
    force_dest: bool,
    report_paths=None,
) -> int:
    try:
        result = export_signals(
            tickers=tickers,
            from_json=from_json,
            run_date=date,
            dest=dest,
            merge=merge,
            generated_by=generated_by,
            force_dest=force_dest,
            report_paths=report_paths,
        )
    except SignalValidationError as exc:
        print(f"[錯誤] {exc}")
        for e in exc.errors:
            print("   -", e)
        return 2
    except ProductionPathError as exc:
        print(f"[錯誤] {exc}")
        return 2
    except SignalExportError as exc:
        print(f"[錯誤] {exc}")
        return 1
    for sig in result["incoming"]:
        print(summarize_signal(sig))
    n = len(result["doc"]["signals"])
    print(f"[驗證] 通過（{result['engine']}；schema_version 1.0）")
    print(f"[輸出] {result['dest']}（{n} 個訊號）")
    print(f"[輸出] {result['dated']}")
    return 0


def _email_and_print(ticker: str, date: str, res=None, extra_paths=None) -> int:
    """Send a completed valuation report using the deployer's mailbox settings."""
    cfg = load_config()
    try:
        paths = list(find_report_attachments(Path(__file__).resolve().parents[2], ticker, date))
        if extra_paths:
            for raw in extra_paths:
                pth = Path(raw)
                if pth not in paths:
                    paths.append(pth)
        fv = None
        currency = ""
        if res and res.get("json"):
            j = res["json"]
            fv = j.get("fair_value_per_share")
            currency = j.get("currency") or ""
            if fv is not None:
                fv = f"{fv:.4f}".rstrip("0").rstrip(".")
        info = send_report(
            paths,
            subject=default_subject(ticker, date, fv, currency),
            body=default_body(ticker, date),
            cfg=cfg,
        )
    except EmailConfigError as exc:
        print(f"[錯誤] {exc}")
        return 4
    except EmailSendError as exc:
        print(f"[錯誤] {exc}")
        return 4
    print(f"[電郵] 已由 {info['from']} 寄至 {info['to']}")
    for a in info["attachments"]:
        print(f"[附件] {a}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="可攜、可換模型的 FCFF 估值系統")
    sub = p.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="為一隻股票做（或重做）估值")
    p_run.add_argument("--ticker", required=True)
    p_run.add_argument("--date", default="2026-11-11", help="估值日 YYYY-MM-DD（澳門）")
    p_run.add_argument("--no-llm", action="store_true", help="即使設定了 API 也不呼叫模型，只用規則後備")
    p_run.add_argument(
        "--export-signals",
        action="store_true",
        help="驗證通過後合併匯出 B 組相容訊號檔至 out/signals.json",
    )
    p_run.add_argument(
        "--email",
        action="store_true",
        help="驗證通過後，按本機設定把報告電郵至部署者指定的收件信箱",
    )
    p_chk = sub.add_parser("check-filings", help="比對上次估值後是否有新業績／年報／中期")
    p_chk.add_argument("--ticker", required=True)
    p_chk.add_argument("--rerun-if-needed", action="store_true")
    p_chk.add_argument("--date", default="2026-11-11")
    p_exp = sub.add_parser("export-signals", help="由已有估值輸出 B 組相容訊號檔（schema_version 1.0）")
    p_exp.add_argument("--ticker", action="append", default=[], help="代號，可重複或用逗號分隔")
    p_exp.add_argument("--date", default="2026-11-11", help="估值日 YYYY-MM-DD（澳門）")
    p_exp.add_argument("--out", default="out/signals.json", help="輸出路徑（預設本套件 out/signals.json）")
    p_exp.add_argument("--from-json", action="append", default=[], dest="from_json",
                       help="直接指定估值 calc JSON 路徑")
    p_exp.add_argument("--no-merge", action="store_true", help="只寫入本次列出的代號，不保留檔中其他訊號")
    p_exp.add_argument("--generated-by", default=GENERATED_BY, help="頂層 generated_by（預設：投資經理）")
    p_exp.add_argument("--force-dest", action="store_true",
                       help="允許寫入正式訊號路徑（危險；預設拒絕）")
    p_mail = sub.add_parser("send-report", help="把已有估值報告電郵至部署者本機設定的收件信箱", description="把已有估值報告電郵至部署者本機設定的收件信箱")
    p_mail.add_argument("--ticker", required=True)
    p_mail.add_argument("--date", default="2026-11-11", help="估值日 YYYY-MM-DD（澳門）")
    args = p.parse_args(argv)

    if args.cmd == "run":
        res = run_ticker(args.ticker, valuation_date=args.date, use_llm=not args.no_llm)
        j = res["json"]
        print(f"TICKER={j['ticker']}  COMPANY={j['asset']}")
        print(f"PRICE={j['price']} {j['currency']}  DATE={j['price_date']}")
        print(f"FV_BASE={j['fair_value_per_share']:.4f}  PRICE/FV={j['price']/j['fair_value_per_share']:.3f}")
        print(f"WACC={j['discount']['wacc']['value']:.4%}  g={j['terminal']['g']:.2%}")
        for s in j["scenarios"]:
            print(f"  {s['name']}: {s['fair_value']:.4f}")
        print(f"VERIFY={'PASS' if res['verify_pass'] else 'FAIL'}  {res['verify_errors']}")
        print(f"ASSUMPTION_SOURCE={j.get('assumption_source')}")
        print(f"XLSX={res['xlsx']}")
        print(f"MD={res['md']}")
        print(f"JSON={res['path']}")
        if not res["verify_pass"]:
            if getattr(args, "export_signals", False):
                print("[略過] 估值驗證未通過，不匯出訊號。")
            if getattr(args, "email", False):
                print("[略過] 估值驗證未通過，不寄電郵。")
            return 2
        rc = 0
        if getattr(args, "export_signals", False):
            rc = _export_and_print(
                tickers=[j.get("ticker") or _norm(args.ticker)],
                date=args.date,
                dest="out/signals.json",
                merge=True,
                generated_by=GENERATED_BY,
                force_dest=False,
                report_paths={j.get("ticker") or _norm(args.ticker): res.get("md") or ""},
            )
            if rc != 0:
                return rc
        if getattr(args, "email", False):
            mail_rc = _email_and_print(j.get("ticker") or _norm(args.ticker), args.date, res=res)
            if mail_rc != 0:
                return mail_rc
        return rc

    if args.cmd == "check-filings":
        out = check_filings(args.ticker)
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        if out.get("rerun_needed"):
            print("STATUS=RERUN_NEEDED")
            if args.rerun_if_needed:
                print("AUTO RERUN...")
                run_ticker(args.ticker, valuation_date=args.date)
            return 10
        if out.get("blocked") or out.get("error"):
            print("STATUS=CHECK_BLOCKED_OR_ERROR")
            return 3
        print("STATUS=NO_RERUN")
        return 0

    if args.cmd == "export-signals":
        tickers = _split_tickers(args.ticker)
        if not tickers and not args.from_json:
            print("[錯誤] 請提供 --ticker 或 --from-json。")
            return 1
        return _export_and_print(
            tickers=tickers or None,
            from_json=args.from_json or None,
            date=args.date,
            dest=args.out,
            merge=not args.no_merge,
            generated_by=args.generated_by,
            force_dest=args.force_dest,
        )

    if args.cmd == "send-report":
        return _email_and_print(_norm(args.ticker), args.date)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
