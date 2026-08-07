"""解析层单测：结构化抽取、Markdown 转换、simhash、license。"""
from quickstudy.adapters import get_adapter
from quickstudy.manifest import detect_license
from quickstudy.parse.page import detect_lang, parse_page
from quickstudy.parse.simhash import hamming, simhash
from tests.fixtures import DOC_BODY, mkdocs_page


def _parse(body: str = DOC_BODY):
    adapter = get_adapter("mkdocs")
    html = mkdocs_page("First Steps", body)
    return parse_page("abc123", "https://docs.example.com/tutorial/first-steps/",
                      html, adapter, "https://docs.example.com/")


def test_parse_extracts_headings_code_tables_links():
    md, doc = _parse()
    assert doc["ok"] and doc["title"] == "First Steps"
    assert doc["adapter"] == "mkdocs"

    code = doc["code_blocks"]
    assert len(code) == 1
    assert code[0]["language"] == "python"
    assert "FastAPI()" in code[0]["code"]

    tables = doc["tables"]
    assert len(tables) == 1 and tables[0]["has_header"]
    assert tables[0]["rows"][0] == ["Parameter", "Default", "Description"]
    assert "| port | 8000 |" in tables[0]["markdown"]

    targets = [l["target"] for l in doc["links"]]
    assert "https://docs.example.com/tutorial/path-params" in targets
    # 外链不进入引用边
    assert not any("external.example.com" in t for t in targets)

    assert doc["images"][0]["alt"] == "request lifecycle"
    assert doc["lang"] == "en"


def test_markdown_has_code_fence_and_table():
    md, _ = _parse()
    assert "```python" in md
    assert "| Parameter | Default |" in md
    assert "md-footer" not in md  # 噪音已剔除


def test_noise_stripped_from_structure():
    _, doc = _parse()
    all_text = str(doc["headings"]) + str(doc["links"])
    assert "header noise" not in all_text


def test_simhash_near_duplicate():
    a = simhash("fastapi is a modern web framework for building APIs with python")
    b = simhash("fastapi is a modern web framework for building APIs with python!")
    c = simhash("completely unrelated content about database indexes and queries")
    assert hamming(a, b) <= 3
    assert hamming(a, c) > 10


def test_detect_lang():
    assert detect_lang("这是中文内容，包含大量汉字。" * 5) == "zh"
    assert detect_lang("This is English documentation content.") == "en"


def test_license_detected_from_footer():
    lic = detect_license(mkdocs_page("T", "<p>x</p>"))
    assert lic["license"].startswith("CC-BY")
    assert lic["risk"] == "low"


def test_license_undetected():
    lic = detect_license("<html><body><p>no license info</p></body></html>")
    assert lic["license"] in ("undetected", "unknown")
