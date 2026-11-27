"""Backend PostgreSQL tests for Document Library & Citation-Grounded Q&A (D13, alpha.5)."""
from __future__ import annotations

import zlib

from backend.app.services import doc_library


def _mini_pdf(text: str) -> bytes:
    stream = zlib.compress(f"BT /F1 12 Tf ({text}) Tj ET".encode("latin-1"))
    return b"%PDF-1.4\n1 0 obj\n<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream\n%%EOF"


def test_upload_list_detail_delete_and_permissions(client, member):
    # Upload a Markdown filing note and a HKEX PDF
    md = b"# NVDA 10-K Notes\n\n## Data Center\nBlackwell GPU architecture powers hyperscale AI training clusters.\n"
    r1 = client.post("/api/v1/stocks/NVDA/documents/upload",
                     files={"file": ("nvda_10k.md", md, "text/markdown")},
                     data={"title": "NVDA 10-K Summary"})
    assert r1.status_code == 201, r1.text
    doc = r1.json()
    assert doc["ticker"] == "NVDA" and doc["chunk_count"] >= 1 and doc["source_kind"] == "upload"

    # Re-uploading identical content is idempotent
    r1_dup = client.post("/api/v1/stocks/NVDA/documents/upload",
                         files={"file": ("nvda_10k.md", md, "text/markdown")})
    assert r1_dup.status_code == 201 and r1_dup.json()["id"] == doc["id"]

    # HKEX PDF upload gets classified as hkex_pdf with page number
    r_pdf = member.post("/api/v1/stocks/0700.HK/documents/upload",
                        files={"file": ("tencent_2025.pdf", _mini_pdf("Tencent WeChat MAU reached 1.38 billion"), "application/pdf")})
    assert r_pdf.status_code == 201, r_pdf.text
    assert r_pdf.json()["source_kind"] == "hkex_pdf"

    # Unsupported file type rejected
    bad = client.post("/api/v1/stocks/NVDA/documents/upload",
                      files={"file": ("evil.exe", b"MZ...", "application/octet-stream")})
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "doc_unsupported_type"

    # List & detail
    docs = client.get("/api/v1/stocks/NVDA/documents").json()
    assert len(docs) == 1 and docs[0]["id"] == doc["id"]
    detail = client.get(f"/api/v1/stocks/NVDA/documents/{doc['id']}").json()
    assert len(detail["chunks"]) == doc["chunk_count"]
    assert "Blackwell" in detail["chunks"][0]["content"]

    # Member cannot delete library documents; admin can
    assert member.delete(f"/api/v1/stocks/NVDA/documents/{doc['id']}").status_code == 403
    assert client.delete(f"/api/v1/stocks/NVDA/documents/{doc['id']}").status_code == 204
    assert client.get(f"/api/v1/stocks/NVDA/documents/{doc['id']}").status_code == 404


