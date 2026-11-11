"""B-group-compatible trading-signal export (schema_version 1.0).

Reimplements the production formulas from A-group's ``build_signals.py``
inside this package. Does not import ``/workspace/invest/models/``.

Default destination is this package's ``out/signals.json`` — never the
production ``/workspace/invest/signals.json`` unless ``--force-dest`` is set.

The portable default path emits only the base side / confidence / upside
mapping. It does not apply A-group-only adjustments (confidence_cap,
stale-price cuts, data-gap cuts, or the signal_enabled review gate).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import tempfile
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_TEMPLATE = PACKAGE_ROOT / "docs" / "signals_schema_v1.json"
SCHEMA_FILE = PACKAGE_ROOT / "docs" / "signals.schema.json"

TZ = ZoneInfo("Asia/Macau")
SCHEMA_VERSION = "1.0"
GENERATED_BY = "投資經理"
DEFAULT_OUT = PACKAGE_ROOT / "out" / "signals.json"

BUY_THRESHOLD = 15.0
SELL_THRESHOLD = -15.0
UPSIDE_DECIMALS = 1
VALUE_DECIMALS = 2  # fair_value and market_price written at 2 d.p. (half-up)

CONF_DIRECTIONAL_BASE = 40
CONF_DIRECTIONAL_CAP = 90
CONF_HOLD_BASE = 50
CONF_MIN, CONF_MAX = 0, 100

HORIZON = "12M"
EXPIRY_HOUR = 7

SUFFIX_MARKET = {"TW": "TW", "TWO": "TW", "HK": "HK", "T": "JP", "KS": "KR", "KQ": "KR"}
NO_SUFFIX_MARKET = "US"
MARKET_ENUM = ["US", "JP", "HK", "KR", "TW", "CRYPTO"]
ASSET_TYPE_BY_MARKET = {
    "TW": "equity", "HK": "equity", "US": "equity",
    "JP": "equity", "KR": "equity", "CRYPTO": "crypto",
}
ASSET_TYPE_ENUM = ["equity", "crypto"]
CRYPTO_PAIR_RE = re.compile(r"^[A-Z0-9]+-[A-Z]{3}$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

CCY_SYMBOL = {"TWD": "NT$", "HKD": "HK$", "USD": "US$", "CNY": "RMB", "JPY": "¥", "KRW": "₩"}

SIG_KEYS_EXPECTED = [
    "signal_id", "ticker", "market", "name", "asset_type", "currency", "side",
    "confidence", "fair_value", "market_price", "price_date", "upside_pct",
    "horizon", "rationale", "report_path", "expires_at",
]
TOP_KEYS_EXPECTED = ["schema_version", "generated_at", "generated_by", "price_date", "signals"]

PRODUCTION_SIGNALS_FILE = Path("/workspace/invest/signals.json")
PRODUCTION_SIGNALS_DIR = Path("/workspace/invest/signals")
PRODUCTION_INVALID_FILE = Path("/workspace/invest/signals.invalid.json")


class SignalExportError(Exception):
    """User-facing export error (message in Traditional Chinese)."""


class ProductionPathError(SignalExportError):
    """Refused to write a production A-group signals path."""


class SignalValidationError(SignalExportError):
    """Document failed schema or semantic validation."""

    def __init__(self, message: str, errors: list[str], invalid_path: Optional[Path] = None):
        super().__init__(message)
        self.errors = errors
        self.invalid_path = invalid_path


def half_up(x, nd=0):
    """Half-up rounding, copied from production ``build_signals.py``."""
    q = Decimal(1).scaleb(-nd)
    return float(Decimal(repr(x)).quantize(q, rounding=ROUND_HALF_UP)) if nd else int(
        Decimal(repr(x)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def market_of(ticker: str) -> Optional[str]:
    if CRYPTO_PAIR_RE.match(ticker):
        return "CRYPTO"
    if "." in ticker:
        return SUFFIX_MARKET.get(ticker.rsplit(".", 1)[1])
    return NO_SUFFIX_MARKET


def next_run(run_date: dt.date) -> str:
    d = run_date + dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return dt.datetime(d.year, d.month, d.day, EXPIRY_HOUR, 0, tzinfo=TZ).isoformat()


def compute_upside(fair_value: float, market_price: float) -> float:
    return half_up((fair_value / market_price - 1) * 100, UPSIDE_DECIMALS)


def compute_side(upside_pct: float) -> str:
    if upside_pct >= BUY_THRESHOLD:
        return "BUY"
    if upside_pct <= SELL_THRESHOLD:
        return "SELL"
    return "HOLD"


def compute_confidence(side: str, upside_pct: float) -> int:
    mag = half_up(abs(upside_pct))
    if side != "HOLD":
        conf = min(CONF_DIRECTIONAL_CAP, CONF_DIRECTIONAL_BASE + mag)
    else:
        conf = max(0, CONF_HOLD_BASE - mag)
    return int(max(CONF_MIN, min(CONF_MAX, conf)))


def short_clause(text: str) -> str:
    return re.split(r"[（(，,；;。]", text, maxsplit=1)[0].strip()


def scenario_range(calc: Optional[dict]) -> tuple:
    """(low, high, low_label, high_label) from scenarios named 悲觀…/樂觀…."""
    sc = [s for s in (calc or {}).get("scenarios", []) if isinstance(s.get("fair_value"), (int, float))]
    lo_s = next((s for s in sc if str(s.get("name", "")).startswith("悲觀")), None)
    hi_s = next((s for s in sc if str(s.get("name", "")).startswith("樂觀")), None)
    if lo_s and hi_s:
        lab = lambda s: short_clause(s["name"].split("：", 1)[1]) if "：" in s["name"] else ""
        return lo_s["fair_value"], hi_s["fair_value"], lab(lo_s), lab(hi_s)
    alt = [s["fair_value"] for s in sc if s.get("name") != "基本"]
    return (min(alt), max(alt), "", "") if alt else (None, None, "", "")


def build_rationale(sym: str, fv: float, px: float, price_date: str, upside: float, scen) -> str:
    lo, hi, lo_lab, hi_lab = scen
    rel = "高" if upside >= 0 else "低"
    s = (f"FCFF DCF 理論價 {sym}{fv:,.2f} 較 {price_date[5:].replace('-', '/')} 收市價 {sym}{px:,.2f} "
         f"{rel} {abs(upside):.1f}%")
    if lo is not None and hi is not None:
        lab = f"悲觀（{lo_lab}）／樂觀（{hi_lab}）" if lo_lab and hi_lab else "悲觀／樂觀"
        s += f"，{lab}情境 {sym}{lo:,.2f}–{hi:,.2f}"
    return s + "。"


def _rel_or_abs(path: Path, root: Path = PACKAGE_ROOT) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _fair_value_from_calc(calc: dict) -> Optional[float]:
    fv = calc.get("fair_value_per_share")
    if isinstance(fv, (int, float)) and fv > 0:
        return float(fv)
    for s in calc.get("scenarios") or []:
        if s.get("name") == "基準" and isinstance(s.get("fair_value"), (int, float)) and s["fair_value"] > 0:
            return float(s["fair_value"])
    return None


def signal_from_calc(
    calc: dict,
    run_date: dt.date,
    report_path: str,
    *,
    root: Path = PACKAGE_ROOT,
) -> dict:
    """Build one signal dict from a loaded calc JSON, a run date, and a report path."""
    ticker = str(calc.get("ticker") or "").strip()
    if not ticker:
        raise SignalExportError("calc JSON 沒有 ticker。")
    name = str(calc.get("asset") or "").strip()
    if not name:
        raise SignalExportError(f"{ticker}：calc JSON 沒有公司名稱（asset）。")
    ccy = str(calc.get("currency") or "").strip()
    if not re.fullmatch(r"[A-Z]{3}", ccy):
        raise SignalExportError(f"{ticker}：貨幣碼無效（{ccy!r}），須為三位大寫字母。")
    pdate = str(calc.get("price_date") or "").strip()
    if not ISO_DATE_RE.fullmatch(pdate):
        raise SignalExportError(f"{ticker}：沒有有效的市價日期（price_date）。")
    raw_fv = _fair_value_from_calc(calc)
    raw_px = calc.get("price")
    if raw_fv is None:
        raise SignalExportError(f"{ticker}：沒有數值理論價（fair_value_per_share 或基準情境）。")
    if not isinstance(raw_px, (int, float)) or raw_px <= 0:
        raise SignalExportError(f"{ticker}：沒有有效市價（price）。")

    mkt = market_of(ticker)
    if mkt not in MARKET_ENUM:
        raise SignalExportError(
            f"{ticker}：市場不在 schema 列舉 {MARKET_ENUM}（按代號後綴無法對應合法市場）。"
        )

    report = str(report_path or "").strip()
    if not report:
        raise SignalExportError(f"{ticker}：report_path 不可空白。")
    # Normalise package-local paths to repo-relative POSIX form.
    report_as_path = Path(report)
    if report_as_path.is_absolute() or (PACKAGE_ROOT / report).exists() or report_as_path.exists():
        candidate = report_as_path if report_as_path.is_absolute() else (
            report_as_path if report_as_path.exists() else PACKAGE_ROOT / report
        )
        if candidate.exists():
            report = _rel_or_abs(candidate, root)

    fv = half_up(raw_fv, VALUE_DECIMALS)
    px = half_up(float(raw_px), VALUE_DECIMALS)
    if fv <= 0 or px <= 0:
        raise SignalExportError(f"{ticker}：四捨五入後理論價或市價不是正數。")

    upside = compute_upside(fv, px)
    side = compute_side(upside)
    conf = compute_confidence(side, upside)
    sym = CCY_SYMBOL.get(ccy, ccy + " ")
    sig = {
        "signal_id": f"{run_date:%Y%m%d}-{ticker}",
        "ticker": ticker,
        "market": mkt,
        "name": name,
        "asset_type": ASSET_TYPE_BY_MARKET[mkt],
        "currency": ccy,
        "side": side,
        "confidence": int(conf),
        "fair_value": fv,
        "market_price": px,
        "price_date": pdate,
        "upside_pct": upside,
        "horizon": HORIZON,
        "rationale": build_rationale(sym, fv, px, pdate, upside, scenario_range(calc)),
        "report_path": report,
        "expires_at": next_run(run_date),
    }
    return {k: sig[k] for k in SIG_KEYS_EXPECTED}


def now_generated_at(when: Optional[dt.datetime] = None) -> str:
    ts = when or dt.datetime.now(TZ)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=TZ)
    else:
        ts = ts.astimezone(TZ)
    return ts.replace(microsecond=0).isoformat()


def build_document(
    signals: Iterable[dict],
    *,
    run_date: dt.date,
    generated_by: str = GENERATED_BY,
    generated_at: Optional[str] = None,
) -> dict:
    sigs = [({k: s[k] for k in SIG_KEYS_EXPECTED} if list(s) != SIG_KEYS_EXPECTED else s) for s in signals]
    price_dates = [s["price_date"] for s in sigs if ISO_DATE_RE.fullmatch(str(s.get("price_date", "")))]
    doc = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or now_generated_at(),
        "generated_by": generated_by or GENERATED_BY,
        "price_date": max(price_dates) if price_dates else f"{run_date:%Y-%m-%d}",
        "signals": sigs,
    }
    return {k: doc[k] for k in TOP_KEYS_EXPECTED}


def load_schema() -> dict:
    with open(SCHEMA_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_template() -> dict:
    with open(SCHEMA_TEMPLATE, encoding="utf-8") as f:
        tpl = json.load(f)
    top, sig = list(tpl.keys()), list(tpl["signals"][0].keys())
    if top != TOP_KEYS_EXPECTED or sig != SIG_KEYS_EXPECTED:
        raise SignalExportError(f"模板欄位與預期不符：top={top} signal={sig}")
    if tpl.get("schema_version") != SCHEMA_VERSION:
        raise SignalExportError(f"模板 schema_version={tpl.get('schema_version')}，套件為 {SCHEMA_VERSION}")
    return tpl


def validate_document(doc: dict, schema: Optional[dict] = None) -> tuple[str, list[str]]:
    schema = schema if schema is not None else load_schema()
    errs: list[str] = []
    try:
        import jsonschema
        v = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
        errs = [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}" for e in v.iter_errors(doc)]
        try:
            ver = __import__("importlib.metadata").metadata.version("jsonschema")
        except Exception:
            ver = ""
        engine = f"jsonschema {ver}".strip()
    except ImportError:
        engine = "manual"
        if list(doc) != TOP_KEYS_EXPECTED:
            errs.append(f"top-level keys {list(doc)}")
        for i, s in enumerate(doc.get("signals", [])):
            if list(s) != SIG_KEYS_EXPECTED:
                errs.append(f"signals/{i} keys {list(s)}")
            if s.get("side") not in ("BUY", "SELL", "HOLD"):
                errs.append(f"signals/{i}/side")
            if not isinstance(s.get("confidence"), int) or not 0 <= s["confidence"] <= 100:
                errs.append(f"signals/{i}/confidence")
    # Semantic checks beyond the schema (same as production).
    for i, s in enumerate(doc.get("signals", [])):
        sid = str(s.get("signal_id") or "")
        if len(sid) < 10 or sid[9:] != s.get("ticker") or not sid[:8].isdigit():
            errs.append(f"signals/{i}/signal_id 與 ticker 不一致")
        try:
            exp = half_up((s["fair_value"] / s["market_price"] - 1) * 100, UPSIDE_DECIMALS)
            if abs(exp - s["upside_pct"]) > 1e-9:
                errs.append(f"signals/{i}/upside_pct {s['upside_pct']} ≠ {exp}")
        except Exception as exc:
            errs.append(f"signals/{i}/upside_pct 無法核對：{exc}")
    return engine, errs


def atomic_write(path: Path, text: str) -> None:
    path = Path(path)
    d = str(path.parent)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def is_protected_signals_path(path: Path) -> bool:
    """True if *path* is the production A-group signals file or its dated directory."""
    try:
        p = Path(path).resolve()
    except OSError:
        p = Path(path)
    protected = {
        PRODUCTION_SIGNALS_FILE.resolve(),
        PRODUCTION_INVALID_FILE.resolve(),
    }
    if p in protected:
        return True
    try:
        p.relative_to(PRODUCTION_SIGNALS_DIR.resolve())
        return p.suffix == ".json" or p.name.endswith(".json")
    except (ValueError, OSError):
        return False


def _refuse_if_protected(paths: Iterable[Path], force_dest: bool) -> None:
    blocked = [Path(p) for p in paths if is_protected_signals_path(p)]
    if not blocked:
        return
    shown = ", ".join(str(p) for p in blocked)
    if not force_dest:
        raise ProductionPathError(
            f"拒絕寫入正式 A 組訊號路徑：{shown}。"
            f"本套件預設只寫到套件內 out/signals.json。若確實要覆寫正式檔，須明確加上 --force-dest。"
        )
    print(f"[警告] --force-dest 已啟用，將寫入正式訊號路徑：{shown}")


def merge_signals(existing: Optional[dict], incoming: list[dict]) -> list[dict]:
    by_ticker: dict[str, dict] = {}
    if existing:
        for s in existing.get("signals") or []:
            t = s.get("ticker")
            if t:
                by_ticker[t] = s
    for s in incoming:
        by_ticker[s["ticker"]] = s
    return list(by_ticker.values())


def dumps_doc(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def load_calc_file(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_calc_and_report(
    ticker: str,
    run_date: dt.date,
    *,
    root: Path = PACKAGE_ROOT,
) -> tuple[Path, Path]:
    date = f"{run_date:%Y-%m-%d}"
    folder = root / "out" / f"{date}_{ticker}"
    candidates = [
        folder / f"{ticker}_latest.json",
        folder / f"{date}_{ticker}.json",
    ]
    calc_path = next((p for p in candidates if p.is_file()), None)
    if calc_path is None:
        raise SignalExportError(
            f"找不到 {ticker} 於 {date} 的估值 JSON（已試 {'、'.join(p.as_posix() for p in candidates)}）。"
        )
    md = folder / f"{date}_{ticker}_valuation.md"
    if not md.is_file():
        found = sorted(folder.glob("*_valuation.md"))
        if found:
            md = found[-1]
        else:
            raise SignalExportError(f"找不到 {ticker} 於 {date} 的估值說明（{md.name}）。")
    return calc_path, md


def report_beside_calc(calc_path: Path) -> Path:
    folder = calc_path.parent
    found = sorted(folder.glob("*_valuation.md"))
    if not found:
        raise SignalExportError(f"在 {folder} 找不到估值說明 Markdown（*_valuation.md）。")
    return found[-1]


def resolve_out_path(out: Optional[os.PathLike | str], *, root: Path = PACKAGE_ROOT) -> Path:
    if out is None or str(out).strip() == "":
        return DEFAULT_OUT
    p = Path(out)
    if not p.is_absolute():
        p = root / p
    return p


def dated_copy_path(dest: Path, run_date: dt.date) -> Path:
    return dest.parent / "signals" / f"{run_date:%Y-%m-%d}.json"


def invalid_path_for(dest: Path) -> Path:
    return dest.parent / "signals.invalid.json"


def summarize_signal(sig: dict) -> str:
    return (
        f"[訊號] {sig['ticker']:8s} {sig['side']:4s} conf={sig['confidence']:3d} "
        f"upside={sig['upside_pct']:+.1f}% fv={sig['fair_value']} px={sig['market_price']} "
        f"({sig['price_date']})"
    )


def export_signals(
    *,
    tickers: Optional[Iterable[str]] = None,
    from_json: Optional[Iterable[os.PathLike | str]] = None,
    run_date: dt.date | str = "2026-11-11",
    dest: Optional[os.PathLike | str] = None,
    merge: bool = True,
    generated_by: str = GENERATED_BY,
    generated_at: Optional[str] = None,
    force_dest: bool = False,
    root: Path = PACKAGE_ROOT,
    report_paths: Optional[dict[str, str]] = None,
) -> dict:
    """Build and publish a schema_version 1.0 signals document.

    Returns a dict with ``doc``, ``dest``, ``dated``, ``engine``.
    On validation failure, writes ``signals.invalid.json`` beside *dest*
    (unless that path is production-protected) and raises ``SignalValidationError``.
    """
    if isinstance(run_date, str):
        run_date = dt.date.fromisoformat(run_date)
    dest_path = resolve_out_path(dest, root=root)
    dated = dated_copy_path(dest_path, run_date)
    invalid = invalid_path_for(dest_path)
    _refuse_if_protected([dest_path, dated, invalid], force_dest=force_dest)

    incoming: list[dict] = []
    report_paths = report_paths or {}

    for raw in from_json or []:
        p = Path(raw)
        if not p.is_absolute():
            for base in (Path.cwd(), root):
                cand = base / p
                if cand.is_file():
                    p = cand
                    break
        if not p.is_file():
            raise SignalExportError(f"找不到 calc JSON：{raw}")
        calc = load_calc_file(p)
        ticker = str(calc.get("ticker") or "").strip()
        rp = report_paths.get(ticker) or report_paths.get(str(p))
        if not rp:
            rp = str(report_beside_calc(p))
        incoming.append(signal_from_calc(calc, run_date, rp, root=root))

    for t in tickers or []:
        ticker = str(t).strip()
        if not ticker:
            continue
        if any(s["ticker"] == ticker for s in incoming):
            continue
        calc_path, md = find_calc_and_report(ticker, run_date, root=root)
        calc = load_calc_file(calc_path)
        rp = report_paths.get(ticker) or str(md)
        incoming.append(signal_from_calc(calc, run_date, rp, root=root))

    if not incoming:
        raise SignalExportError("沒有可匯出的訊號：請提供 --ticker 或 --from-json。")

    existing = None
    if merge and dest_path.is_file():
        try:
            with open(dest_path, encoding="utf-8") as f:
                existing = json.load(f)
        except json.JSONDecodeError as exc:
            raise SignalExportError(f"既有訊號檔無法解析，拒絕合併：{dest_path}（{exc}）。可用 --no-merge 覆寫。")

    signals = merge_signals(existing, incoming) if merge else incoming
    doc = build_document(signals, run_date=run_date, generated_by=generated_by, generated_at=generated_at)
    load_template()  # key-order / version sanity vs the shipped official template
    engine, errs = validate_document(doc)
    text = dumps_doc(doc)
    if errs:
        # Do not write invalid output onto a protected production path.
        if not is_protected_signals_path(invalid):
            atomic_write(invalid, text)
        raise SignalValidationError(
            f"schema 驗證失敗（{engine}），未發布 {dest_path}；輸出已存 {invalid}" if not is_protected_signals_path(invalid)
            else f"schema 驗證失敗（{engine}），未發布 {dest_path}（亦未寫入正式目錄的 invalid 檔）",
            errors=errs,
            invalid_path=None if is_protected_signals_path(invalid) else invalid,
        )

    atomic_write(dest_path, text)
    if dated.resolve() != dest_path.resolve():
        atomic_write(dated, text)
    if invalid.is_file() and not is_protected_signals_path(invalid):
        try:
            invalid.unlink()
        except OSError:
            pass
    with open(dest_path, encoding="utf-8") as f:
        engine2, errs2 = validate_document(json.load(f))
    if errs2:
        raise SignalValidationError(f"寫入後重新驗證失敗：{errs2}", errors=errs2)

    return {
        "doc": doc,
        "dest": dest_path,
        "dated": dated,
        "engine": engine2,
        "incoming": incoming,
    }


def build_single_document(
    calc: dict,
    run_date: dt.date | str,
    report_path: str,
    *,
    generated_by: str = GENERATED_BY,
    generated_at: Optional[str] = None,
    root: Path = PACKAGE_ROOT,
) -> dict:
    """In-memory single-signal document (for Web UI download; does not write)."""
    if isinstance(run_date, str):
        run_date = dt.date.fromisoformat(run_date)
    sig = signal_from_calc(calc, run_date, report_path, root=root)
    doc = build_document([sig], run_date=run_date, generated_by=generated_by, generated_at=generated_at)
    engine, errs = validate_document(doc)
    if errs:
        raise SignalValidationError(f"schema 驗證失敗（{engine}）", errors=errs)
    return doc
