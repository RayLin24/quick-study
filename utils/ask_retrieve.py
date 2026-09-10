"""Chapter retrieval for Ask: BM25 over mixed CN/EN tokens. No vector DB."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Iterable, Sequence

# English / identifiers stay whole; CJK is unigram+bigram so mixed queries hit.
IDENT_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_\./-]*")
CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
SPLIT_IDENT_RE = re.compile(r"[./\\_-]+")

# Baseline token-hit tokenizer (kept for A/B evidence against score_chapter).
TOKEN_HIT_RE = re.compile(r"[A-Za-z0-9_\./-]+|[\u4e00-\u9fff]{1,}")

K1 = 1.5
B = 0.75


def tokenize(text: str) -> list[str]:
    """Mixed CN/EN tokens: identifiers + CJK unigrams/bigrams + path parts."""
    raw = text or ""
    tokens: list[str] = []
    for match in IDENT_RE.finditer(raw):
        word = match.group(0).lower()
        tokens.append(word)
        parts = [p.lower() for p in SPLIT_IDENT_RE.split(word) if p]
        tokens.extend(part for part in parts if part != word)
    for match in CJK_RE.finditer(raw):
        run = match.group(0)
        tokens.extend(run)
        tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def token_hit_tokens(text: str) -> set[str]:
    return {part.lower() for part in TOKEN_HIT_RE.findall(text or "") if part.strip()}


class Bm25Index:
    """In-memory BM25Okapi. Built once per Ask from chapter texts."""

    def __init__(self, documents: Sequence[str], *, k1: float = K1, b: float = B):
        self.k1 = k1
        self.b = b
        self.docs = [tokenize(doc) for doc in documents]
        self.n = len(self.docs)
        self.tf = [Counter(doc) for doc in self.docs]
        self.dl = [len(doc) for doc in self.docs]
        self.avgdl = (sum(self.dl) / self.n) if self.n else 0.0
        df: Counter[str] = Counter()
        for counts in self.tf:
            df.update(counts.keys())
        self.df = df
        self.idf = {
            term: math.log(1.0 + (self.n - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def score(self, doc_index: int, query_tokens: Iterable[str]) -> float:
        if not self.n or doc_index < 0 or doc_index >= self.n:
            return 0.0
        counts = self.tf[doc_index]
        dl = self.dl[doc_index]
        denom_norm = self.k1 * (1.0 - self.b + self.b * dl / self.avgdl) if self.avgdl else self.k1
        total = 0.0
        seen: set[str] = set()
        for tok in query_tokens:
            if tok in seen:
                continue
            seen.add(tok)
            freq = counts.get(tok, 0)
            if not freq:
                continue
            idf = self.idf.get(tok, math.log(1.0 + (self.n + 0.5) / 0.5))
            total += idf * (freq * (self.k1 + 1.0)) / (freq + denom_norm)
        return total

    def scores(self, query: str) -> list[float]:
        q_tokens = tokenize(query)
        return [self.score(i, q_tokens) for i in range(self.n)]


def chapter_search_text(chapter, *, title_repeat: int = 3) -> str:
    """Title/path get extra weight; body is included (token-hit score_chapter skips body)."""
    title = getattr(chapter, "title", "") or ""
    filename = getattr(chapter, "filename", "") or ""
    sources = " ".join(getattr(chapter, "sources", None) or [])
    body = getattr(chapter, "text", "") or ""
    head = " ".join(filter(None, [title] * max(1, title_repeat) + [filename, sources]))
    return f"{head}\n{body}"


def bm25_chapter_scores(chapters: Sequence, question: str) -> list[float]:
    docs = [chapter_search_text(ch) for ch in chapters]
    return Bm25Index(docs).scores(question)


def rank_chapters(chapters: Sequence, question: str) -> list[tuple[object, float]]:
    scores = bm25_chapter_scores(chapters, question)
    ranked = sorted(
        zip(chapters, scores),
        key=lambda item: item[1],
        reverse=True,
    )
    return list(ranked)
