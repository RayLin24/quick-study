import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from sdk.quick_study import QuickStudy
from utils.annotations import add_annotation, load_annotations
from utils.continue_reading import continue_card, save_progress_file
from utils.disaster_recovery import backup_output, restore_output
from utils.entry_files import pick_entry_files
from utils.exercises import chapter_exercises
from utils.mem_guard import suggest_concurrency
from utils.og_cover import og_cover_html, og_image_url
from utils.presets import dump_preset, load_preset, save_preset
from utils.stale import git_head, save_upstream_commit, stale_status
from utils.test_policy import classify_files
from utils.week_path import week_path
from web.render import markdown_to_html
from webapp import create_app


def _tutorial(tmp_path: Path, name="Demo"):
    folder = tmp_path / "output" / name
    folder.mkdir(parents=True)
    (folder / "index.md").write_text("# Demo\n\n概述。\n", encoding="utf-8")
    (folder / "01_a.md").write_text("# Chapter 1: 入口\n\n*source: src/main.py*\n\n讲入口。\n", encoding="utf-8")
    (folder / "02_b.md").write_text("# Chapter 2: 核心\n\n*source: src/core.py*\n", encoding="utf-8")
    (folder / "meta.json").write_text(
        json.dumps({"language": "Chinese", "name": name, "repo_url": "https://github.com/acme/demo"}),
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
            ask_fn=kwargs.get("ask_fn") or (lambda folder, q: f"ok:{q}"),
        )
    )


def test_36_stale_commit_drift(tmp_path: Path):
    folder = _tutorial(tmp_path)
    save_upstream_commit(folder, "aaa")
    status = stale_status(folder, local_dir=tmp_path)
    assert status["recorded"] == "aaa"
    save_upstream_commit(folder, "bbb")
    with patch("utils.stale.git_head", return_value="ccc"):
        drifted = stale_status(folder, local_dir=tmp_path)
    assert drifted["stale"] is True
    client = _client(tmp_path)
    assert client.get("/api/tutorials/Demo/stale").status_code == 200


def test_37_pick_entry_files():
    picked = pick_entry_files(
        [("src/main.py", "x"), ("tests/test_a.py", "y"), ("lib/util.py", "z"), ("README.md", "r"), ("app.py", "a")]
    )
    paths = [p["path"] for p in picked]
    assert "src/main.py" in paths
    assert paths[0] in {"src/main.py", "app.py"}
    assert len(picked) <= 5


def test_38_test_policy_visual():
    report = classify_files(["src/a.py", "tests/test_a.py"], tests_as_chapter=False)
    assert report["counts"]["exclude"] >= 1
    as_ch = classify_files(["tests/test_a.py"], tests_as_chapter=True)
    assert as_ch["files"][0]["fate"] == "chapter"
    client_src = Path("webapp.py").read_text(encoding="utf-8")
    assert "/api/test-policy" in client_src


def test_39_v1_stable_api(tmp_path: Path):
    _tutorial(tmp_path)
    client = _client(tmp_path)
    listed = client.get("/v1/tutorials")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["name"] == "Demo"
    asked = client.post("/v1/tutorials/Demo/ask", json={"question": "入口"})
    assert asked.status_code == 200
    assert asked.json()["answer"].startswith("ok:")
    job = client.post("/v1/jobs", json={"source_type": "repo", "repo_url": "https://github.com/o/r"})
    assert job.status_code == 200
    assert client.get("/v1/jobs/current").status_code == 200


def test_40_python_sdk(tmp_path: Path):
    _tutorial(tmp_path)
    client = _client(tmp_path)
    sdk = QuickStudy(base_url=str(client.base_url))

    def fake_open(req, timeout=60):
        class Resp:
            def read(self):
                path = req.full_url.split(str(client.base_url), 1)[-1]
                method = req.get_method()
                body = req.data.decode("utf-8") if req.data else None
                res = client.request(method, path, content=body, headers=dict(req.header_items()))
                return res.content

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return Resp()

    with patch("sdk.quick_study.urllib.request.urlopen", side_effect=fake_open):
        items = sdk.list_tutorials()
        assert items[0]["name"] == "Demo"
        ans = sdk.ask("Demo", "入口")
        assert "answer" in ans


def test_41_github_extension_or_bookmarklet():
    man = json.loads(Path("extensions/github/manifest.json").read_text(encoding="utf-8"))
    assert man["manifest_version"] == 3
    bg = Path("extensions/github/background.js").read_text(encoding="utf-8")
    assert "127.0.0.1:8000/?repo=" in bg
    docs = Path("docs/github-extension.md").read_text(encoding="utf-8")
    assert "javascript:" in docs


