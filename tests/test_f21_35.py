import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from utils.audit_log import actor_id, append_audit, list_audit
from utils.demo_mode import demo_enabled, is_generate_path
from utils.export_epub import build_epub
from utils.export_obsidian import build_obsidian_zip, notion_index_markdown
from utils.feed import atom_feed, rss_feed
from utils.import_pack import import_tutorial_zip
from utils.ip_rate_limit import BucketLimiter, RateLimited, bucket_key
from utils.jsonl_log import emit_run, tail_run
from utils.key_rotation import detect_key_files
from utils.otel import otel_enabled, span, traces_path
from utils.tutorial_search import search_tutorials
from utils.tutorial_tags import list_groups, set_tags
from utils.webhook import dingtalk_body, feishu_body
from webapp import create_app


def _tutorial(tmp_path: Path, name="Demo"):
    folder = tmp_path / "output" / name
    folder.mkdir(parents=True)
    (folder / "index.md").write_text("# Demo\n\n入口在 main。\n", encoding="utf-8")
    (folder / "01_a.md").write_text("# Chapter 1: 入口\n\n*source: src/main.py*\n\n讲入口。\n", encoding="utf-8")
    (folder / "meta.json").write_text(json.dumps({"language": "Chinese", "name": name}), encoding="utf-8")
    return folder


def _client(tmp_path: Path, **kwargs):
    output = tmp_path / "output"
    output.mkdir(exist_ok=True)
    return TestClient(
        create_app(output_dir=output, runner=kwargs.get("runner") or (lambda cmd, on_line, cwd: 0), python_exe="python")
    )


def test_21_tutorial_search(tmp_path: Path):
    _tutorial(tmp_path)
    hits = search_tutorials(tmp_path / "output", "入口")
    assert hits and hits[0]["tutorial"] == "Demo"
    client = _client(tmp_path)
    res = client.get("/api/search", params={"q": "入口"})
    assert res.status_code == 200
    assert res.json()["items"]


def test_22_tags_and_groups(tmp_path: Path):
    _tutorial(tmp_path)
    meta = set_tags(tmp_path / "output", "Demo", ["python", "web"], group="工作")
    assert "python" in meta["tags"]
    groups = list_groups(tmp_path / "output")
    assert "Demo" in groups["groups"]["工作"]
    client = _client(tmp_path)
    res = client.post("/api/tutorials/Demo/tags", json={"tags": ["cli"], "group": "工具"})
    assert res.status_code == 200
    assert client.get("/api/library/tags").json()["groups"]["工具"]


