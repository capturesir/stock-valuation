"""Bilingual (EN + Trad/Simp CJK) hybrid retrieval: BM25 + cross-lingual financial expansion + dense cosine + RRF."""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Optional, Sequence

# Canonicalize high-frequency Traditional Chinese financial/research characters to Simplified
# so Traditional and Simplified documents/queries match identically without external dependencies.
_TRAD_TO_SIMP_PAIRS = (
    "營营業业財财報报風风險险資资產产負负債债權权淨净潤润現现開开價价長长場场競竞爭争優优勢势"
    "應应鏈链戶户數数據据務务銷销發发戰战並并購购匯汇稅税損损虧亏舊旧攤摊無无譽誉貨货賬账醫医"
    "藥药電电訊讯雲云計计導导體体軟软網网絡络銀银環环監监規规訴诉訟讼專专費费層层會会獨独審审"
    "總总結结問问題题響响預预測测標标動动機机實实際际歷历過过来國国亞亚歐欧陸陆經经濟济觀观週周"
    "訂订單单積积壓压邊边槓杠桿杆償偿質质關关係系說说點点項项類类種种與与為为嗎吗麼么這这個个"
    "對对於于從从將将給给讓让還还進进運运轉转變变調调減减擴扩繼继續续維维持穩稳顯显達达億亿萬万"
    "塊块幣币點点頭头號号視视頻频遊游戲戏廣广東东買买賣卖漲涨跌跌擔担憂忧壞坏帳账撥拨備备儲储"
    "險险賠赔繳缴費费貸贷儲储蓄蓄廠厂設设備备製制庫库銷销購购併并組组織织員员職职薪薪獎奖勵励"
    "績绩効效評评級级預測测預预估估隱隐憂忧挑挑战戰機机遇遇護护城城河河驅驱動动因因細细節节"
    "說说明明摘摘要要綜综合合覽览概概况況"
)
_T2S_MAP: dict[int, int] = {}
for _i in range(0, len(_TRAD_TO_SIMP_PAIRS) - 1, 2):
    _t, _s = _TRAD_TO_SIMP_PAIRS[_i], _TRAD_TO_SIMP_PAIRS[_i + 1]
    if _t != _s:
        _T2S_MAP[ord(_t)] = ord(_s)


def normalize_cjk(text: str) -> str:
    """Normalize Traditional Chinese characters to canonical Simplified form and lowercase."""
    return (text or "").translate(_T2S_MAP).lower()


STOPWORDS = frozenset({
    # English conversational / structural words
    "the", "and", "for", "that", "with", "this", "from", "are", "was", "were", "have", "has", "had",
    "its", "their", "our", "your", "about", "into", "over", "under", "between", "through", "during",
    "what", "which", "when", "where", "how", "why", "who", "does", "did", "can", "could", "would",
    "should", "will", "please", "tell", "explain", "describe", "give", "show", "company", "stock",
    "share", "shares", "firm", "corporation", "inc", "ltd", "corp", "any", "some", "much", "many",
    "very", "really", "currently", "now", "here", "there", "more", "most", "also", "just", "like",
    # Canonical Simplified Chinese conversational / structural words (Traditional auto-maps via normalize_cjk)
    "的", "了", "在", "是", "和", "与", "及", "为", "有", "吗", "呢", "啊", "吧", "嘛", "呀", "么",
    "什", "啥", "哪", "些", "请", "问", "这", "那", "个", "家", "只", "支", "公", "司", "股", "票",
    "帮", "我", "你", "他", "它", "们", "告", "诉", "解", "释", "看", "下", "如", "何", "怎", "样",
    "是", "否", "能", "可", "以", "多", "少", "目", "前", "现", "情", "况", "到", "底", "究", "竟",
    "什么", "哪些", "请问", "这家", "这个", "公司", "股票", "告诉", "解释", "帮我", "看看", "一下",
    "如何", "怎么", "怎样", "是否", "能否", "可以", "多少", "目前", "现在", "情况", "到底", "究竟",
    "有没有", "还有", "以及", "关于", "一下", "主要", "具体", "比较",
})

WORD_RE = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*|[\u4e00-\u9fff]+")

