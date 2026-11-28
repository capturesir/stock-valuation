"""Document library & citation-grounded Q&A core (2.0.0-alpha.5, D13).

Pure Python library (no FastAPI or SQLAlchemy imports):
- ``parser``: SEC HTML filings (10-K/10-Q/8-K), Markdown reports, PDFs and plain text → sections
- ``chunker``: section-aware ~500-token chunking and hierarchical document/section summaries
- ``retrieval``: bilingual (EN + Trad/Simp CJK) BM25 + cross-lingual financial expansion + dense cosine + RRF
- ``qa``: prompt-injection-safe Q&A prompt builder, citation verification, history compression,
  and rich deterministic offline / refusal fallback.
"""
from __future__ import annotations

from .chunker import Chunk, chunk_document, count_tokens, summarize_document, summarize_section
from .parser import (
    ParsedDocument,
    Section,
    build_statements_summary_doc,
    build_valuation_doc,
    parse_html_filing,
    parse_markdown,
    parse_pdf,
    parse_plain_text,
)
from .qa import (
    Citation,
    build_qa_prompt,
    compress_history,
    extractive_answer,
    refusal_text,
    refusal_with_hints,
    verify_citations,
)
from .retrieval import (
    CandidateChunk,
    RankedChunk,
    bm25_scores,
    cosine,
    embed_hash,
    evidence_sufficient,
    expand_query_tokens,
    hybrid_rank,
    is_overview_query,
    normalize_cjk,
    tokenize,
)

__all__ = [
    "CandidateChunk",
    "Chunk",
    "Citation",
    "ParsedDocument",
    "RankedChunk",
    "Section",
    "bm25_scores",
    "build_qa_prompt",
    "build_statements_summary_doc",
    "build_valuation_doc",
    "chunk_document",
    "compress_history",
    "cosine",
    "count_tokens",
    "embed_hash",
    "evidence_sufficient",
    "expand_query_tokens",
    "extractive_answer",
    "hybrid_rank",
    "is_overview_query",
    "normalize_cjk",
    "parse_html_filing",
    "parse_markdown",
    "parse_pdf",
    "parse_plain_text",
    "refusal_text",
    "refusal_with_hints",
    "summarize_document",
    "summarize_section",
    "tokenize",
    "verify_citations",
]
