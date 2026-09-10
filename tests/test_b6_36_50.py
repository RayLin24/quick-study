"""Batch 6 — checklist #36–50. Does not redo #1–35."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from utils.auth_errors import (
    AUTH_DEMO,
    AUTH_FORBIDDEN,
    AUTH_LOGIN_INVALID,
    AUTH_READ_ONLY,
    AUTH_UNAUTHORIZED,
    auth_code_catalog,
    demo_forbidden,
)
from utils.backup_crypto import decrypt_bytes, encrypt_bytes
from utils.demo_mode import demo_payload
from utils.disaster_recovery import backup_output, restore_output
from utils.export_epub import build_epub
from utils.export_obsidian import notion_index_markdown
from utils.gallery import gallery_payload
from utils.i18n import t
from utils.i18n_audit import audit_hardcoded_zh, missing_key_report
from utils.jsonl_log import emit_run
from utils.log_context import log_context
from utils.map_inspect import inspect_map_slices
from utils.otel import otel_enabled, span, traces_path
from utils.readonly_token import is_write_path
from utils.repo_map import extract_symbol_locations, first_symbol_line
from utils.source_format import append_blob_line, split_source_ref, unified_source_line
from utils.tutorial_patch import CHANGE_HEADING, patch_chapters_from_pr
from webapp import LOGIN_HTML, create_app

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "export-golden"


def _client(tmp_path: Path, **kwargs):
    output = tmp_path / "output"
    output.mkdir(exist_ok=True)
    return TestClient(
        create_app(
            output_dir=output,
            runner=kwargs.get("runner") or (lambda cmd, on_line, cwd: 0),
            python_exe="python",
        )
    )


def _tutorial(tmp_path: Path, name="Demo"):
    folder = tmp_path / "output" / name
    folder.mkdir(parents=True)
    (folder / "index.md").write_text("# Demo\n\n*source: src/main.py*\n\n入口。\n", encoding="utf-8")
    (folder / "01_a.md").write_text("# Chapter 1: 入口\n\n*source: src/main.py:L3*\n\n讲入口。\n", encoding="utf-8")
    (folder / "meta.json").write_text(json.dumps({"language": "Chinese", "name": name}), encoding="utf-8")
    return folder


def test_36_clone_bloat_docs_and_gitignore():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".playwright-mcp/" in gitignore
    assert ".agent-teams/archive/" in gitignore
    text = (ROOT / "docs" / "clone-bloat.md").read_text(encoding="utf-8")
    assert "sparse-checkout" in text
    assert "docs/PocketFlow" in text


def test_37_auth_codes_and_login_copy(tmp_path: Path, monkeypatch):
    codes = {item["code"] for item in auth_code_catalog()}
    assert codes == {
        AUTH_UNAUTHORIZED,
        AUTH_FORBIDDEN,
        AUTH_READ_ONLY,
        AUTH_DEMO,
        AUTH_LOGIN_INVALID,
    }
    assert demo_payload()["code"] == AUTH_DEMO
    assert "AUTH_READ_ONLY" in LOGIN_HTML
    assert "AUTH_DEMO" in LOGIN_HTML
    assert "Bearer" in LOGIN_HTML
    monkeypatch.setenv("QUICK_STUDY_DEMO", "1")
    client = _client(tmp_path)
    res = client.post("/api/jobs", json={"source_type": "repo", "repo_url": "https://github.com/o/r"})
    assert res.status_code == 403
    assert res.json()["code"] == AUTH_DEMO
    assert client.get("/api/auth/codes").json()["items"]


def test_38_i18n_missing_key_report():
    assert t("zh", "skip_main") == "跳到主内容"
    assert t("en", "advanced") == "Advanced options"
    report = audit_hardcoded_zh(ROOT)
    assert report["scanned_files"] >= 3
    assert "missing" in report
    text = missing_key_report(ROOT)
    assert "missing=" in text


def test_39_a11y_skip_ask_mermaid(tmp_path: Path):
    _tutorial(tmp_path)
    client = _client(tmp_path)
    home = client.get("/").text
    assert 'class="skip-link"' in home
    assert 'href="#generate-title"' in home
    page = client.get("/t/Demo").text
    assert 'href="#ask-title"' in page
    assert 'aria-live="polite"' in page
    assert "mermaid-svg-focus" in page
    assert 'href="#article-main"' in page


def test_40_gallery_seeded_no_regenerate(tmp_path: Path):
    payload = gallery_payload(ROOT)
    assert payload["regenerate_required"] is False
    assert 3 <= len(payload["items"]) <= 5
    names = {item["name"] for item in payload["items"]}
    assert "PocketFlow" in names
    client = _client(tmp_path)
    res = client.get("/api/gallery")
    assert res.status_code == 200
    html = client.get("/gallery").text
    assert "PocketFlow" in html
    assert (ROOT / "gallery" / "manifest.json").is_file()
    assert (ROOT / "gallery" / ".nojekyll").exists()


def test_41_map_mode_inspect_symbols():
    report = inspect_map_slices(
        {
            "src/app.py": "class App:\n    def run(self):\n        return 1\n",
            "src/util.py": "def helper():\n    pass\n",
        }
    )
    assert report["file_count"] == 2
    assert report["symbol_count"] >= 2
    assert any(s["name"] == "App" for item in report["slices"] for s in item["symbols"])
    assert "--- File: src/app.py ---" in report["prompt"]


def test_42_otel_jsonl_correlate_keys(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OTEL_TRACES", "1")
    assert otel_enabled()
    with log_context(job_id="job-1", tutorial="Demo", stage="write"):
        rec = emit_run(tmp_path / "output", "step", extra="x")
        with span("write", output_dir=tmp_path / "output"):
            pass
    assert rec["job_id"] == "job-1"
    assert rec["tutorial"] == "Demo"
    assert rec["stage"] == "write"
    line = json.loads((tmp_path / "output" / "run.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert set(line) >= {"job_id", "tutorial", "stage", "event"}
    trace = json.loads(traces_path(tmp_path / "output").read_text(encoding="utf-8").splitlines()[0])
    assert trace["job_id"] == "job-1"
    assert trace["tutorial"] == "Demo"
    assert trace["stage"] == "write"


def test_43_pr_patch_writes_old_chapters(tmp_path: Path):
    folder = _tutorial(tmp_path)
    diff = """diff --git a/src/main.py b/src/main.py