# Cross-lingual financial & equity-research concept clusters (canonical Simplified ZH <-> EN).
# Triggered ONLY when the query contains one of the cluster's triggers; adds counterpart terms
# so Chinese queries match English 10-K/HKEX filings and vice versa without causing false positives.
CONCEPT_CLUSTERS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("风险", "隐患", "担忧", "挑战", "威胁", "缺点", "利空", "红队", "警示"),
     ("risk", "risks", "factors", "uncertainty", "adverse", "threat", "litigation", "regulatory", "red", "team")),
    (("出口", "管制", "制裁", "关税", "地缘", "政策", "监管", "合规", "反垄断"),
     ("export", "controls", "licensing", "tariff", "sanction", "regulation", "regulatory", "antitrust", "compliance", "government")),
    (("业务", "主营", "产品", "服务", "模式", "商业", "做什么", "经营", "生态"),
     ("business", "operations", "products", "services", "platform", "model", "segment", "segments", "ecosystem", "solutions")),
    (("竞争", "护城河", "优势", "壁垒", "市占", "份额", "对手", "领先"),
     ("competition", "competitive", "competitors", "moat", "advantage", "market", "share", "leadership", "differentiation")),
    (("供应链", "供应商", "代工", "晶圆", "产能", "制造", "集中度", "客户"),
     ("supply", "chain", "supplier", "foundry", "tsmc", "manufacturing", "capacity", "concentration", "customer", "customers")),
    (("营收", "收入", "销售", "营业额", "业绩", "顶线"),
     ("revenue", "revenues", "sales", "turnover", "top-line", "performance")),
    (("毛利", "毛利率", "营业利润", "利润率", "净利率", "盈利能力", "利润"),
     ("gross", "margin", "operating", "profit", "profitability", "ebit", "ebitda", "net", "income", "earnings")),
    (("净利", "净利润", "盈利", "收益", "每股盈利", "每股收益", "eps", "亏损"),
     ("net", "income", "earnings", "profit", "eps", "diluted", "loss", "attributable")),
    (("现金流", "自由现金流", "经营现金", "资本开支", "capex", "fcf", "cfo"),
     ("cash", "flow", "operating", "free", "fcf", "capex", "capital", "expenditure", "cfo")),
    (("资产", "负债", "债务", "借款", "杠杆", "现金", "流动性", "偿债", "资产负债表"),
     ("assets", "liabilities", "debt", "borrowings", "leverage", "cash", "liquidity", "balance", "sheet", "equity")),
    (("股息", "分红", "派息", "股息率", "回购", "股东回报"),
     ("dividend", "dividends", "payout", "yield", "buyback", "repurchase", "repurchases", "shareholder", "return")),
    (("研发", "技术", "创新", "专利", "芯片", "算力", "人工智能", "数据中心", "云"),
     ("research", "development", "technology", "innovation", "gpu", "ai", "data", "center", "cloud", "cuda", "blackwell", "hopper")),
    (("分部", "板块", "地区", "区域", "市场", "中国", "美国", "海外", "收入构成"),
     ("segment", "segments", "geographic", "region", "china", "americas", "europe", "asia", "mix", "breakdown")),
    (("展望", "指引", "未来", "增长", "驱动", "趋势", "战略", "扩张", "计划"),
     ("outlook", "guidance", "forecast", "growth", "driver", "drivers", "strategy", "expansion", "future", "trend")),
    (("估值", "理论价", "目标价", "内在价值", "折现", "贴现", "贵不贵", "便宜", "合理", "值得"),
     ("valuation", "fair", "value", "intrinsic", "dcf", "wacc", "terminal", "scenario", "upside", "downside", "multiple")),
    (("市盈率", "市净率", "本益比", "倍数", "同业", "可比"),
     ("pe", "pb", "multiple", "multiples", "peer", "peers", "comparable", "ev/ebitda", "ev/sales", "relative")),
    (("杜邦", "roe", "roa", "roic", "周转", "质量", "应计", "盈利质量", "基本面", "财务"),
     ("dupont", "roe", "roa", "roic", "turnover", "quality", "accrual", "sloan", "financial", "statements", "ratios")),
    (("管理层", "治理", "董事会", "股权", "高管", "薪酬", "回购"),
     ("management", "governance", "board", "executive", "ceo", "compensation", "ownership", "directors")),
    (("新闻", "公告", "动态", "消息", "最新", "近期", "催化剂", "事件", "并购", "收购"),
     ("news", "announcement", "catalyst", "event", "recent", "latest", "acquisition", "merger", "release")),
    (("银行", "贷款", "存款", "净息差", "拨备", "不良"),
     ("bank", "loans", "deposits", "net", "interest", "provision", "credit", "losses", "nim")),
    (("保险", "保费", "赔付", "准备金", "综合成本"),
     ("insurance", "premiums", "claims", "reserves", "policy", "benefits", "underwriting")),
    (("用户", "月活", "广告", "游戏", "订阅", "会员", "流量", "微信"),
     ("users", "mau", "dau", "advertising", "ads", "gaming", "subscription", "members", "wechat", "traffic")),
)

