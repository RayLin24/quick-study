"""清单 #21–35（P1 体验与增长）。不覆盖 #1–20。"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from sdk.quick_study import QuickStudy
from utils.ask_stream import ASK_SSE_BILLING, ask_tutorial_stream
from utils.diagram_click import attach_node_ids, resolve_diagram_href
from utils.docs_drift import plan_docs_drift
from utils.gitlab_gitea import classify_repo_url, fetch_http_tree, tree_api_url
from utils.model_presets import compare_preset_costs, recommended_presets
from utils.package_picker import join_includes, scan_packages
from utils.pause_semantics import pause_matrix
from utils.quality_score import abstraction_coverage, relationship_density, score_tutorial
from utils.rewrite_chapter import list_chapter_abstractions, rewrite_chapter
from utils.tutorial_search import rank_hit, search_tutorials
from utils.v1_openapi import openapi_spec
from utils.week_path import week_path
from webapp import create_app


def _tutorial(tmp_path: Path, name="Demo"):
    folder = tmp_path / "output" / name
    folder.mkdir(parents=True)
    (folder / "index.md").write_text(
        "# Demo\n\n```mermaid\nflowchart TD\n    A0[入口]\n    A1[缓存]\n    click A0 \"01_a.md\"\n    click A1 \"02_b.md\"\n```\n",
        encoding="utf-8",
    )
    (folder / "01_a.md").write_text(
        "# Chapter 1: 入口\n\n## 动机\n\n入口负责启动。\n\n*source: src/main.py*\n\n详见 [缓存](02_b.md)。\n",
        encoding="utf-8",
    )
    (folder / "02_b.md").write_text(
        "# Chapter 2: 缓存\n\n缓存与入口协同。\n\n*source: src/cache.py*\n",
        encoding="utf-8",
    )
    (folder / "meta.json").write_text(
        json.dumps(
            {
                "language": "Chinese",
                "name": name,
                "repo_url": "https://github.com/acme/demo",
                "abstractions": [
                    {"filename": "01_a.md", "name": "入口", "description": "启动服务"},
                    {"filename": "02_b.md", "name": "缓存", "description": "缓存层"},
                ],
                "relationships": {"details": [{"from": 0, "to": 1, "label": "uses"}]},
            }
        ),
        encoding="utf-8",
    )
    return folder


def _client(tmp_path: Path, **kwargs):
    output = tmp_path / "output"
    output.mkdir(exist_ok=True)
    return TestClient(
        create_app(
            output_dir=output,
            runner=kwargs.get("runner") or (lambda cmd, on_line, cwd: 0),
            python_exe="python",
            ask_fn=kwargs.get("ask_fn"),
            rewrite_call=kwargs.get("rewrite_call"),
        )
    )


def test_21_rewrite_one_chapter(tmp_path: Path):
    folder = _tutorial(tmp_path)
    items = list_chapter_abstractions(folder)
    assert any(i["filename"] == "01_a.md" for i in items)

    def fake(prompt, **_kwargs):
        assert "单章重写" in prompt
        return "# Chapter 1: 入口\n\n新说明：只讲启动。\n\n*source: src/main.py*\n"

    out = rewrite_chapter(folder, "01_a.md", "只讲启动入口", call=fake)
    assert out["calls"] == 1
    assert out["cheaper_than_resume"] is True
    assert "只讲启动" in (folder / "01_a.md").read_text(encoding="utf-8")
    client = _client(tmp_path, rewrite_call=fake)
    res = client.post(
        "/api/tutorials/Demo/rewrite-chapter",
        json={"filename": "02_b.md", "description": "缓存怎么失效"},
    )
    assert res.status_code == 200
    assert res.json()["filename"] == "02_b.md"


def test_22_ask_sse_separate_billing(tmp_path: Path):
    folder = _tutorial(tmp_path)

    def tokens(prompt):
        assert "问题" in prompt
        yield "缓"
        yield "存"

    events = list(ask_tutorial_stream(folder, "缓存怎么工作", token_iter=tokens))
    kinds = [e["type"] for e in events]
    assert kinds[0] == "meta"
    assert "token" in kinds and kinds[-1] == "done"
    assert ASK_SSE_BILLING in events[0]["billing"]
    assert events[-1]["answer"] == "缓存"

    def ask_fn(_folder, question):
        from utils.ask_tutorial import AskResult

        return AskResult(answer=f"答:{question}", used_chapters=[], citations=[])

    client = _client(tmp_path, ask_fn=ask_fn)
    res = client.post("/api/tutorials/Demo/ask/events", json={"question": "入口"})
    assert res.status_code == 200
    assert "text/event-stream" in res.headers["content-type"]
    body = res.text
    assert "data:" in body
    v1 = client.post("/v1/tutorials/Demo/ask/events", json={"question": "入口"})
    assert v1.status_code == 200
    page = client.get("/t/Demo/01_a.md")
    assert "Ask SSE" in page.text or "流式回答" in page.text
    assert Path("docs/ask-sse.md").is_file()


def test_24_monorepo_package_picker(tmp_path: Path):
    root = tmp_path / "mono"
    (root / "apps" / "web").mkdir(parents=True)
    (root / "packages" / "sdk").mkdir(parents=True)
    (root / "apps" / "web" / "package.json").write_text("{}", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    items = scan_packages(root)
    kinds = {i["kind"] for i in items}
    assert "package.json" in kinds or any(i["path"].startswith("apps/") for i in items)
    assert any(i["path"] in {".", "root"} or i["name"] == "root" for i in items) or any(
        i["kind"] == "pyproject.toml" for i in items
    )
    include = join_includes(items)
    assert "apps/web/**" in include
    listed = scan_packages(paths=["apps/api/package.json", "packages/core/pyproject.toml"])
    assert any("apps/api" in i["include"] for i in listed)
    client = _client(tmp_path)
    res = client.post("/api/packages", json={"paths": ["apps/web/package.json", "apps/worker/pyproject.toml"]})
    assert res.status_code == 200
    assert "apps/web/**" in res.json()["include"]
    home = client.get("/")
    assert "scan-packages" in home.text


def test_25_bitbucket_http_tree(monkeypatch):
    url = "https://bitbucket.org/acme/demo"
    assert classify_repo_url(url) == "bitbucket"
    api = tree_api_url(url)
    assert api and "api.bitbucket.org/2.0/repositories/acme/demo/src/HEAD" in api

    class Fake:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "values": [
                    {"path": "src/main.py", "type": "commit_file"},
                    {"path": "src", "type": "commit_directory"},
                ]
            }

    import utils.gitlab_gitea as gg

    monkeypatch.setattr(gg.requests, "get", lambda *a, **k: Fake())
    paths = fetch_http_tree(url)
    assert "src/main.py" in paths
    assert "src" not in paths


def test_26_lock_and_sbom():
    lock = Path("requirements.lock").read_text(encoding="utf-8")
    assert "fastapi==" in lock
    assert Path("sbom/cyclonedx.json").is_file()
    doc = json.loads(Path("sbom/cyclonedx.json").read_text(encoding="utf-8"))
    assert doc["bomFormat"] == "CycloneDX"
    names = {c["name"].lower() for c in doc["components"]}
    assert "fastapi" in names
    assert Path("docs/reproducible.md").is_file()


def test_27_v1_openapi_stable():
    spec = openapi_spec()
    assert spec["openapi"].startswith("3.")
    assert "/v1/tutorials" in spec["paths"]
    assert "/v1/jobs" in spec["paths"]
    assert spec["x-quick-study"]["experimental"] == "/api/*"
    client = TestClient(create_app(output_dir=Path("output"), runner=lambda c, o, w: 0, python_exe="python"))
    res = client.get("/v1/openapi.json")
    assert res.status_code == 200
    assert "list" in json.dumps(res.json()).lower() or "/v1/tutorials" in res.json()["paths"]
    assert Path("docs/v1-openapi.md").is_file()


def test_28_sdk_preview_export_wait(tmp_path: Path):
    _tutorial(tmp_path)
    src = tmp_path / "repo"
    src.mkdir()
    (src / "a.py").write_text("print(1)\n", encoding="utf-8")
    client = _client(tmp_path)
    qs = QuickStudy("http://test")

    def fake_request(method, path, body=None, timeout=60):
        if method == "GET":
            r = client.request(method, path)
        else:
            r = client.request(method, path, json=body)
        return r.json() if r.content else {}

    def fake_bytes(path, timeout=120):
        return client.get(path).content

    qs._request = fake_request
    qs._bytes = fake_bytes
    prev = qs.preview(source_type="dir", local_dir=str(src), include="*.py", max_abstractions=2)
    assert prev.get("dry_run") is True or prev.get("file_count") is not None or "estimated_calls" in prev
    blob = qs.fetch_zip("Demo")
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        assert any(name.endswith("index.md") for name in zf.namelist())
    snap = qs.wait_for_job(sse=False, timeout=2, poll=0.01)
    assert snap.get("status") in {"idle", "succeeded", "failed", "cancelled"}


def test_29_pause_cooperative_matrix():
    matrix = pause_matrix()
    assert matrix["recommended"] == "cooperative"
    assert matrix["cooperative"] is True
    assert Path("docs/pause-resume.md").is_file()
    client = TestClient(create_app(output_dir=Path("output"), runner=lambda c, o, w: 0, python_exe="python"))
    res = client.get("/api/jobs/pause-semantics")
    assert res.status_code == 200
    assert res.json()["recommended"] == "cooperative"


def test_30_dual_model_presets_keep_flash_default():
    presets = recommended_presets()
    flash = next(p for p in presets if p["id"] == "flash")
    dual = next(p for p in presets if p["id"] == "dual")
    assert flash["default"] is True
    assert flash["structure"] == "z-ai/glm-5.3-flash"
    assert flash["write"] == "z-ai/glm-5.3-flash"
    assert dual["structure"] == "z-ai/glm-5.3-flash"
    assert dual["write"] != dual["structure"]
    rows = compare_preset_costs(max_abstractions=8)
    by_id = {r["id"]: r for r in rows}
    assert by_id["dual"]["usd"] >= by_id["flash"]["usd"]
    client = TestClient(create_app(output_dir=Path("output"), runner=lambda c, o, w: 0, python_exe="python"))
    res = client.get("/api/model-presets", params={"max_abstractions": 8})
    assert res.status_code == 200
    assert res.json()["default_model"] == "z-ai/glm-5.3-flash"


def test_31_docs_drift_mvp(tmp_path: Path):
    folder = _tutorial(tmp_path)
    event = {
        "after": "abcdef1234567890",
        "repository": {"name": "Demo", "html_url": "https://github.com/acme/demo"},
        "commits": [{"modified": ["src/main.py"]}],
    }
    plan = plan_docs_drift(folder, event)
    assert plan["branch"].startswith("docs/drift-")
    assert any(ch["filename"] == "01_a.md" for ch in plan["chapters"])
    assert plan["job"]["incremental"] is True
    client = _client(tmp_path)
    res = client.post("/api/docs-drift", json={"tutorial": "Demo", "changed": ["src/cache.py"]})
    assert res.status_code == 200
    assert res.json()["chapters"][0]["filename"] == "02_b.md"


def test_32_diagram_data_id_not_textcontent(tmp_path: Path):
    folder = _tutorial(tmp_path)
    chapters = [
        {"title": "入口", "href": "/t/Demo/01_a.md", "filename": "01_a.md"},
        {"title": "缓存", "href": "/t/Demo/02_b.md", "filename": "02_b.md"},
        {"title": "目录", "href": "/t/Demo", "filename": "index.md"},
    ]
    attached = attach_node_ids(chapters, (folder / "index.md").read_text(encoding="utf-8"))
    assert {c["filename"]: c["node_id"] for c in attached if c.get("node_id")}["01_a.md"] == "A0"
    href = resolve_diagram_href(
        data_id="flowchart-A1-99",
        element_id="flowchart-A1-99",
        click_file="",
        text_content="入口 缓存 入口",  # mixed SVG soup must not win
        chapters=attached,
    )
    assert href == "/t/Demo/02_b.md"
    js = Path("web/static/diagram_click.js").read_text(encoding="utf-8")
    assert "data-id" in js
    assert "textContent" not in js or "fragile" in js
    client = _client(tmp_path)
    page = client.get("/t/Demo")
    assert "diagram_click.js" in page.text
    assert "node_id" in page.text


def test_33_quality_coverage_density(tmp_path: Path):
    folder = _tutorial(tmp_path)
    cov = abstraction_coverage(folder)
    assert cov["ratio"] > 0
    den = relationship_density(folder)
    assert den["edges"] >= 1
    scored = score_tutorial(folder)
    assert "abstraction_coverage" in scored
    assert "relationship_density" in scored
    client = _client(tmp_path)
    res = client.get("/api/tutorials/Demo/quality")
    assert res.status_code == 200
    assert "ratio" in res.json()["abstraction_coverage"]


def test_34_search_title_outranks_body(tmp_path: Path):
    _tutorial(tmp_path)
    extra = tmp_path / "output" / "Other"
    extra.mkdir()
    (extra / "index.md").write_text("# Other\n\n提到入口这个词在正文里。\n", encoding="utf-8")
    (extra / "09_note.md").write_text("# 附录\n\n正文里写了入口但标题没有。\n", encoding="utf-8")
    hits = search_tutorials(tmp_path / "output", "入口")
    assert hits
    assert hits[0]["title"].find("入口") >= 0 or hits[0]["score"] >= rank_hit("入口", "", "x", ["入口"])
    assert hits[0]["score"] >= hits[-1]["score"]
    title_score = rank_hit("入口模块", "", "其他", ["入口"])
    body_score = rank_hit("附录", "", "这里提到入口", ["入口"])
    assert title_score > body_score


def test_35_adaptive_week_path_skips_read(tmp_path: Path):
    folder = _tutorial(tmp_path)
    progress = {"/t/Demo/01_a.md": 1.0}
    data = week_path(folder, progress=progress, adaptive=True)
    names = [c["filename"] for day in data["days"] for c in day["chapters"]]
    assert "01_a.md" not in names
    assert any(s["filename"] == "01_a.md" for s in data["skipped"])
    assert data["adaptive"] is True
    client = _client(tmp_path)
    (tmp_path / "output" / "continue.json").write_text(json.dumps(progress), encoding="utf-8")
    res = client.get("/api/tutorials/Demo/week-path", params={"adaptive": True})
    assert res.status_code == 200
    assert res.json()["adaptive"] is True


def test_merge_after_docs_present():
    text = Path("docs/merge-after-10-13.md").read_text(encoding="utf-8")
    assert "PR #10" in text and "PR #13" in text
    assert "#21" in text or "21–35" in text
