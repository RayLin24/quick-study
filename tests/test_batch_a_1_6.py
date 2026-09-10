"""Unit tests for checklist #1–6 (stale remote, blob pin, diagram jump, mermaid, outline, pricing)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from nodes import CombineTutorial, ConfirmOutline
from utils.diagram_nodes import build_diagram_nodes, mermaid_node_lines
from utils.mermaid_validate import assert_chapter_mermaid, validate_markdown_mermaids, validate_mermaid
from utils.outline_gate import (
    build_outline_payload,
    confirm_enabled,
    confirm_outline,
    load_outline_gate,
    wait_for_outline_confirm,
)
from utils.provider_cost import estimate_cost, estimate_preview_calls, openrouter_price
from utils.source_format import github_blob_url
from utils.stale import remote_head, save_upstream_commit, stale_status
from web.render import mermaid_click_bindings
from webapp import create_app


def _tutorial(tmp_path: Path, name="Demo", **meta):
    folder = tmp_path / "output" / name
    folder.mkdir(parents=True)
    (folder / "index.md").write_text("# Demo\n\n概述。\n", encoding="utf-8")
    (folder / "01_a.md").write_text("# Chapter 1: 入口\n\n*source: src/main.py*\n", encoding="utf-8")
    payload = {"language": "Chinese", "name": name, "repo_url": "https://github.com/acme/demo"}
    payload.update(meta)
    (folder / "meta.json").write_text(json.dumps(payload), encoding="utf-8")
    return folder


def _client(tmp_path: Path):
    output = tmp_path / "output"
    output.mkdir(exist_ok=True)
    return TestClient(create_app(output_dir=output, runner=lambda cmd, on_line, cwd: 0, python_exe="python"))


def test_1_stale_uses_remote_head_for_repo_url(tmp_path: Path):
    folder = _tutorial(tmp_path)
    save_upstream_commit(folder, "aaa1111")
    with patch("utils.stale.git_head", return_value=None), patch(
        "utils.stale.remote_head", return_value="bbb2222"
    ) as remote:
        status = stale_status(folder)
    remote.assert_called()
    assert status["stale"] is True
    assert status["reason"] == "commit_drift"
    assert status["source"] == "remote"
    assert status["current"] == "bbb2222"


def test_1_remote_head_github_api():
    repo = {"default_branch": "main"}
    commit = {"sha": "deadbeefcafebabe"}

    def fake_get(url, *args, **kwargs):
        class Resp:
            status_code = 200

            def json(self_inner):
                if url.endswith("/repos/acme/demo"):
                    return repo
                return commit

        return Resp()

    with patch("utils.stale.requests.get", side_effect=fake_get):
        sha = remote_head("https://github.com/acme/demo")
    assert sha == "deadbeefcafebabe"


def test_2_blob_url_pins_sha_never_head():
    assert github_blob_url("https://github.com/o/r", "src/a.py") is None
    href = github_blob_url("https://github.com/o/r", "src/a.py#L10-L20", sha="abc1234def")
    assert href == "https://github.com/o/r/blob/abc1234def/src/a.py#L10-L20"
    assert "HEAD" not in href
    gl = github_blob_url("https://gitlab.com/g/p", "lib/x.go", sha="1234567", start_line=3)
    assert gl.endswith("/-/blob/1234567/lib/x.go#L3")


def test_2_source_html_uses_meta_sha(tmp_path: Path):
    folder = _tutorial(tmp_path, upstream_commit="abc1234")
    client = _client(tmp_path)
    page = client.get("/t/Demo/01_a.md")
    assert page.status_code == 200
    assert "blob/abc1234/src/main.py" in page.text
    assert "/blob/HEAD/" not in page.text


def test_3_diagram_nodes_prefer_blob():
    nodes = build_diagram_nodes(
        [{"name": "入口", "files": [0]}, {"name": "核心", "files": []}],
        files=[("src/main.py", "x"), ("src/core.py", "y")],
        chapter_filenames={
            0: {"filename": "01_entry.md", "name": "入口"},
            1: {"filename": "02_core.md", "name": "核心"},
        },
        repo_url="https://github.com/o/r",
        sha="abc1234",
    )
    assert nodes[0]["files"] == ["src/main.py"]
    assert nodes[0]["blob"].endswith("/blob/abc1234/src/main.py")
    assert nodes[0]["href"] == nodes[0]["blob"]
    assert nodes[1]["blob"] is None
    assert nodes[1]["chapter"] == "02_core.md"
    lines = "\n".join(mermaid_node_lines(nodes))
    assert "%% files A0: src/main.py" in lines
    assert "click A0" in lines and "abc1234" in lines
    mapping = mermaid_click_bindings(
        [{"title": "入口", "href": "/t/Demo/01_entry.md", "filename": "01_entry.md"}],
        nodes,
    )
    assert "abc1234" in mapping["入口"]


def test_3_tutorial_page_exposes_diagram_nodes(tmp_path: Path):
    folder = _tutorial(tmp_path, upstream_commit="abc1234")
    (folder / "meta.json").write_text(
        json.dumps(
            {
                "repo_url": "https://github.com/o/r",
                "upstream_commit": "abc1234",
                "diagram_nodes": [
                    {
                        "id": "A0",
                        "title": "入口",
                        "files": ["src/main.py"],
                        "blob": "https://github.com/o/r/blob/abc1234/src/main.py",
                        "chapter": "01_a.md",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    page = _client(tmp_path).get("/t/Demo")
    assert 'id="diagram-nodes"' in page.text
    assert "src/main.py" in page.text
    assert "bindMermaidChapterClicks" in page.text
    assert "data-blob-href" in page.text or "diagram-nodes" in page.text


def test_4_mermaid_validate_and_retry():
    good = 'flowchart TD\n    A0["入口"]\n    A1["核心"]\n    A0 -- "uses" --> A1'
    assert validate_mermaid(good)["ok"] is True
    bad = 'flowchart TD\n    A0["入口"]\n    A0 -- "x" --> A9'
    report = validate_mermaid(bad)
    assert report["ok"] is False
    assert any("orphan_edge" in err for err in report["errors"])
    md = "# Ch\n\n```mermaid\nflowchart TD\n    A0[\"x\"]\n    A0 --> Z9\n```\n"
    with pytest.raises(ValueError, match="invalid mermaid"):
        assert_chapter_mermaid(md)
    assert validate_markdown_mermaids("# no diagram")["ok"] is True


def test_4_combine_retries_invalid_then_keeps_files():
    shared = {
        "project_name": "Demo",
        "output_dir": "/tmp/out",
        "repo_url": "https://github.com/o/r",
        "upstream_commit": "abc1234",
        "relationships": {"summary": "sum", "details": [{"from": 0, "to": 1, "label": "uses"}]},
        "chapter_order": [0, 1],
        "abstractions": [
            {"name": "入口", "description": "d", "files": [0]},
            {"name": "核心", "description": "d", "files": []},
        ],
        "files": [("src/main.py", "print(1)")],
        "chapters": ["# Chapter 1: 入口\n\n" + ("说明。" * 40), "# Chapter 2: 核心\n\n" + ("说明。" * 40)],
        "language": "Chinese",
        "file_count": 1,
        "map_mode": False,
    }
    prep = CombineTutorial().prep(shared)
    assert "```mermaid" in prep["index_content"]
    assert "%% files A0: src/main.py" in prep["index_content"]
    assert "click A0" in prep["index_content"]
    assert prep["upstream_commit"] == "abc1234"
    assert prep["diagram_nodes"][0]["files"] == ["src/main.py"]


def test_5_outline_gate_payload_and_confirm(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OUTLINE_CONFIRM", raising=False)
    assert confirm_enabled(None) is False
    assert confirm_enabled(True) is True
    shared = {
        "project_name": "Demo",
        "language": "Chinese",
        "abstractions": [
            {"name": "入口", "description": "启动", "files": [0]},
            {"name": "核心", "description": "逻辑", "files": [1]},
        ],
        "chapter_order": [0, 1],
        "output_dir": str(tmp_path),
        "confirm_outline": True,
    }
    payload = build_outline_payload(shared)
    assert payload["chapter_count"] == 2
    assert payload["remaining_calls"]["remaining"] == 2
    assert payload["estimated_calls"]["write_chapters"] == 2
    confirmed = wait_for_outline_confirm(tmp_path, payload, enabled=False)
    assert confirmed["confirmed"] is True
    loaded = load_outline_gate(tmp_path)
    assert loaded["abstractions"][0]["name"] == "入口"

    monkeypatch.setenv("OUTLINE_CONFIRM", "1")
    node = ConfirmOutline()
    shared["confirm_outline"] = False
    prep = node.prep(shared)
    assert prep["chapter_count"] == 2
    out = node.exec({**prep, "confirm": False, "output_dir": str(tmp_path)})
    assert out["confirmed"] is True


def test_5_outline_api(tmp_path: Path):
    output = tmp_path / "output"
    output.mkdir()
    client = TestClient(create_app(output_dir=output, runner=lambda c, o, w: 0, python_exe="python"))
    idle = client.get("/api/jobs/current/outline")
    assert idle.status_code == 200
    assert idle.json()["awaiting"] is False

    started = {"n": 0}

    def runner(cmd, on_line, cwd, job=None):
        started["n"] += 1
        assert "--confirm-outline" in cmd
        on_line("QUICK_STUDY_STEP: outline")
        on_line(f"QUICK_STUDY_OUTLINE: waiting=1 path={output / '.outline_gate.json'}")
        from utils.outline_gate import write_outline_gate

        write_outline_gate(
            output,
            {
                "abstractions": [{"name": "入口", "description": "启动"}],
                "chapter_count": 1,
                "remaining_calls": {"remaining": 1},
                "estimated_calls": {"total": 4, "write_chapters": 1},
            },
        )
        from utils.outline_gate import gate_paths
        import time

        _, confirm = gate_paths(output)
        for _ in range(40):
            if confirm.is_file():
                on_line("QUICK_STUDY_OUTLINE: waiting=0 confirmed=1")
                return 0
            time.sleep(0.05)
        return 1

    app = create_app(output_dir=output, runner=runner, python_exe="python")
    client = TestClient(app)
    job = client.post("/api/jobs", json={"source_type": "repo", "repo_url": "https://github.com/o/r"})
    assert job.status_code == 200
    outline = None
    for _ in range(40):
        outline = client.get("/api/jobs/current/outline").json()
        if outline.get("awaiting"):
            break
        import time

        time.sleep(0.05)
    assert outline and outline.get("awaiting")
    assert outline["outline"]["abstractions"][0]["name"] == "入口"
    confirmed = client.post("/api/jobs/current/confirm-outline")
    assert confirmed.status_code == 200
    client.app.state.manager.wait(timeout=5)
    assert started["n"] == 1


def test_6_openrouter_per_model_pricing(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "OPENROUTER")
    monkeypatch.setenv("OPENROUTER_MODEL", "z-ai/glm-5.3-flash")
    monkeypatch.delenv("LLM_STRUCTURE_MODEL", raising=False)
    monkeypatch.delenv("LLM_WRITE_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_PRICES_JSON", raising=False)
    flash = openrouter_price("z-ai/glm-5.3-flash")
    gpt = openrouter_price("openai/gpt-4o")
    assert flash != gpt
    cheap = estimate_cost(prompt_tokens=1_000_000, completion_tokens=0, provider="OPENROUTER", model="z-ai/glm-5.3-flash")
    dear = estimate_cost(prompt_tokens=1_000_000, completion_tokens=0, provider="OPENROUTER", model="openai/gpt-4o")
    assert cheap["usd"] < dear["usd"]
    assert cheap["model"] == "z-ai/glm-5.3-flash"
    monkeypatch.setenv("LLM_STRUCTURE_MODEL", "z-ai/glm-5.3-flash")
    monkeypatch.setenv("LLM_WRITE_MODEL", "openai/gpt-4o")
    preview = estimate_preview_calls({"identify": 1, "relationships": 1, "order": 1, "write_chapters": 2, "total": 5})
    assert preview["structure_model"] == "z-ai/glm-5.3-flash"
    assert preview["write_model"] == "openai/gpt-4o"
    assert "parts" in preview
    monkeypatch.setenv("OPENROUTER_PRICES_JSON", json.dumps({"custom/m": [9.0, 9.0]}))
    custom = openrouter_price("custom/m")
    assert custom == (9.0, 9.0)


def test_6_default_model_unchanged():
    from utils.call_llm import DEFAULT_OPENROUTER_MODEL

    assert DEFAULT_OPENROUTER_MODEL == "z-ai/glm-5.3-flash"
    assert openrouter_price() == openrouter_price("z-ai/glm-5.3-flash")
