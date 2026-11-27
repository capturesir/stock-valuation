"""Section-aware chunking (~500 tokens) and hierarchical extractive summaries."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .parser import ParsedDocument, Section

TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9]+(?:[._/-][A-Za-z0-9]+)*")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？.!?])\s+|\n+")


@dataclass(frozen=True)
class Chunk:
    seq: int
    section: str
    page: Optional[int]
    content: str
    token_count: int
    url: Optional[str] = None


def count_tokens(text: str) -> int:
    """Bilingual approximate token count: English/numeric words + individual CJK characters."""
    return len(TOKEN_RE.findall(text or ""))


def _split_long_paragraph(para: str, target_tokens: int, overlap_tokens: int) -> list[str]:
    sentences = [s.strip() for s in SENTENCE_SPLIT_RE.split(para) if s.strip()]
    if not sentences:
        return []
    out: list[str] = []
    cur: list[str] = []
    cur_tok = 0
    for s in sentences:
        st = count_tokens(s)
        if st > target_tokens:
            if cur:
                out.append(" ".join(cur).strip())
                cur, cur_tok = [], 0
            # Hard-slice very long single sentence by character ratio
            step = max(200, int(len(s) * target_tokens / max(st, 1)))
            ov = max(40, int(step * overlap_tokens / max(target_tokens, 1)))
            i = 0
            while i < len(s):
                piece = s[i:i + step].strip()
                if piece:
                    out.append(piece)
                if i + step >= len(s):
                    break
                i += max(1, step - ov)
            continue
        if cur and cur_tok + st > target_tokens:
            out.append(" ".join(cur).strip())
            # Keep trailing overlap sentences
            ov_buf: list[str] = []
            ov_tok = 0
            for prev in reversed(cur):
                pt = count_tokens(prev)
                if ov_tok + pt > overlap_tokens and ov_buf:
                    break
                ov_buf.append(prev)
                ov_tok += pt
            cur = list(reversed(ov_buf))
            cur_tok = ov_tok
        cur.append(s)
        cur_tok += st
    if cur:
        out.append(" ".join(cur).strip())
    return out


def chunk_document(doc: ParsedDocument, *, target_tokens: int = 500, overlap_tokens: int = 60) -> list[Chunk]:
    """Split a parsed document into section-preserving chunks of ~500 tokens each."""
    chunks: list[Chunk] = []
    seq = 0
    for sec in doc.sections:
        paras = [p.strip() for p in re.split(r"\n{2,}", sec.content) if p.strip()]
        if not paras:
            continue
        buf: list[str] = []
        buf_tok = 0

        def emit(parts: list[str]) -> None:
            nonlocal seq
            text = "\n\n".join(parts).strip()
            if not text:
                return
            seq += 1
            chunks.append(Chunk(
                seq=seq,
                section=sec.title or doc.title,
                page=sec.page,
                content=text,
                token_count=max(1, count_tokens(text)),
                url=sec.url or doc.url,
            ))

        for p in paras:
            pt = count_tokens(p)
            if pt > target_tokens:
                if buf:
                    emit(buf)
                    buf, buf_tok = [], 0
                for sub in _split_long_paragraph(p, target_tokens, overlap_tokens):
                    emit([sub])
                continue
            if buf and buf_tok + pt > target_tokens:
                emit(buf)
                # Carry over last paragraph if it fits overlap budget
                if count_tokens(buf[-1]) <= overlap_tokens:
                    buf = [buf[-1]]
                    buf_tok = count_tokens(buf[0])
                else:
                    buf, buf_tok = [], 0
            buf.append(p)
            buf_tok += pt
        if buf:
            emit(buf)
    return chunks


def summarize_section(text: str, max_chars: int = 320) -> str:
    """Fast extractive summary for a section: lead sentences within ``max_chars``."""
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    sentences = [s.strip() for s in SENTENCE_SPLIT_RE.split(cleaned) if len(s.strip()) >= 15]
    if not sentences:
        return cleaned[:max_chars - 1].rstrip() + "…"
    out = ""
    for s in sentences:
        if out and len(out) + len(s) + 1 > max_chars:
            break
        out = f"{out} {s}".strip() if out else s
    if not out:
        return cleaned[:max_chars - 1].rstrip() + "…"
    return out[:max_chars]


def summarize_document(sections: list[Section], max_chars: int = 900) -> tuple[str, list[dict]]:
    """Produce a hierarchical summary: (document_summary, [{section, page, summary}])."""
    sec_summaries: list[dict] = []
    for sec in sections:
        s = summarize_section(sec.content, max_chars=260)
        if s:
            sec_summaries.append({"section": sec.title, "page": sec.page, "summary": s})
    parts: list[str] = []
    total = 0
    for item in sec_summaries:
        line = f"[{item['section']}] {item['summary']}" if item["section"] else item["summary"]
        if parts and total + len(line) + 2 > max_chars:
            break
        parts.append(line)
        total += len(line) + 2
    return "\n".join(parts)[:max_chars], sec_summaries