--- a/src/main.py
+++ b/src/main.py
@@ -1,2 +1,3 @@
+print('hi')
"""
    rec = patch_chapters_from_pr(folder, diff, title="Add hi", explanation="入口多了一行打印。")
    assert rec["patched"]
    text = (folder / rec["patched"][0]).read_text(encoding="utf-8")
    assert CHANGE_HEADING in text
    assert "入口多了一行打印" in text
    client = _client(tmp_path)
    res = client.post(
        "/api/tutorials/Demo/patch-from-pr",
        json={"diff": diff, "title": "Add hi", "explanation": "又一次。"},
    )
    assert res.status_code == 200
    assert res.json()["patched"]


def test_44_epub_notion_golden_fixtures():
    assert GOLDEN.is_dir()
    chapters = list(GOLDEN.glob("*.md"))
    assert len(chapters) >= 3
    notion = notion_index_markdown(GOLDEN)
    assert "```mermaid" in notion
    assert "Chapter 1: Fetch" in notion or "01_fetch" in notion
    assert "Identify" in notion
    epub = build_epub(GOLDEN)
    assert epub[:4] == b"PK\x03\x04"
    with zipfile.ZipFile(__import__("io").BytesIO(epub)) as zf:
        names = zf.namelist()
        assert any(n.endswith(".xhtml") for n in names)
        bodies = "\n".join(zf.read(n).decode("utf-8", errors="replace") for n in names if n.endswith(".xhtml"))
        assert "mermaid" in bodies.lower()
        assert bodies.count("<html") >= 3


def test_45_readonly_matrix_docs_and_ops_write():
    text = (ROOT / "docs" / "readonly-permission-matrix.md").read_text(encoding="utf-8")
    assert "MCP Ask" in text or "Ask" in text
    assert "备份" in text
    assert "写" in text
    assert is_write_path("POST", "/api/jobs")
    assert is_write_path("POST", "/api/ops/backup")
    assert is_write_path("DELETE", "/api/tutorials/Demo")
    assert not is_write_path("POST", "/api/tutorials/Demo/ask")


def test_46_vscode_extension_scaffold():
    pkg = json.loads((ROOT / "extensions" / "vscode" / "package.json").read_text(encoding="utf-8"))
    assert pkg["name"] == "quick-study"
    assert "quickStudy.tutorials" in json.dumps(pkg)
    src = (ROOT / "extensions" / "vscode" / "src" / "extension.js").read_text(encoding="utf-8")
    assert "/api/tutorials" in src
    assert "/ask" in src
    assert (ROOT / "extensions" / "vscode" / "README.md").is_file()


def test_47_backup_encrypt_restore_drill(tmp_path: Path):
    out = tmp_path / "output"
    demo = out / "Demo"
    demo.mkdir(parents=True)
    (demo / "index.md").write_text("# Demo\n", encoding="utf-8")
    rec = backup_output(out, tmp_path / "bak", passphrase="drill-only")
    assert rec["encrypted"] is True
    assert Path(rec["archive"]).suffix == ".qs1"
    dest = tmp_path / "restored"
    restore_output(Path(rec["archive"]), dest, passphrase="drill-only")
    assert (dest / "Demo" / "index.md").is_file()
    try:
        restore_output(Path(rec["archive"]), tmp_path / "bad", passphrase="wrong")
        raise AssertionError("bad passphrase should fail")
    except ValueError:
        pass
    blob = encrypt_bytes(b"hello", "x")
    assert decrypt_bytes(blob, "x") == b"hello"
    docs = (ROOT / "docs" / "backup-restore-drill.md").read_text(encoding="utf-8")
    assert "llm_cache" in docs
    assert ".env" in docs


def test_48_mobile_form_collapses_advanced(tmp_path: Path):
    html = _client(tmp_path).get("/").text
    assert 'id="advanced-options"' in html
    assert "高级选项" in html
    css = (ROOT / "web" / "static" / "app.css").read_text(encoding="utf-8")
    assert "advanced-options" in css
    assert "max-width: 800px" in css


def test_49_optional_symbol_source_lines(monkeypatch):
    src = "class Foo:\n    def bar(self):\n        pass\n"
    locs = extract_symbol_locations("pkg/mod.py", src)
    assert locs[0]["name"] == "Foo"
    assert locs[0]["line"] == 1
    assert first_symbol_line("pkg/mod.py", src) == 1
    assert unified_source_line("a.py") == "*source: a.py*"
    assert unified_source_line("a.py", 12) == "*source: a.py:L12*"
    path, line = split_source_ref("src/app.py:L4")
    assert path == "src/app.py" and line == 4
    assert append_blob_line("https://github.com/o/r/blob/HEAD/a.py", 9).endswith("#L9")
    monkeypatch.setenv("SYMBOL_SOURCE_LINES", "1")
    from nodes import clip_snippets

    text = clip_snippets({"pkg/mod.py": src}, max_total_chars=2000, max_file_chars=500)
    assert "*source: pkg/mod.py:L1*" in text


def test_50_competitor_table_in_readme():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "DeepWiki" in readme
    assert "RepoWiki" in readme
    assert "self-hosted" in readme.lower() or "Self-hosted" in readme or "自托管" in readme
    assert (ROOT / "docs" / "competitors.md").is_file()
    assert (ROOT / "docs" / "merge-after-10-14.md").is_file()


def test_36_50_map_inspect_and_i18n_http(tmp_path: Path):
    client = _client(tmp_path)
    res = client.post("/api/map-inspect", json={"files": {"a.py": "def foo():\n    return 1\n"}})
    assert res.status_code == 200
    assert res.json()["symbol_count"] >= 1
    assert client.get("/api/i18n/audit").status_code == 200