def test_42_raycast_alfred_scripts():
    ray = Path("scripts/raycast-ask.sh").read_text(encoding="utf-8")
    alfred = Path("scripts/alfred-ask.sh").read_text(encoding="utf-8")
    assert "/v1/tutorials/" in ray and "QUICK_STUDY_TOKEN" in ray
    assert "--token" not in ray and "-t " not in alfred
    assert "/v1/tutorials/" in alfred


def test_43_change_this_spot_exercises(tmp_path: Path):
    items = chapter_exercises("# 入口\n\n*source: src/main.py*\n", filename="01_a.md")
    assert items[0]["file"] == "src/main.py"
    assert "改这一处" in items[0]["prompt"]
    _tutorial(tmp_path)
    client = _client(tmp_path)
    res = client.get("/api/tutorials/Demo/exercises")
    assert res.status_code == 200
    assert res.json()["items"]


def test_44_week_path(tmp_path: Path):
    folder = _tutorial(tmp_path)
    path = week_path(folder)
    assert path["chapter_count"] >= 2
    assert path["days"]
    client = _client(tmp_path)
    res = client.get("/api/tutorials/Demo/week-path")
    assert res.status_code == 200
    md = client.get("/api/tutorials/Demo/week-path.md")
    assert "一周" in md.text


def test_45_shared_annotations_file(tmp_path: Path):
    folder = _tutorial(tmp_path)
    add_annotation(folder, filename="01_a.md", quote="入口", note="这里", author="ada")
    items = load_annotations(folder)
    assert items[-1]["author"] == "ada"
    assert (folder / "annotations.json").is_file()
    client = _client(tmp_path)
    res = client.post(
        "/api/tutorials/Demo/annotations",
        json={"filename": "01_a.md", "note": "同步用文件", "author": "bob"},
    )
    assert res.status_code == 200
    assert any(i.get("author") == "bob" for i in res.json()["items"])


def test_46_og_cover():
    url = og_image_url("https://github.com/acme/demo")
    assert url == "https://opengraph.githubassets.com/1/acme/demo"
    html = markdown_to_html("# Demo\n\n概述\n", "Demo", cover=True, repo_url="https://github.com/acme/demo")
    assert "opengraph.githubassets.com" in html
    assert og_cover_html(None) == ""


def test_47_continue_reading_card(tmp_path: Path):
    _tutorial(tmp_path)
    card = continue_card(tmp_path / "output", {"/t/Demo/01_a.md": 9e12})
    assert card["filename"] == "01_a.md"
    client = _client(tmp_path)
    client.post("/api/continue", json={"path": "/t/Demo/01_a.md"})
    home = client.get("/")
    assert "今日继续读" in home.text
    assert client.get("/api/continue").json()["tutorial"] == "Demo"


def test_48_preset_json_roundtrip(tmp_path: Path):
    payload = {"source_type": "repo", "repo_url": "https://github.com/o/r", "include": "*.py", "max_abstractions": 6}
    saved = save_preset(tmp_path / "output", "web", payload)
    assert saved["preset"]["include"] == "*.py"
    loaded = load_preset(tmp_path / "output", "web")
    assert loaded["max_abstractions"] == 6
    assert "github_token" not in dump_preset({**payload, "github_token": "secret"})
    client = _client(tmp_path)
    res = client.post("/api/presets", json={"name": "x", "payload": payload})
    assert res.status_code == 200
    assert client.get("/api/presets/x").json()["repo_url"].endswith("/r")


def test_49_backup_restore(tmp_path: Path):
    _tutorial(tmp_path)
    out = tmp_path / "output"
    rec = backup_output(out, tmp_path / "bak")
    assert Path(rec["archive"]).is_file()
    dest = tmp_path / "restored"
    restore_output(Path(rec["archive"]), dest)
    assert (dest / "Demo" / "index.md").is_file()
    client = _client(tmp_path)
    res = client.post("/api/ops/backup", json={})
    assert res.status_code == 200
    assert res.json()["archive"]


def test_50_mem_watermark_throttle():
    low = suggest_concurrency(80, available_kb=200_000, current=5)
    assert low["suggested"] == 1
    assert low["throttled"] is True
    many = suggest_concurrency(500, available_kb=8_000_000, current=5)
    assert many["suggested"] <= 2
    ok = suggest_concurrency(10, available_kb=8_000_000, current=5)
    assert ok["suggested"] == 5
    client_src = Path("nodes.py").read_text(encoding="utf-8")
    assert "apply_concurrency" in client_src
