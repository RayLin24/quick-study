"""#7 BM25 chapter retrieval vs token-hit score_chapter (mixed CN/EN)."""

from pathlib import Path

from utils.ask_retrieve import Bm25Index, bm25_chapter_scores, tokenize
from utils.ask_tutorial import ChapterDoc, score_chapter, select_ask_chapters


def _mixed_corpus() -> list[ChapterDoc]:
    # Token-hit bait: query contains "middleware", which only appears in this path.
    decoy = ChapterDoc(
        filename="01_overview.md",
        title="Overview",
        text="This chapter lists the request package layout only. No JWT here.",
        sources=["src/middleware.py"],
        chars=90,
    )
    relevant = ChapterDoc(
        filename="02_security.md",
        title="Security Layer",
        text=(
            "本章讲 JWT authentication 鉴权。请求进入后由鉴权中间件校验 token。"
            "authentication 负责 鉴权 与 JWT 签名，并拒绝过期凭证。"
        )
        * 3,
        sources=["src/auth.py"],
        chars=240,
    )
    other = ChapterDoc(
        filename="03_cache.md",
        title="Cache 缓存",
        text="LRU cache eviction and TTL only.",
        sources=["src/cache.py"],
        chars=40,
    )
    return [decoy, relevant, other]


MIXED_QUERY = "JWT authentication 鉴权 middleware 怎么校验"


def test_bm25_beats_token_hit_on_mixed_cn_en():
    chapters = _mixed_corpus()
    query = MIXED_QUERY

    token_ranked = sorted(chapters, key=lambda ch: score_chapter(ch, query), reverse=True)
    bm25_scores = bm25_chapter_scores(chapters, query)
    bm25_ranked = [ch for ch, _ in sorted(zip(chapters, bm25_scores), key=lambda item: item[1], reverse=True)]

    assert score_chapter(token_ranked[0], query) > score_chapter(chapters[1], query)
    assert token_ranked[0].filename == "01_overview.md"
    assert bm25_ranked[0].filename == "02_security.md"
    assert bm25_scores[1] > bm25_scores[0]
    assert bm25_scores[1] > bm25_scores[2]


def test_bm25_hits_glued_cjk_where_token_hit_misses():
    entry = ChapterDoc(
        "01_entry.md",
        "启动",
        "进程启动后加载配置。",
        ["src/main.py"],
        20,
    )
    auth = ChapterDoc(
        "02_auth.md",
        "Security",
        "用户登录后要做鉴权，校验 token 是否过期。",
        ["src/auth.py"],
        30,
    )
    glued = "鉴权怎么校验token"
    assert score_chapter(entry, glued) == 0
    assert score_chapter(auth, glued) == 0
    tokens = tokenize(glued)
    assert "鉴权" in tokens
    assert "校验" in tokens
    scores = bm25_chapter_scores([entry, auth], glued)
    assert scores[1] > scores[0]


def test_select_ask_chapters_uses_bm25_not_token_hit_bait():
    chapters = _mixed_corpus()
    selected, routed = select_ask_chapters(chapters, MIXED_QUERY, top_k=1, max_chars=80)
    assert routed is True
    assert selected[0].filename == "02_security.md"


def test_bm25_index_empty_is_safe():
    index = Bm25Index([])
    assert index.scores("anything") == []


def test_tokenize_splits_paths_and_cjk_bigrams():
    tokens = tokenize("src/auth.py 鉴权入口")
    assert "src/auth.py" in tokens
    assert "auth" in tokens
    assert "鉴权" in tokens
    assert "入口" in tokens
