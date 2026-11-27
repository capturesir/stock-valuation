"""Backend API tests for D11 HKEX free financial statements (Yahoo + Annual Report PDF cross-check)."""
from __future__ import annotations

from backend.app.services import statements as stmt_svc


def _minimal_pdf_with_text(lines: list[str]) -> bytes:
    """Build a valid single-page PDF containing exact text lines parseable by pypdf (no external deps)."""
    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    stream_parts = ["BT", "/F1 10 Tf", "40 760 Td", "14 TL"]
    for idx, ln in enumerate(lines):
        if idx == 0:
            stream_parts.append(f"({_esc(ln)}) Tj")
        else:
            stream_parts.append(f"T* ({_esc(ln)}) Tj")
    stream_parts.append("ET")
    stream = "\n".join(stream_parts).encode("latin-1")

    objs: list[bytes] = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n",
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
        f"5 0 obj << /Length {len(stream)} >> stream\n".encode("latin-1") + stream + b"\nendstream endobj\n",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objs:
        offsets.append(len(out))
        out.extend(obj)
    xref = len(out)
    out.extend(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("latin-1"))
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("latin-1"))
    out.extend(f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("latin-1"))
    return bytes(out)


def test_hkex_sync_pdf_upload_cross_check_and_standard_statements(client, monkeypatch):
    yahoo_payload = {
        "ticker": "0700.HK",
        "source": "Yahoo Finance via yfinance",
        "annual": {
            "2024-12-31": {
                "Total Revenue": 660_257_000_000,
                "Cost Of Revenue": 311_011_000_000,
                "Gross Profit": 349_246_000_000,
                "Operating Income": 200_000_000_000,   # intentionally ~3.9% off from PDF 208,099M
                "Net Income": 194_073_000_000,
                "Total Assets": 1_780_995_000_000,
                "Total Liabilities Net Minority Interest": 727_099_000_000,
                "Total Equity Gross Minority Interest": 1_053_896_000_000,
                "Operating Cash Flow": 258_521_000_000,
                "Capital Expenditure": -76_760_000_000,
            }
        },
    }
    monkeypatch.setattr(stmt_svc, "_yahoo_annual", lambda ticker: yahoo_payload if ticker == "0700.HK" else None)
    monkeypatch.setattr(stmt_svc, "_yahoo_meta", lambda ticker: {
        "name": "Tencent Holdings Limited", "currency": "CNY", "sector": "Communication Services", "industry": "Internet Content"
    })

    # 1. Status recognizes HKEX ticker without requiring SEC contact
    st = client.get("/api/v1/stocks/0700.HK/statements/status").json()
    assert st["hk_supported"] is True
    assert st["synced"] is False

    # 2. Admin sync pulls Yahoo annual statements into 4-layer pipeline
    r = client.post("/api/v1/admin/statements/sync/0700.HK")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cik"] == "HK00000700"
    assert body["facts_added"] >= 10

    # 3. Upload HKEX Annual Report PDF and cross-check against Yahoo
    pdf = _minimal_pdf_with_text([
        "CONSOLIDATED INCOME STATEMENT For the year ended 31 December 2024 (RMB million)",
        "Note 2024",
        "Revenue 5 660,257",
        "Cost of sales (311,011)",
        "Gross profit 349,246",
        "Operating profit 208,099",
        "Profit attributable to equity holders of the Company 194,073",
        "Total assets 1,780,995",
        "Total liabilities 727,099",
        "Total equity 1,053,896",
    ])
    up = client.post(
        "/api/v1/stocks/0700.HK/statements/hkex-pdf",
        files={"file": ("0700_ar_2024.pdf", pdf, "application/pdf")},
        data={"document_url": "https://www1.hkexnews.hk/listedco/listconews/sehk/2025/0408/2025040800123.pdf"},
    )
    assert up.status_code == 200, up.text
    up_body = up.json()
    assert up_body["currency"] == "CNY"
    assert up_body["scale"] == 1_000_000
    assert up_body["cross_check"] is not None
    assert up_body["cross_check"]["discrepancy_count"] == 1
    assert up_body["cross_check"]["discrepancies"][0]["concept"] == "ProfitLossFromOperatingActivities"

    # 4. Standard statements endpoint works for 0700.HK and prefers HKEX-AR over YAHOO-AR with clickable HKEX URL
    std_resp = client.get("/api/v1/stocks/0700.HK/statements")
    assert std_resp.status_code == 200, std_resp.text
    std = std_resp.json()
    assert std["currency"] == "CNY"
    assert std["checks"]["grades"]["FY2024"] == "A"
    op_row = next(row for row in std["rows"] if row["code"] == "operating_income")
    cell = op_row["cells"][0]
    assert cell["value"] == "208099000000"
    assert cell["form"] == "HKEX-AR"
    assert "hkexnews.hk" in cell["document_url"]