OVERVIEW_RE = re.compile(
    r"(?:总结|概括|概览|概况|简介|介绍|分析一下|全面分析|基本面|怎么样|值得买|值得投资|可以买吗|投资价值|投资逻辑|核心看点|亮点|"
    r"overview|summary|summarize|investment\s+thesis|worth\s+buying|how\s+is\s+this|tell\s+me\s+about\s+this\s+(?:company|stock)|"
    r"key\s+(?:takeaways|highlights|points)|fundamentals)",
    re.I,
)


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


def is_overview_query(query: str) -> bool:
    """Return True when the user asks a broad company overview/summary/thesis question."""
    return bool(OVERVIEW_RE.search(normalize_cjk(query)))


def tokenize(text: str) -> list[str]:
    """Extract lowercase EN/alphanumeric tokens + canonicalized CJK unigrams & bigrams."""
    out: list[str] = []
    norm = normalize_cjk(text)
    for m in WORD_RE.finditer(norm):
        tok = m.group(0)
        if "\u4e00" <= tok[0] <= "\u9fff":
            # Add bigrams (high precision) + unigrams (recall)
            for i in range(len(tok) - 1):
                bg = tok[i:i + 2]
                if bg not in STOPWORDS:
                    out.append(bg)
            for ch in tok:
                if ch not in STOPWORDS:
                    out.append(ch)
        else:
            if len(tok) >= 2 and tok not in STOPWORDS:
                out.append(tok)
    return out


def expand_query_tokens(query: str) -> tuple[list[str], list[str]]:
    """Return ``(direct_tokens, expanded_cross_lingual_tokens)`` for a bilingual financial query."""
    norm = normalize_cjk(query)
    direct = tokenize(norm)
    direct_set = set(direct)
    expanded: list[str] = []
    seen: set[str] = set(direct_set)

    for zh_terms, en_terms in CONCEPT_CLUSTERS:
        zh_hit = any(z in norm for z in zh_terms)
        en_hit = any((e in direct_set or (len(e) >= 4 and e in norm)) for e in en_terms)
        if not (zh_hit or en_hit):
            continue
        targets = en_terms + zh_terms if zh_hit else zh_terms + en_terms
        for term in targets:
            for t in tokenize(term):
                if t not in seen:
                    seen.add(t)
                    expanded.append(t)
    return direct, expanded


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


def bm25_scores(
    query: str,
    docs_tokens: list[list[str]],
    *,
    extra_terms: Optional[Sequence[str]] = None,
    k1: float = 1.5,
    b: float = 0.75,
) -> list[float]:
    q_direct = tokenize(query)
    weighted_terms: dict[str, float] = {}
    for t in q_direct:
        weighted_terms[t] = 1.0
    for t in extra_terms or ():
        if t not in weighted_terms:
            weighted_terms[t] = 0.65
    if not weighted_terms or not docs_tokens:
        return [0.0] * len(docs_tokens)
    n_docs = len(docs_tokens)
    avgdl = sum(len(d) for d in docs_tokens) / max(n_docs, 1)
    df: Counter[str] = Counter()
    tfs: list[Counter[str]] = []
    q_terms = list(weighted_terms.keys())
    q_term_set = set(q_terms)
    for d in docs_tokens:
        c = Counter(d)
        tfs.append(c)
        for term in q_term_set:
            if term in c:
                df[term] += 1
    scores: list[float] = []
    for d, tf in zip(docs_tokens, tfs):
        dl = len(d)
        s = 0.0
        for term in q_terms:
            f = tf.get(term, 0)
            if f == 0:
                continue
            idf = math.log(1.0 + (n_docs - df[term] + 0.5) / (df[term] + 0.5))
            denom = f + k1 * (1.0 - b + b * dl / max(avgdl, 1.0))
            s += weighted_terms[term] * idf * (f * (k1 + 1.0)) / max(denom, 1e-9)
        scores.append(s)
    return scores


