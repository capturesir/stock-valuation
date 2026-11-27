"""Branding (requirement 5.2): name and logos set by an admin, visible before sign-in."""
from __future__ import annotations

import io

SVG_OK = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'


def _png(w=300, h=120) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), (31, 95, 191, 255)).save(buf, format="PNG")
    return buf.getvalue()


def test_default_branding_is_public(client, client_as):
    anon = client_as()
    b = anon.get("/api/v1/branding").json()
    assert b["name"] == "估值工作台" and b["logo_light_url"] is None
    m = anon.get("/api/v1/branding/manifest.webmanifest")
    assert m.headers["content-type"].startswith("application/manifest+json")
    assert m.json()["icons"][0]["src"] == "/icon-192.png"


def test_admin_sets_name_and_png_logo_and_icons_follow(client, client_as):
    assert client.put("/api/v1/admin/branding", json={"name": "Hoi 投資研究"}).json()["name"] == "Hoi 投資研究"
    r = client.post("/api/v1/admin/branding/logo/light", files={"file": ("logo.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["logo_light_url"].startswith("/api/v1/branding/logo/light?v=") and b["logo_dark_url"] == b["logo_light_url"]
    anon = client_as()
    assert anon.get(b["logo_light_url"]).headers["content-type"] == "image/png"
    man = anon.get("/api/v1/branding/manifest.webmanifest").json()
    assert man["name"] == "Hoi 投資研究" and man["icons"][0]["src"].startswith("/api/v1/branding/icon/192.png")
    from PIL import Image
    icon = Image.open(io.BytesIO(anon.get("/api/v1/branding/icon/512-maskable.png").content))
    assert icon.size == (512, 512)
    # removing the light logo falls back to the bundled icons
    assert client.delete("/api/v1/admin/branding/logo/light").json()["logo_light_url"] is None
    assert anon.get("/api/v1/branding/manifest.webmanifest").json()["icons"][0]["src"] == "/icon-192.png"


def test_svg_logo_is_served_with_a_script_blocking_policy(client, client_as):
    r = client.post("/api/v1/admin/branding/logo/dark", files={"file": ("logo.svg", SVG_OK, "image/svg+xml")})
    url = r.json()["logo_dark_url"]
    resp = client_as().get(url)
    assert resp.headers["content-type"].startswith("image/svg+xml")
    assert "default-src 'none'" in resp.headers["content-security-policy"]


def test_unsafe_or_oversized_logos_are_refused(client):
    bad = [b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
           b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>',
           b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://evil.example/x.png"/></svg>',
           b"GIF89a....", b"\x89PNG\r\n\x1a\n" + b"broken", b"<svg>" + b" " * (512 * 1024)]
    for data in bad:
        r = client.post("/api/v1/admin/branding/logo/light", files={"file": ("x", data, "application/octet-stream")})
        assert r.status_code == 400, data[:40]
    assert client.post("/api/v1/admin/branding/logo/sepia", files={"file": ("x.svg", SVG_OK, "image/svg+xml")}).status_code == 400
    assert client.put("/api/v1/admin/branding", json={"name": ""}).status_code == 422


def test_members_cannot_change_branding(member):
    assert member.put("/api/v1/admin/branding", json={"name": "x"}).status_code == 403
    assert member.post("/api/v1/admin/branding/logo/light",
                       files={"file": ("logo.svg", SVG_OK, "image/svg+xml")}).status_code == 403


def test_run_excel_and_report_carry_the_brand_but_out_stays_neutral(configured, monkeypatch, tmp_path):
    """5.2: the archived Excel and report of a v2 run show the system name and PNG logo; the engine's
    own out/ files are left as the v1 tools wrote them."""
    import json
    import time
    from pathlib import Path

    from openpyxl import load_workbook

    from backend.app.config import get_settings
    from src.export.report_md import write_report
    from src.export.workbook import build_workbook
    from src.pipeline import orchestrator

    root = Path(__file__).resolve().parents[2]
    calc = json.loads((root / "out" / "2026-11-11_NVDA" / "2026-11-11_NVDA.json").read_text(encoding="utf-8"))
    out = tmp_path / "out" / "2026-11-18_NVDA"
    out.mkdir(parents=True)

    def fake_run_ticker(ticker, cfg=None, use_llm=True, valuation_date="", progress=None, should_stop=None,
                        skip_extract=False, apply_adjustments=True, statements=None):
        xlsx, md, js = out / "2026-11-18_NVDA_FCFF.xlsx", out / "2026-11-18_NVDA_valuation.md", out / "2026-11-18_NVDA.json"
        build_workbook(calc, str(xlsx))
        write_report(calc, str(md))
        js.write_text(json.dumps(calc), encoding="utf-8")
        return {"json": calc, "path": str(js), "xlsx": str(xlsx), "md": str(md), "verify_pass": True, "verify_errors": []}

    monkeypatch.setattr(orchestrator, "run_ticker", fake_run_ticker)
    configured.put("/api/v1/admin/branding", json={"name": "Hoi 投資研究"})
    configured.post("/api/v1/admin/branding/logo/light", files={"file": ("logo.png", _png(), "image/png")})

    job = configured.post("/api/v1/valuations", json={"ticker": "NVDA", "mode": "none"}).json()["id"]
    end = time.time() + 10
    while configured.get(f"/api/v1/jobs/{job}").json()["status"] not in ("done", "failed", "cancelled"):
        assert time.time() < end, "job did not finish"
        time.sleep(0.05)
    run = configured.get("/api/v1/stocks/NVDA/valuations").json()["runs"][0]
    excel = configured.get(f"/api/v1/runs/{run['id']}/excel")
    assert excel.status_code == 200
    dl = tmp_path / "dl.xlsx"
    dl.write_bytes(excel.content)
    wb = load_workbook(dl)
    assert wb["摘要"]["A2"].value == "Hoi 投資研究" and len(wb["摘要"]._images) == 1
    assert all(ws.oddHeader.left.text == "Hoi 投資研究" for ws in wb.worksheets)
    report = get_settings().data_dir / "runs" / job / "2026-11-18_NVDA_valuation.md"
    assert report.read_text(encoding="utf-8").startswith("> Hoi 投資研究\n\n# ")
    # the engine's shared output is untouched
    assert load_workbook(out / "2026-11-18_NVDA_FCFF.xlsx")["摘要"]["A2"].value is None
    assert (out / "2026-11-18_NVDA_valuation.md").read_text(encoding="utf-8").startswith("# ")

    # localized Excel & Markdown report endpoints (?lang= and Accept-Language), stamped with brand
    en_xlsx = configured.get(f"/api/v1/runs/{run['id']}/excel", params={"lang": "en"})
    assert en_xlsx.status_code == 200
    dl_en = tmp_path / "dl_en.xlsx"
    dl_en.write_bytes(en_xlsx.content)
    wb_en = load_workbook(dl_en)
    assert wb_en.sheetnames[0] == "Summary"
    assert wb_en["Summary"]["A2"].value == "Hoi 投資研究" and len(wb_en["Summary"]._images) == 1

    hans_xlsx = configured.get(f"/api/v1/runs/{run['id']}/excel", headers={"Accept-Language": "zh-CN"})
    assert hans_xlsx.status_code == 200
    dl_hans = tmp_path / "dl_hans.xlsx"
    dl_hans.write_bytes(hans_xlsx.content)
    assert load_workbook(dl_hans).sheetnames[1] == "计算"

    en_md = configured.get(f"/api/v1/runs/{run['id']}/report", params={"lang": "en"})
    assert en_md.status_code == 200
    assert en_md.text.startswith("> Hoi 投資研究\n\n# ")
    assert "## Five-Scenario Fair Value per Share" in en_md.text

    hans_md = configured.get(f"/api/v1/runs/{run['id']}/report", headers={"Accept-Language": "zh-CN"})
    assert hans_md.status_code == 200
    assert "## 五情景每股理论价" in hans_md.text

    missing = configured.get("/api/v1/runs/does-not-exist/report", headers={"Accept-Language": "en"})
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "no_report"