def test_23_import_exported_zip(tmp_path: Path):
    folder = _tutorial(tmp_path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Pack/index.md", "# Pack\n\nimported\n")
        zf.writestr("Pack/01_a.md", "# A\n\n*source: a.py*\n")
    dest = tmp_path / "other"
    dest.mkdir()
    result = import_tutorial_zip(buf.getvalue(), dest, name="Pack")
    assert result["name"] == "Pack"
    assert (dest / "Pack" / "index.md").is_file()
    client = _client(tmp_path)
    res = client.post("/api/tutorials/import", files={"file": ("p.zip", buf.getvalue(), "application/zip")})
    assert res.status_code == 200


def test_24_obsidian_notion_export(tmp_path: Path):
    folder = _tutorial(tmp_path)
    blob = build_obsidian_zip(folder)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = zf.namelist()
        assert any("_MOC.md" in n for n in names)
        assert any(".obsidian/app.json" in n for n in names)
    md = notion_index_markdown(folder)
    assert "## index" in md or "## 01_a" in md
    client = _client(tmp_path)
    assert client.get("/api/tutorials/Demo/obsidian.zip").status_code == 200
    assert client.get("/api/tutorials/Demo/notion.md").status_code == 200


def test_25_epub_export(tmp_path: Path):
    folder = _tutorial(tmp_path)
    blob = build_epub(folder)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        assert "mimetype" in zf.namelist()
        assert zf.read("mimetype") == b"application/epub+zip"
        assert "OEBPS/content.opf" in zf.namelist()
    client = _client(tmp_path)
    res = client.get("/api/tutorials/Demo/export.epub")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/epub")


def test_26_rss_atom(tmp_path: Path):
    _tutorial(tmp_path)
    atom = atom_feed(tmp_path / "output")
    rss = rss_feed(tmp_path / "output")
    assert "<feed" in atom and "Demo" in atom
    assert "<rss" in rss and "Demo" in rss
    client = _client(tmp_path)
    assert client.get("/feed.xml").status_code == 200
    assert client.get("/rss.xml").status_code == 200


def test_27_feishu_dingtalk_cards():
    job = {"status": "succeeded", "output_name": "Demo", "file_count": 3, "error": None}
    feishu = feishu_body(job)
    assert feishu["msg_type"] == "interactive"
    assert "Demo" in json.dumps(feishu, ensure_ascii=False)
    ding = dingtalk_body(job)
    assert ding["msgtype"] == "markdown"
    assert "Demo" in ding["markdown"]["text"]


def test_28_github_action_template():
    text = Path(".github/workflows/quick-study.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in text
    assert "OPENROUTER_API_KEY" in text
    assert "--token" not in text
    assert Path("docs/github-action.md").is_file()


def test_29_gitlab_ci_component():
    text = Path(".gitlab-ci/quick-study.yml").read_text(encoding="utf-8")
    assert "spec:" in text
    assert "inputs:" in text
    assert "OPENROUTER" not in text or "secrets" not in text.lower()
    assert Path("docs/gitlab-ci.md").is_file()


def test_30_demo_mode_blocks_generate(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("QUICK_STUDY_DEMO", "1")
    assert demo_enabled()
    assert is_generate_path("POST", "/api/jobs")
    client = _client(tmp_path)
    res = client.post("/api/jobs", json={"source_type": "repo", "repo_url": "https://github.com/o/r"})
    assert res.status_code == 403
    assert "演示" in res.json()["detail"]
    assert client.get("/api/tutorials").status_code == 200


def test_31_audit_log_no_accounts(tmp_path: Path):
    _tutorial(tmp_path)
    actor = actor_id(token="secret-token")
    assert actor.startswith("token:")
    assert "secret-token" not in actor
    append_audit(tmp_path / "output", action="ask", actor=actor, detail={"tutorial": "Demo"})
    items = list_audit(tmp_path / "output")
    assert items[0]["action"] == "ask"
    client = _client(tmp_path)
    client.post("/api/tutorials/Demo/ask", json={"question": "入口"})
    logged = client.get("/api/audit").json()["items"]
    assert any(i["action"] == "ask" for i in logged)


def test_32_rate_limit_buckets():
    lim = BucketLimiter(per_minute=2, burst=2)
    lim.check("ip:1")
    lim.check("ip:1")
    with pytest.raises(RateLimited):
        lim.check("ip:1")
    lim.check("token:abc")
    assert bucket_key(token="abcdefghij").startswith("token:")


def test_33_key_rotation_detect(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    info = detect_key_files(tmp_path)
    assert "OPENROUTER_API_KEY" in info["env_keys_present"]
    assert info["hints"]
    assert "作废" in "\n".join(info["hints"])
    client = _client(tmp_path)
    res = client.get("/api/ops/keys")
    assert res.status_code == 200
    assert "hints" in res.json()
    assert Path("docs/key-rotation.md").is_file()


def test_34_otel_traces_optional(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OTEL_TRACES", "1")
    assert otel_enabled()
    with span("ask", output_dir=tmp_path / "output", attributes={"q": "1"}):
        pass
    path = traces_path(tmp_path / "output")
    assert path.is_file()
    rec = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert rec["name"] == "ask"
    assert rec["duration_ms"] >= 0


def test_35_jsonl_run_logs(tmp_path: Path):
    emit_run(tmp_path / "output", "job_done", status="succeeded", id="x")
    items = tail_run(tmp_path / "output")
    assert items[0]["event"] == "job_done"
    line = (tmp_path / "output" / "run.jsonl").read_text(encoding="utf-8").strip()
    json.loads(line)
    client = _client(tmp_path)
    assert client.get("/api/ops/logs").status_code == 200
