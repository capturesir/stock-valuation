"""CLI argparse interface, defaults, and help text must stay unchanged."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RUN_HELP = """\
usage: cli.py run [-h] --ticker TICKER [--date DATE] [--no-llm]
                  [--export-signals] [--email]

options:
  -h, --help        show this help message and exit
  --ticker TICKER
  --date DATE       估值日 YYYY-MM-DD（澳門）
  --no-llm          即使設定了 API 也不呼叫模型，只用規則後備
  --export-signals  驗證通過後合併匯出 B 組相容訊號檔至 out/signals.json
  --email           驗證通過後，按本機設定把報告電郵至部署者指定的收件信箱
"""

CHECK_HELP = """\
usage: cli.py check-filings [-h] --ticker TICKER [--rerun-if-needed]
                            [--date DATE]

options:
  -h, --help         show this help message and exit
  --ticker TICKER
  --rerun-if-needed
  --date DATE
"""


def _help(*args: str) -> str:
    proc = subprocess.run(
        [sys.executable, "-m", "src.pipeline.cli", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_run_help_unchanged():
    assert _help("run", "--help").strip() == RUN_HELP.strip()


def test_check_filings_help_unchanged():
    assert _help("check-filings", "--help").strip() == CHECK_HELP.strip()


def test_date_default_remains_2026_11_11():
    src = (ROOT / "src/pipeline/cli.py").read_text(encoding="utf-8")
    assert 'p_run.add_argument("--date", default="2026-11-11"' in src
    assert 'p_chk.add_argument("--date", default="2026-11-11")' in src


def test_importing_web_app_does_not_break_cli():
    import src.web.app  # noqa: F401
    assert _help("run", "--help").strip() == RUN_HELP.strip()
    assert _help("check-filings", "--help").strip() == CHECK_HELP.strip()
    proc = subprocess.run(
        [sys.executable, "-m", "src.pipeline.cli"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    # no subcommand → argparse error, not a crash from the web import
    assert proc.returncode != 0
    assert "run" in (proc.stderr + proc.stdout)
    assert "check-filings" in (proc.stderr + proc.stdout)


def test_send_report_help_lists_ticker():
    out = _help("send-report", "--help")
    assert "send-report" in out
    assert "--ticker" in out
    assert "收件信箱" in out