def test_qa_grounding_refusal_web_fallback_and_thread_privacy(client, member, monkeypatch):
    # Ingest a library document for NVDA
    html = (
        b"<html><body><h2>ITEM 1. BUSINESS</h2>"
        b"<p>NVIDIA designs Blackwell and Hopper GPUs with CUDA software for AI data centers.</p>"
        b"<h2>ITEM 1A. RISK FACTORS</h2>"
        b"<p>Export licensing rules and TSMC manufacturing capacity could constrain GPU supply.</p>"
        b"</body></html>"
    )
    up = client.post("/api/v1/stocks/NVDA/documents/upload",
                     files={"file": ("nvda_10k.html", html, "text/html")},
                     data={"title": "NVDA FY2026 10-K"})
    assert up.status_code == 201

    # 1. Library-grounded question (no LLM configured → deterministic extractive cited answer)
    q1 = client.post("/api/v1/stocks/NVDA/qa/ask",
                     json={"question": "What GPU architectures and software does NVIDIA build for AI data centers?"},
                     headers={"Accept-Language": "en"})
    assert q1.status_code == 200, q1.text
    body1 = q1.json()
    assert not body1["message"]["insufficient_evidence"]
    assert not body1["message"]["used_web"]
    assert len(body1["message"]["citations"]) >= 1
    assert body1["message"]["citations"][0]["source_type"] == "library"
    assert "[L1]" in body1["message"]["content"]
    thread_id = body1["thread_id"]

    # 2. Unanswerable question with allow_web=False → strict refusal
    q2 = client.post("/api/v1/stocks/NVDA/qa/ask",
                     json={"question": "What is the price of coffee on Mars in 2099?", "thread_id": thread_id, "allow_web": False},
                     headers={"Accept-Language": "zh-Hant"})
    assert q2.status_code == 200
    assert q2.json()["message"]["insufficient_evidence"] is True
    assert q2.json()["message"]["citations"] == []
    assert "文件庫沒有相關資料" in q2.json()["message"]["content"]

    # 3. Web fallback archives fetched page into stock_documents and marks citation as source_type="web"
    monkeypatch.setattr(
        doc_library, "_web_search",
        lambda ticker, query, limit=3: [{
            "title": "NVDA Rubin Next-Gen Chip Announcement",
            "url": "https://news.example.com/nvda-rubin",
            "content": "NVIDIA announced the Rubin next-generation AI accelerator architecture for 2026 deployment.",
        }],
    )
    q3 = client.post("/api/v1/stocks/NVDA/qa/ask",
                     json={"question": "Tell me about the latest Rubin next-generation AI accelerator announcement.",
                           "thread_id": thread_id, "allow_web": True},
                     headers={"Accept-Language": "en"})
    assert q3.status_code == 200, q3.text
    b3 = q3.json()
    assert b3["message"]["used_web"] is True
    assert any(c["source_type"] == "web" and c["url"] == "https://news.example.com/nvda-rubin" for c in b3["message"]["citations"])
    # Web source was archived into the stock's library
    docs = client.get("/api/v1/stocks/NVDA/documents").json()
    assert any(d["source_kind"] == "web" and d["url"] == "https://news.example.com/nvda-rubin" for d in docs)

    # 4. LLM path verifies citations and strips hallucinated IDs
    monkeypatch.setattr(doc_library, "_call_llm", lambda db, msgs: ("Blackwell powers AI data centers [L1] and fake [L99].", "qwen3:8b"))
    q4 = client.post("/api/v1/stocks/NVDA/qa/ask",
                     json={"question": "Does Blackwell power AI data centers?", "thread_id": thread_id},
                     headers={"Accept-Language": "en"})
    assert q4.status_code == 200
    assert "[L1]" in q4.json()["message"]["content"] and "[L99]" not in q4.json()["message"]["content"]
    assert q4.json()["message"]["model"] == "qwen3:8b"

    # 5. Thread privacy: member cannot read, append to, or delete admin's thread
    assert member.get("/api/v1/stocks/NVDA/qa/threads").json() == []
    assert member.get(f"/api/v1/stocks/NVDA/qa/threads/{thread_id}").status_code == 404
    assert member.delete(f"/api/v1/stocks/NVDA/qa/threads/{thread_id}").status_code == 404
    assert member.post("/api/v1/stocks/NVDA/qa/ask",
                       json={"question": "hi", "thread_id": thread_id}).status_code == 404

    # Owner reads full thread and deletes it
    th = client.get(f"/api/v1/stocks/NVDA/qa/threads/{thread_id}").json()
    assert len(th["messages"]) == 8        # 4 user + 4 assistant turns
    assert client.delete(f"/api/v1/stocks/NVDA/qa/threads/{thread_id}").status_code == 204
    assert client.get(f"/api/v1/stocks/NVDA/qa/threads/{thread_id}").status_code == 404


def test_sync_valuation_and_numeric_tool_grounding(client, tmp_path):
    import shutil
    from backend.app import ROOT
    from backend.app.db import session
    from backend.app.services.legacy import import_legacy

    out = tmp_path / "out"
    shutil.copytree(ROOT / "out" / "2026-11-11_NVDA", out / "2026-11-11_NVDA")
    with session() as db:
        assert import_legacy(db, out_dir=out) == 1

    sync = client.post("/api/v1/stocks/NVDA/documents/sync", params={"include_sec": False})
    assert sync.status_code == 200, sync.text
    assert sync.json()["added"] == 1 and sync.json()["total_documents"] == 1

    # Idempotent second sync
    sync2 = client.post("/api/v1/stocks/NVDA/documents/sync", params={"include_sec": False})
    assert sync2.status_code == 200 and sync2.json()["added"] == 0 and sync2.json()["skipped"] == 1

    # Asking about valuation fair value invokes the structured numeric tool (used_statements=True)
    ans = client.post("/api/v1/stocks/NVDA/qa/ask",
                      json={"question": "What is the latest NVDA valuation fair value and WACC?"},
                      headers={"Accept-Language": "en"})
    assert ans.status_code == 200, ans.text
    msg = ans.json()["message"]
    assert msg["used_statements"] is True
    assert any(c["source_type"] == "statements" for c in msg["citations"])
    assert not msg["insufficient_evidence"]
