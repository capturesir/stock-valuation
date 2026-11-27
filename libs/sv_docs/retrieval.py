"""Bilingual (EN + CJK) hybrid retrieval: BM25 lexical + dense cosine + RRF reranking."""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Optional, Sequence

STOPWORDS = frozenset({
    "the", "and", "for", "that", "with", "this", "from", "are", "was", "were", "have", "has", "had",
    "its", "their", "our", "your", "about", "into", "over", "under", "between", "through", "during",
    "what", "which", "when", "where", "how", "why", "who", "does", "did", "can", "could", "would",
    "的", "了", "在", "是", "和", "與", "及", "為", "有", "嗎", "呢", "什麼", "甚麼", "哪些", "請問",
})

WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*|[\u4e00-\u9fff]+")


@dataclass(frozen=True)
class CandidateChunk:
    chunk_id: int
    document_id: int
    source_kind: str         # sec_filing | hkex_pdf | valuation | statements | web | upload
    title: str
    doc_date: Optional[str]
    section: str
    page: Optional[int]
    content: str
    token_count: int
    url: Optional[str]
    embedding: Optional[Sequence[float]] = None
    fts_rank: float = 0.0    # optional PostgreSQL ts_rank_cd score


@dataclass(frozen=True)
class RankedChunk:
    chunk: CandidateChunk
    score: float             # calibrated relevance score in [0, 1]
    lexical_score: float
    vector_score: float


def tokenize(text: str) -> list[str]:
    """Extract lowercase EN/alphanumeric tokens + CJK unigrams and bigrams for cross-script matching."""
    out: list[str] = []
    for m in WORD_RE.finditer((text or "").lower()):
        tok = m.group(0)
        if "\u4e00" <= tok[0] <= "\u9fff":
            # Add bigrams (high precision) + unigrams (recall)
            for ch in tok:
                if ch not in STOPWORDS:
                    out.append(ch)
            for i in range(len(tok) - 1):
                bg = tok[i:i + 2]
                if bg not in STOPWORDS:
                    out.append(bg)
        else:
            if len(tok) >= 2 and tok not in STOPWORDS:
                out.append(tok)
    return out


def embed_hash(text: str, dim: int = 128) -> list[float]:
    """Deterministic L2-normalized dense vector via signed feature hashing (offline/CPU fallback)."""
    vec = [0.0] * dim
    toks = tokenize(text)
    if not toks:
        return vec
    counts = Counter(toks)
    for tok, cnt in counts.items():
        h = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "little") % dim
        sign = 1.0 if (h[4] & 1) == 0 else -1.0
        vec[idx] += sign * (1.0 + math.log(cnt))
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [round(v / norm, 6) for v in vec]
    return vec


def cosine(a: Optional[Sequence[float]], b: Optional[Sequence[float]]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return max(0.0, dot / (na * nb))


def bm25_scores(query: str, docs_tokens: list[list[str]], *, k1: float = 1.5, b: float = 0.75) -> list[float]:
    q_toks = tokenize(query)
    if not q_toks or not docs_tokens:
        return [0.0] * len(docs_tokens)
    n_docs = len(docs_tokens)
    avgdl = sum(len(d) for d in docs_tokens) / max(n_docs, 1)
    df: Counter[str] = Counter()
    tfs: list[Counter[str]] = []
    for d in docs_tokens:
        c = Counter(d)
        tfs.append(c)
        for term in set(q_toks):
            if term in c:
                df[term] += 1
    scores: list[float] = []
    q_unique = list(dict.fromkeys(q_toks))
    for d, tf in zip(docs_tokens, tfs):
        dl = len(d)
        s = 0.0
        for term in q_unique:
            f = tf.get(term, 0)
            if f == 0:
                continue
            idf = math.log(1.0 + (n_docs - df[term] + 0.5) / (df[term] + 0.5))
            denom = f + k1 * (1.0 - b + b * dl / max(avgdl, 1.0))
            s += idf * (f * (k1 + 1.0)) / max(denom, 1e-9)
        scores.append(s)
    return scores


def hybrid_rank(
    query: str,
    candidates: list[CandidateChunk],
    *,
    query_vec: Optional[Sequence[float]] = None,
    top_k: int = 10,
    max_tokens: int = 6500,
) -> list[RankedChunk]:
    """Hybrid search (PostgreSQL FTS + BM25 + dense vector cosine via RRF) with strict token budget."""
    if not candidates or not query.strip():
        return []
    q_toks = tokenize(query)
    q_set = set(q_toks)
    q_lower = query.strip().lower()
    q_vec = list(query_vec) if query_vec is not None else embed_hash(query)

    docs_tokens = [tokenize(f"{c.title} {c.section} {c.content}") for c in candidates]
    bm25 = bm25_scores(query, docs_tokens)
    vecs = [
        cosine(q_vec, c.embedding if (c.embedding and len(c.embedding) == len(q_vec)) else embed_hash(f"{c.section} {c.content}", dim=len(q_vec)))
        for c in candidates
    ]

    # Reciprocal Rank Fusion over lexical (BM25 + FTS) and vector rankings
    n = len(candidates)
    lex_combined = [bm25[i] + 2.0 * max(0.0, candidates[i].fts_rank) for i in range(n)]
    lex_order = sorted(range(n), key=lambda i: lex_combined[i], reverse=True)
    vec_order = sorted(range(n), key=lambda i: vecs[i], reverse=True)
    lex_rank = {idx: r + 1 for r, idx in enumerate(lex_order)}
    vec_rank = {idx: r + 1 for r, idx in enumerate(vec_order)}

    ranked_all: list[RankedChunk] = []
    max_bm25 = max(lex_combined) if lex_combined else 0.0
    for i, c in enumerate(candidates):
        doc_set = set(docs_tokens[i])
        coverage = (len(q_set & doc_set) / len(q_set)) if q_set else 0.0
        phrase_boost = 0.15 if (len(q_lower) >= 4 and q_lower in c.content.lower()) else 0.0
        section_boost = 0.08 if (q_set and (q_set & set(tokenize(c.section)))) else 0.0

        rrf = (1.0 / (60 + lex_rank[i])) + (1.0 / (60 + vec_rank[i]))
        rrf_norm = rrf / (2.0 / 61.0)     # normalized to (0, 1]
        lex_norm = (lex_combined[i] / max_bm25) if max_bm25 > 0 else 0.0

        # Calibrated relevance score that drops cleanly near 0 when there is zero overlap
        if coverage == 0.0 and vecs[i] < 0.20 and c.fts_rank <= 0.0:
            score = round(min(0.10, vecs[i] * 0.4), 4)
        else:
            raw = 0.45 * coverage + 0.25 * vecs[i] + 0.15 * lex_norm + 0.15 * rrf_norm + phrase_boost + section_boost
            score = round(min(1.0, raw), 4)
        ranked_all.append(RankedChunk(chunk=c, score=score, lexical_score=round(lex_combined[i], 4), vector_score=round(vecs[i], 4)))

    ranked_all.sort(key=lambda r: (r.score, r.chunk.doc_date or ""), reverse=True)

    selected: list[RankedChunk] = []
    used_tokens = 0
    for item in ranked_all:
        if len(selected) >= top_k:
            break
        tok = max(1, item.chunk.token_count)
        if selected and used_tokens + tok > max_tokens:
            continue
        selected.append(item)
        used_tokens += tok
    return selected


def evidence_sufficient(ranked: list[RankedChunk], query: str, *, min_score: float = 0.22) -> bool:
    """Return True when top retrieved evidence is strong enough to ground an answer."""
    if not ranked or not query.strip():
        return False
    return ranked[0].score >= min_score
