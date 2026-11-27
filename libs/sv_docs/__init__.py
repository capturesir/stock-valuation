"""Document library & citation-grounded Q&A core (2.0.0-alpha.5, D13).

Pure Python library (no FastAPI or SQLAlchemy imports):
- ``parser``: SEC HTML filings (10-K/10-Q/8-K), Markdown reports, PDFs and plain text → sections
- ``chunker``: section-aware ~500-token chunking and hierarchical document/section summaries
- ``retrieval``: bilingual (EN + CJK) BM25 + dense vector cosine + RRF hybrid search & reranking
- ``qa``: prompt-injection-safe Q&A prompt builder, citation verification, history compression,
  and deterministic extractive / refusal fallback.
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
    verify_citations,
)
from .retrieval import (
    CandidateChunk,
    RankedChunk,
    bm25_scores,
    cosine,
    embed_hash,
    evidence_sufficient,
    hybrid_rank,
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
    "extractive_answer",
    "hybrid_rank",
    "parse_html_filing",
    "parse_markdown",
    "parse_pdf",
    "parse_plain_text",
    "refusal_text",
    "summarize_document",
    "summarize_section",
    "tokenize",
    "verify_citations",
]