_SOURCE_OVERVIEW_PRIORITY = {
    "valuation": 0.42,
    "statements": 0.38,
    "hkex_pdf": 0.36,
    "sec_filing": 0.36,
    "upload": 0.35,
    "web": 0.30,
}


def hybrid_rank(
    query: str,
    candidates: list[CandidateChunk],
    *,
    query_vec: Optional[Sequence[float]] = None,
    top_k: int = 10,
    max_tokens: int = 6500,
) -> list[RankedChunk]:
    """Hybrid search (PostgreSQL FTS + cross-lingual BM25 + dense cosine via RRF) with strict token budget."""
    if not candidates or not query.strip():
        return []
    q_direct, q_expanded = expand_query_tokens(query)
    q_set = set(q_direct)
    q_exp_set = set(q_expanded)
    q_all_set = q_set | q_exp_set
    q_norm = normalize_cjk(query).strip()
    overview = is_overview_query(query)

    # Combine query + cross-lingual expanded text for dense cosine matching
    vec_text = query if not q_expanded else f"{query} {' '.join(q_expanded)}"
    q_vec = list(query_vec) if query_vec is not None else embed_hash(vec_text)

    docs_tokens = [tokenize(f"{c.title} {c.section} {c.content}") for c in candidates]
    bm25 = bm25_scores(query, docs_tokens, extra_terms=q_expanded)
    vecs = [
        cosine(
            q_vec,
            embed_hash(f"{c.title} {c.section} {c.content}", dim=len(q_vec))
            if (q_expanded or not c.embedding or len(c.embedding) != len(q_vec))
            else c.embedding,
        )
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
        direct_matched = q_set & doc_set
        exp_matched = q_exp_set & doc_set
        direct_hits = len(direct_matched)
        exp_hits = len(exp_matched)
        # Multi-character matches (EN words or CJK bigrams) carry real semantics; stray single CJK chars are noisy
        strong_hits = sum(1 for t in (direct_matched | exp_matched) if len(t) >= 2)
        direct_cov = (direct_hits / len(q_set)) if q_set else 0.0
        # Concept coverage rewards matching >=2 cross-lingual concept terms strongly without requiring all synonyms
        exp_cov = min(1.0, exp_hits / max(2.0, min(len(q_exp_set), 5.0))) if q_exp_set else 0.0
        coverage = max(direct_cov, 0.85 * exp_cov)

        content_norm = normalize_cjk(c.content)
        phrase_boost = 0.15 if (len(q_norm) >= 4 and q_norm in content_norm) else 0.0
        sec_tokens = set(tokenize(c.section))
        section_boost = 0.10 if (q_all_set and any(len(t) >= 2 for t in (q_all_set & sec_tokens))) else 0.0

        rrf = (1.0 / (60 + lex_rank[i])) + (1.0 / (60 + vec_rank[i]))
        rrf_norm = rrf / (2.0 / 61.0)     # normalized to (0, 1]
        lex_norm = (lex_combined[i] / max_bm25) if max_bm25 > 0 else 0.0

        # Calibrated relevance score that drops cleanly near 0 when there is no meaningful semantic/lexical match
        if (coverage == 0.0 and vecs[i] < 0.20 and c.fts_rank <= 0.0) or (strong_hits == 0 and direct_cov < 0.35 and not overview):
            if overview:
                # Broad company overview/summary queries should surface representative library sections
                base = _SOURCE_OVERVIEW_PRIORITY.get(c.source_kind, 0.30)
                if re.search(r"business|summary|overview|md&a|risk|估值|摘要|業務|业务|風險|风险", c.section or "", re.I):
                    base += 0.08
                score = round(min(0.60, base), 4)
            else:
                score = round(min(0.12, vecs[i] * 0.4 + direct_cov * 0.2), 4)
        else:
            rank_gate = min(1.0, (coverage + vecs[i]) / 0.35)
            raw = 0.45 * coverage + 0.25 * vecs[i] + rank_gate * (0.15 * lex_norm + 0.15 * rrf_norm) + phrase_boost + section_boost
            if overview:
                raw = max(raw, _SOURCE_OVERVIEW_PRIORITY.get(c.source_kind, 0.32))
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
