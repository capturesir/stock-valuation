"""Optional valuation-report email.

Recipient, sender and SMTP settings come only from the deployer's
environment (preferred) or local config.yaml. This module never ships
a default mailbox and must not contain any personal address.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Callable, Iterable, Optional

ENV_TO = "VALUATION_REPORT_TO"
ENV_FROM = "VALUATION_REPORT_FROM"
ENV_SMTP_HOST = "VALUATION_SMTP_HOST"
ENV_SMTP_PORT = "VALUATION_SMTP_PORT"
ENV_SMTP_USER = "VALUATION_SMTP_USER"
ENV_SMTP_PASSWORD = "VALUATION_SMTP_PASSWORD"
ENV_SMTP_STARTTLS = "VALUATION_SMTP_STARTTLS"

# Labels used in error messages when a setting is missing.
REQUIRED = (
    ("to_email", ENV_TO, "收件信箱"),
    ("from_email", ENV_FROM, "寄件信箱"),
    ("smtp_host", ENV_SMTP_HOST, "SMTP 主機"),
    ("smtp_password", ENV_SMTP_PASSWORD, "SMTP 密碼"),
)


class EmailConfigError(Exception):
    """Settings are incomplete; nothing was sent."""


class EmailSendError(Exception):
    """SMTP failed after settings were present."""


def _strip(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _truthy(value, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", "n"}


def resolve_email_settings(cfg: Optional[dict] = None, environ=None) -> dict:
    """Merge env vars (first) with optional config.yaml ``report`` section.

    The SMTP password is read only from the environment, never from a
    config file, even if someone wrote it there.
    """
    environ = environ if environ is not None else os.environ
    report = (cfg or {}).get("report") or {}
    port_raw = _strip(environ.get(ENV_SMTP_PORT)) or report.get("smtp_port") or 587
    try:
        smtp_port = int(port_raw)
    except (TypeError, ValueError) as exc:
        raise EmailConfigError(f"{ENV_SMTP_PORT} 必須是數字，目前為 {port_raw!r}") from exc

    pw_env = _strip(report.get("smtp_password_env")) or ENV_SMTP_PASSWORD
    password = _strip(environ.get(pw_env)) or _strip(environ.get(ENV_SMTP_PASSWORD))

    starttls_raw = environ.get(ENV_SMTP_STARTTLS)
    if starttls_raw is None or str(starttls_raw).strip() == "":
        starttls_raw = report.get("smtp_starttls")

    user = _strip(environ.get(ENV_SMTP_USER)) or _strip(report.get("smtp_user"))
    from_email = _strip(environ.get(ENV_FROM)) or _strip(report.get("from_email"))
    if not user:
        user = from_email

    return {
        "to_email": _strip(environ.get(ENV_TO)) or _strip(report.get("to_email")),
        "from_email": from_email,
        "smtp_host": _strip(environ.get(ENV_SMTP_HOST)) or _strip(report.get("smtp_host")),
        "smtp_port": smtp_port,
        "smtp_user": user,
        "smtp_password": password,
        "smtp_starttls": _truthy(starttls_raw, True),
    }


def missing_settings(settings: dict) -> list[tuple[str, str]]:
    """Return (env_name, chinese_label) for required fields that are empty."""
    out = []
    for key, env_name, label in REQUIRED:
        if not _strip(settings.get(key)):
            out.append((env_name, label))
    return out


def settings_error_message(missing: Iterable[tuple[str, str]]) -> str:
    names = "、".join(f"{label}（{env}）" for env, label in missing)
    return (
        f"尚未設定齊全，不能寄信：缺少 {names}。"
        "請在本機用環境變數設定你自己的收件與寄件信箱及 SMTP（見 README 第 2.4 節）。"
        "未設定時系統只在本機保留檔案，不會使用任何預設私人信箱。"
    )


def find_report_attachments(root: Path, ticker: str, date: str) -> list[Path]:
    folder = Path(root) / "out" / f"{date}_{ticker}"
    if not folder.is_dir():
        raise EmailConfigError(f"找不到估值輸出目錄：{folder}")
    paths: list[Path] = []
    for pattern in (f"*{ticker}*_FCFF.xlsx", f"{date}_{ticker}_FCFF.xlsx", "*.xlsx"):
        found = sorted(p for p in folder.glob(pattern) if p.is_file())
        if found:
            paths.extend(found)
            break
    for pattern in (f"*{ticker}*_valuation.md", "*_valuation.md", "*.md"):
        found = sorted(p for p in folder.glob(pattern) if p.is_file())
        if found:
            paths.extend(found)
            break
    # de-dupe while keeping order
    uniq: list[Path] = []
    seen = set()
    for p in paths:
        key = p.resolve()
        if key not in seen:
            uniq.append(p)
            seen.add(key)
    if not uniq:
        raise EmailConfigError(f"在 {folder} 找不到可附加的 Excel 或說明檔")
    return uniq


def build_message(
    *,
    settings: dict,
    subject: str,
    body: str,
    attachments: Iterable[Path],
) -> EmailMessage:
    msg = EmailMessage()
    msg["To"] = settings["to_email"]
    msg["From"] = settings["from_email"]
    msg["Subject"] = subject
    msg.set_content(body)
    for path in attachments:
        path = Path(path)
        data = path.read_bytes()
        maintype, subtype = "application", "octet-stream"
        suffix = path.suffix.lower()
        if suffix == ".xlsx":
            maintype, subtype = (
                "application",
                "vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        elif suffix in {".md", ".txt"}:
            maintype, subtype = "text", "plain"
        elif suffix == ".pdf":
            maintype, subtype = "application", "pdf"
        msg.add_attachment(
            data,
            maintype=maintype,
            subtype=subtype,
            filename=path.name,
        )
    return msg


def default_subject(ticker: str, date: str, fair_value=None, currency: str = "") -> str:
    if fair_value is None:
        return f"估值報告 {ticker}（{date}）"
    ccy = f" {currency}" if currency else ""
    return f"估值報告 {ticker}（{date}）基準理論價 {fair_value}{ccy}"


def default_body(ticker: str, date: str) -> str:
    return (
        f"附件為 {ticker} 在 {date} 的估值 Excel 與說明（如有）。\n"
        "產出供研究草稿使用，不構成投資建議。請自行核對來源與假設。\n"
    )


def send_report(
    attachments: Iterable[Path],
    *,
    subject: str,
    body: str,
    cfg: Optional[dict] = None,
    environ=None,
    smtp_factory: Optional[Callable] = None,
) -> dict:
    settings = resolve_email_settings(cfg, environ=environ)
    missing = missing_settings(settings)
    if missing:
        raise EmailConfigError(settings_error_message(missing))
    paths = [Path(p) for p in attachments]
    for p in paths:
        if not p.is_file():
            raise EmailConfigError(f"附件不存在：{p}")
    msg = build_message(settings=settings, subject=subject, body=body, attachments=paths)
    factory = smtp_factory or smtplib.SMTP
    try:
        with factory(settings["smtp_host"], settings["smtp_port"], timeout=30) as smtp:
            if settings["smtp_starttls"]:
                smtp.starttls(context=ssl.create_default_context())
            if settings["smtp_user"] or settings["smtp_password"]:
                smtp.login(settings["smtp_user"], settings["smtp_password"])
            smtp.send_message(msg)
    except EmailConfigError:
        raise
    except Exception as exc:  # noqa: BLE001 — surface any SMTP failure
        raise EmailSendError(f"寄信失敗：{exc}") from exc
    return {
        "to": settings["to_email"],
        "from": settings["from_email"],
        "attachments": [str(p) for p in paths],
    }
