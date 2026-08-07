"""URL 治理与指纹检测单测。"""
from quickstudy.fingerprint import detect_fingerprint
from quickstudy.urltools import UrlClass, classify_url, normalize_url, same_site
from tests.fixtures import mkdocs_page, sphinx_page


def test_normalize_strips_tracking_and_fragment():
    u = normalize_url("HTTPS://Docs.Example.com/learn/?utm_source=x&id=3#top")
    assert u == "https://docs.example.com/learn"


def test_normalize_keeps_meaningful_query_sorted():
    u = normalize_url("https://a.com/p?b=2&a=1", keep_query=True)
    assert u == "https://a.com/p?a=1&b=2"


def test_classify_skip_blog():
    assert classify_url("https://a.com/blog/launch/") == UrlClass.SKIP


def test_classify_api_reference():
    assert classify_url("https://a.com/api-reference/users/") == UrlClass.API_REFERENCE


def test_classify_unknown_root():
    assert classify_url("https://a.com/") == UrlClass.UNKNOWN


def test_same_site_exact_match_only():
    assert same_site("https://docs.a.com/x", "https://docs.a.com/")
    assert not same_site("https://other.com/x", "https://docs.a.com/")


def test_fingerprint_mkdocs_via_meta():
    fp = detect_fingerprint(mkdocs_page("T", "<p>hi</p>"))
    assert fp["adapter"] == "mkdocs"
    assert fp["signal"] == "meta"


def test_fingerprint_sphinx_via_meta():
    fp = detect_fingerprint(sphinx_page("T", "<p>hi</p>"))
    assert fp["adapter"] == "sphinx"


def test_fingerprint_fallback_generic():
    fp = detect_fingerprint("<html><body><p>plain</p></body></html>")
    assert fp["adapter"] == "generic"
    assert fp["signal"] == "fallback"
