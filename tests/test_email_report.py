"""Email settings must come from the deployer; no built-in mailbox."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from src.export.email_report import (
    ENV_FROM,
    ENV_SMTP_HOST,
    ENV_SMTP_PASSWORD,
    ENV_SMTP_USER,
    ENV_TO,
    EmailConfigError,
    build_message,
    find_report_attachments,
    resolve_email_settings,
    send_report,
    settings_error_message,
)

ROOT = Path(__file__).resolve().parents[1]
# SHA-256 of known author mailbox local-parts; hashes only, never the addresses.
FORBIDDEN_SHA256 = {
    "8637b2f42d3b427bdd4373aff9ae102c6ce62955310158ae017c24c1076b51fb",
    "c6ecb80758a6733a26c9f95a9113aacaf4cd9528161078ebb39611fa6b815dae",
}
TOKEN_RE = re.compile(r"[A-Za-z0-9._%+-]+")
SKIP_PARTS = {".git", "__pycache__", ".pytest_cache", "data/damodaran"}
TEXT_SUFFIX = {
    ".py", ".md", ".yaml", ".yml", ".json", ".txt", ".html", ".csv",
    ".example", ".ini", ".cfg", ".toml", ".bat", ".ps1", ".sh",
}


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.lower().encode("utf-8")).hexdigest()


def test_package_has_no_author_mailboxes():
    hits = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_PARTS for part in path.parts) or "damodaran" in path.parts:
            continue
        raw = path.read_bytes()
        text = raw.decode("utf-8", "replace")
        # Real mailbox addresses on consumer hosts are forbidden.
        # The SMTP server name smtp.gmail.com is allowed (no leading '@').
        if re.search(r"[\w.+-]+@(?:gmail|outlook|hotmail|icloud)\.com", text, re.I):
            hits.append(f"{path.relative_to(ROOT)}:consumer-mailbox-address")
        for token in TOKEN_RE.findall(text):
            if _token_hash(token) in FORBIDDEN_SHA256:
                hits.append(str(path.relative_to(ROOT)))
    assert hits == []


def test_no_default_mailboxes_in_empty_config():
    settings = resolve_email_settings({}, environ={})
    assert settings["to_email"] == ""
    assert settings["from_email"] == ""
    assert settings["smtp_host"] == ""
    assert settings["smtp_password"] == ""
    assert settings["smtp_port"] == 587
    assert settings["smtp_starttls"] is True


def test_env_overrides_config_and_password_not_read_from_file():
    cfg = {
        "report": {
            "to_email": "config-to@example.com",
            "from_email": "config-from@example.com",
            "smtp_host": "smtp.config.example",
            "smtp_port": 465,
            "smtp_user": "config-user",
            "smtp_password": "should-never-be-used",
            "smtp_password_env": "VALUATION_SMTP_PASSWORD",
            "smtp_starttls": False,
        }
    }
    env = {
        ENV_TO: "env-to@example.com",
        ENV_FROM: "env-from@example.com",
        ENV_SMTP_HOST: "smtp.env.example",
        ENV_SMTP_USER: "env-user",
        ENV_SMTP_PASSWORD: "env-secret",
        "VALUATION_SMTP_PORT": "2525",
        "VALUATION_SMTP_STARTTLS": "1",
    }
    settings = resolve_email_settings(cfg, environ=env)
    assert settings["to_email"] == "env-to@example.com"
    assert settings["from_email"] == "env-from@example.com"
    assert settings["smtp_host"] == "smtp.env.example"
    assert settings["smtp_user"] == "env-user"
    assert settings["smtp_password"] == "env-secret"
    assert settings["smtp_port"] == 2525
    assert settings["smtp_starttls"] is True


def test_password_in_config_yaml_is_ignored():
    cfg = {"report": {"smtp_password": "from-file", "to_email": "a@example.com"}}
    settings = resolve_email_settings(cfg, environ={})
    assert settings["smtp_password"] == ""


def test_send_refuses_when_settings_missing():
    with pytest.raises(EmailConfigError) as exc:
        send_report(
            [],
            subject="x",
            body="y",
            cfg={},
            environ={},
        )
    msg = str(exc.value)
    assert ENV_TO in msg
    assert ENV_FROM in msg
    assert ENV_SMTP_HOST in msg
    assert ENV_SMTP_PASSWORD in msg
    assert "預設私人信箱" in msg


def test_settings_error_lists_all_missing():
    text = settings_error_message(
        [(ENV_TO, "收件信箱"), (ENV_FROM, "寄件信箱")]
    )
    assert ENV_TO in text and ENV_FROM in text


class _DummySMTP:
    def __init__(self, host, port, timeout=30):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.logged_in = None
        self.sent = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self, context=None):
        self.started_tls = True

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, msg):
        self.sent = msg


def test_send_uses_deployer_addresses(tmp_path):
    attachment = tmp_path / "report.xlsx"
    attachment.write_bytes(b"PK\x03\x04fake")
    factory_holder = {}

    def factory(host, port, timeout=30):
        smtp = _DummySMTP(host, port, timeout)
        factory_holder["smtp"] = smtp
        return smtp

    env = {
        ENV_TO: "reader@example.com",
        ENV_FROM: "sender@example.com",
        ENV_SMTP_HOST: "smtp.example.com",
        ENV_SMTP_USER: "sender@example.com",
        ENV_SMTP_PASSWORD: "app-password",
    }
    result = send_report(
        [attachment],
        subject="估值報告 TEST",
        body="附件為研究草稿。",
        cfg={},
        environ=env,
        smtp_factory=factory,
    )
    smtp = factory_holder["smtp"]
    assert result["to"] == "reader@example.com"
    assert result["from"] == "sender@example.com"
    assert smtp.host == "smtp.example.com"
    assert smtp.port == 587
    assert smtp.started_tls is True
    assert smtp.logged_in == ("sender@example.com", "app-password")
    assert smtp.sent["To"] == "reader@example.com"
    assert smtp.sent["From"] == "sender@example.com"
    assert smtp.sent["Subject"] == "估值報告 TEST"


def test_build_message_attaches_file(tmp_path):
    p = tmp_path / "a.xlsx"
    p.write_bytes(b"data")
    settings = {
        "to_email": "to@example.com",
        "from_email": "from@example.com",
    }
    msg = build_message(settings=settings, subject="s", body="b", attachments=[p])
    assert msg["To"] == "to@example.com"
    assert msg["From"] == "from@example.com"
    payloads = list(msg.iter_attachments())
    assert len(payloads) == 1
    assert payloads[0].get_filename() == "a.xlsx"


def test_find_report_attachments(tmp_path):
    folder = tmp_path / "out" / "2026-11-11_NVDA"
    folder.mkdir(parents=True)
    xlsx = folder / "2026-11-11_NVDA_FCFF.xlsx"
    md = folder / "2026-11-11_NVDA_valuation.md"
    xlsx.write_bytes(b"x")
    md.write_text("hi", encoding="utf-8")
    found = find_report_attachments(tmp_path, "NVDA", "2026-11-11")
    assert xlsx in found and md in found


def test_find_report_attachments_missing(tmp_path):
    with pytest.raises(EmailConfigError):
        find_report_attachments(tmp_path, "NVDA", "2026-11-11")
