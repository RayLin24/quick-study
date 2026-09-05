import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from utils.abstraction_compare import compare_abstractions
from utils.abstraction_map import build_abstraction_map
from utils.ask_cli import run_ask
from utils.ask_thread import format_history, save_thread
from utils.auth_errors import AUTH_READ_ONLY, AUTH_UNAUTHORIZED
from utils.bilingual import other_language
from utils.cost_hint import max_abstractions_cost_hint
from utils.crawl_progress import crawl_progress
from utils.deepwiki import deepwiki_url
from utils.digest import gitingest_digest
from utils.disk_warn import temp_clone_warning
from utils.gitlab_gitea import classify_repo_url, tree_api_url
from utils.glossary import build_glossary
from utils.health import health_payload
from utils.heatmap import heatmap_markdown
from utils.identify_parse import parse_llm_structured
from utils.issue_guide import build_issue_guide_prompt
from utils.keyword_guard import check_manual, missing_keywords
from utils.learning_goal import learning_goal_block
from utils.mcp_tools import call_mcp_tool, mcp_catalog
from utils.mermaid_export import extract_mermaid_blocks, mermaid_to_svg
from utils.models import model_for_stage
from utils.offline_html import build_offline_html
from utils.pagerank_order import in_degree_order, pagerank_order
from utils.pat_wizard import classify_pat, wizard_steps
from utils.pr_guide import build_pr_guide_prompt, parse_pr_diff
from utils.push_hook import incremental_job_from_push, verify_github_signature
from utils.quiz import chapter_quiz
from utils.readonly_token import is_write_path
from utils.rpm_limit import RateSemaphore
from utils.seed_files import load_seed_texts
from utils.source_symbol import source_symbol_targets
from utils.timeout_hint import job_timeout_hint
from utils.versions import diff_versions, snapshot_tutorial
from utils.webhook import slack_body
from utils.workbench import save_workbench
from utils.zip_source import extract_source_zip
from web.render import list_tutorials, slugify_heading
from webapp import create_app


def _client(tmp_path: Path, **kwargs):
    output = tmp_path / "output"
    output.mkdir(exist_ok=True)
    return TestClient(
        create_app(output_dir=output, runner=kwargs.get("runner") or (lambda cmd, on_line, cwd: 0), python_exe="python")
    )


def _tutorial(tmp_path: Path, name="Demo"):
    folder = tmp_path / "output" / name
    folder.mkdir(parents=True)
    (folder / "index.md").write_text("# Demo\n\n**入口** 在 `main.py`。\n\n```mermaid\nflowchart TD\n A[x]\n```\n", encoding="utf-8")
    (folder / "01_a.md").write_text("# Chapter 1: 入口\n\n讲入口。*source: src/main.py#run*\n", encoding="utf-8")
    (folder / "meta.json").write_text(json.dumps({"language": "Chinese", "file_count": 2, "repo_url": "https://github.com/acme/demo", "chapter_count": 1}), encoding="utf-8")
    return folder


def test_41_compose_requires_token():
    text = Path("docker-compose.yml").read_text(encoding="utf-8")
    docker = Path("Dockerfile.web").read_text(encoding="utf-8")
    assert "QUICK_STUDY_TOKEN" in text
    assert "127.0.0.1:8000:8000" in text
    assert '"0.0.0.0:' not in text and "- 0.0.0.0:" not in text
    assert "web.serve" in docker
    assert "127.0.0.1" in docker


def test_42_mcp_readonly(tmp_path: Path):
    _tutorial(tmp_path)
    catalog = mcp_catalog()
    names = {t["name"] for t in catalog["tools"]}
    assert names == {"list_tutorials", "read_chapter", "ask"}
    assert "generate" in catalog["denied"]
    listed = call_mcp_tool(tmp_path / "output", "list_tutorials")
    assert listed["items"][0]["name"] == "Demo"
    read = call_mcp_tool(tmp_path / "output", "read_chapter", {"tutorial": "Demo", "filename": "index.md"})
    assert "Demo" in read["text"]
    with pytest.raises(ValueError, match="unknown"):
        call_mcp_tool(tmp_path / "output", "generate", {})


def test_43_cli_ask(tmp_path: Path, monkeypatch):
    _tutorial(tmp_path)

    def fake(folder, question):
        from utils.ask_tutorial import AskResult

        return AskResult(answer=f"ok:{question}", used_chapters=[{"filename": "01_a.md"}])

    monkeypatch.setattr("utils.ask_cli.ask_tutorial_detailed", fake)
    result = run_ask("Demo", "入口?", output=tmp_path / "output")
    assert result.answer.startswith("ok:")


def test_44_offline_html(tmp_path: Path):
    folder = _tutorial(tmp_path)
    html = build_offline_html(folder)
    assert "单文件离线" in html
    assert "01_a" in html
    client = _client(tmp_path)
    res = client.get("/api/tutorials/Demo/offline.html")
    assert res.status_code == 200
    assert "离线" in res.text


def test_45_mermaid_export(tmp_path: Path):
    folder = _tutorial(tmp_path)
    blocks = extract_mermaid_blocks((folder / "index.md").read_text(encoding="utf-8"))
    assert blocks
    svg = mermaid_to_svg(blocks[0], title="t")
    assert "<svg" in svg and "flowchart" in svg
    client = _client(tmp_path)
    res = client.post("/api/tutorials/Demo/mermaid/export")
    assert res.status_code == 200
    assert res.json()["svg"]


def test_46_ask_thread(tmp_path: Path):
    folder = _tutorial(tmp_path)
    save_thread(folder, [{"question": "q1", "answer": "a1"}])
    hist = format_history([{"question": "q1", "answer": "a1"}])
    assert "Q: q1" in hist


def test_47_dual_model(monkeypatch):
    monkeypatch.setenv("LLM_STRUCTURE_MODEL", "flash")
    monkeypatch.setenv("LLM_WRITE_MODEL", "strong")
    assert model_for_stage("identify", "default") == "flash"
    assert model_for_stage("write", "default") == "strong"
    assert model_for_stage("other", "default") == "default"


def test_48_versions(tmp_path: Path):
    folder = _tutorial(tmp_path)
    a = snapshot_tutorial(folder, label="v1")
    (folder / "index.md").write_text("# Demo2\n", encoding="utf-8")
    b = snapshot_tutorial(folder, label="v2")
    diff = diff_versions(folder, a, b)
    assert "Demo2" in diff or "---" in diff


def test_49_abstraction_compare():
    out = compare_abstractions([{"name": "A"}, {"name": "B"}], [{"name": "B"}, {"name": "C"}])
    assert out["only_left"] == ["A"]
    assert out["shared"] == ["B"]


def test_50_51_seed_and_goal(tmp_path: Path):
    p = tmp_path / "seed.py"
    p.write_text("def main():\n    pass\n", encoding="utf-8")
    assert "seed.py" in load_seed_texts([str(p)])
    assert "LEARNING GOAL" in learning_goal_block("改入口")


def test_52_53_guides():
    diff = "+++ b/src/a.py\n@@ -1 +1 @@\n+x\n"
    info = parse_pr_diff(diff)
    assert info["file_count"] == 1
    assert "src/a.py" in build_pr_guide_prompt(diff)
    assert "Title: bug" in build_issue_guide_prompt("bug", "body")


def test_54_gitlab_gitea():
    assert classify_repo_url("https://gitlab.com/group/proj") == "gitlab"
    assert classify_repo_url("https://gitea.com/o/r") == "gitea"
    assert "gitlab.com/api/v4" in (tree_api_url("https://gitlab.com/group/proj") or "")


def test_55_zip_upload(tmp_path: Path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("proj/app.py", "print(1)\n")
    dest = extract_source_zip(buf.getvalue(), tmp_path / "unz")
    assert (dest / "app.py").is_file() or (dest / "proj" / "app.py").is_file()


def test_56_workbench(tmp_path: Path):
    items = save_workbench(tmp_path, [{"repo_url": "https://github.com/a/b"}])
    assert items[0]["repo_url"].endswith("/b")


def test_57_readonly_paths():
    assert is_write_path("POST", "/api/jobs")
    assert not is_write_path("POST", "/api/tutorials/Demo/ask")
    assert AUTH_UNAUTHORIZED and AUTH_READ_ONLY


def test_58_webhook_payload():
    body = slack_body({"status": "succeeded", "output_name": "Demo"})
    assert "Demo" in body["text"]


def test_59_cron_schedule(tmp_path: Path):
    from utils.cron_regen import cron_command, save_schedule

    jobs = save_schedule(tmp_path, [{"repo_url": "https://github.com/a/b", "name": "B"}])
    cmd = cron_command("python", Path("main.py"), jobs[0])
    assert "--incremental" in cmd


def test_60_healthz(tmp_path: Path):
    payload = health_payload(bind_host="127.0.0.1", output_dir=tmp_path)
    assert payload["ok"] is True
    assert payload["loopback"] is True
    client = _client(tmp_path)
    res = client.get("/healthz")
    assert res.status_code == 200
    assert "disk_free_bytes" in res.json()
    assert "has_llm_key" in res.json()


def test_61_70_reader_assets():
    html = Path("web/templates/tutorial.html").read_text(encoding="utf-8")
    js = Path("web/static/reader.js").read_text(encoding="utf-8")
    css = Path("web/static/app.css").read_text(encoding="utf-8")
    index = Path("web/templates/index.html").read_text(encoding="utf-8")
    appjs = Path("web/static/app.js").read_text(encoding="utf-8")
    assert "qs-progress" in js
    assert "theme-dark" in js and "theme-dark" in css
    assert "ArrowRight" in js
    assert "toc-drawer" in html and "is-open" in js
    assert "@media print" in css
    assert "annotation" in js
    assert "star" in js
    assert "术语表" in html
    assert "热力图" in html
    assert "quiz" in js
    assert "speechSynthesis" in js
    assert "source-symbol" in js
    assert "/embed/" in html
    assert "github.com/" in Path("docs/github-extension.md").read_text(encoding="utf-8")
    assert "QUICK_STUDY_READ_TOKEN" in Path("docs/share-readonly.md").read_text(encoding="utf-8")
    assert "Fetch 拉取" in index
    assert "smoke-btn" in index
    assert "auto-open" in appjs
    assert "copy-error" in appjs
    assert "ask-countdown" in html
    assert "CRAWL:" in crawl_progress("start")


def test_68_69_70_content(tmp_path: Path):
    folder = _tutorial(tmp_path)
    gloss = build_glossary(folder)
    assert "入口" in gloss
    heat = heatmap_markdown(["A", "B"], [{"from": 0, "to": 1}])
    assert "█" in heat
    quiz = chapter_quiz("# Chapter 1: 入口\n\n入口负责启动服务。\n", filename="01_a.md")
    assert quiz["questions"]


def test_76_deepwiki():
    assert deepwiki_url("https://github.com/acme/demo") == "https://deepwiki.com/acme/demo"


def test_77_bilingual():
    assert other_language("Chinese") == "english"


def test_78_cover():
    from utils.cover import light_cover_svg

    svg = light_cover_svg("Demo")
    assert "<svg" in svg and "概念一览" in svg


def test_85_86_87_ops(tmp_path: Path):
    warn = temp_clone_warning(tmp_path, need_bytes=10**18)
    assert warn
    sem = RateSemaphore(rpm=100, concurrency=2)
    sem.acquire()
    sem.release()
    hint = job_timeout_hint(8, per_chapter=100)
    assert hint["suggested_job_timeout"] >= 100


def test_88_richer_list(tmp_path: Path):
    _tutorial(tmp_path)
    items = list_tutorials(tmp_path / "output")
    assert items[0]["chapter_count"] >= 1
    assert items[0]["file_count"] == 2


def test_89_abstraction_map():
    mapping = build_abstraction_map([{"name": "A", "files": [0]}], [("a.py", "x")])
    assert mapping["abstractions"][0]["files"] == ["a.py"]


def test_90_identify_json_then_yaml():
    data = parse_llm_structured('```json\n[{"name":"A","description":"d","file_indices":[0]}]\n```')
    assert data[0]["name"] == "A"
    y = parse_llm_structured("```yaml\n- name: B\n  description: d\n  file_indices: [0]\n```")
    assert y[0]["name"] == "B"


def test_91_chinese_slug_disambiguation():
    a = slugify_heading("动机")
    b = slugify_heading("实现")
    assert a != b
    assert "动机" in a


def test_92_mermaid_pinned():
    docs = Path("docs/mermaid-version.md").read_text(encoding="utf-8")
    vendor = Path("web/static/vendor/mermaid.min.js").read_text(encoding="utf-8", errors="replace")
    assert "11.4.1" in docs
    assert "11.4.1" in vendor


def test_93_94_auth_and_cost():
    hint = max_abstractions_cost_hint(16)
    assert hint and "16" in hint
    assert max_abstractions_cost_hint(8) is None


def test_95_keyword_guard():
    result = check_manual()
    assert result["ok"], result["missing"]
    assert missing_keywords("hello") 


def test_96_dry_run_resume_limit_exist():
    main = Path("main.py").read_text(encoding="utf-8")
    assert "--dry-run" in main and "--resume" in main
    assert Path("tests/test_preview.py").is_file()
    assert Path("tests/test_p1_features.py").is_file()


def test_97_pat_wizard():
    assert classify_pat("ghp_abcdefghijk")["ok"]
    assert not classify_pat("x")["ok"]
    assert wizard_steps()


def test_98_digest():
    text = gitingest_digest([("a.py", "print(1)")])
    assert "## a.py" in text


def test_99_pagerank():
    order = in_degree_order(3, [{"from": 0, "to": 1}, {"from": 1, "to": 2}])
    assert order[0] == 0
    ranked = pagerank_order(2, [{"from": 0, "to": 1}])
    assert set(ranked) == {0, 1}


def test_100_push_hook():
    secret = "s3cret"
    body = b'{"repository":{"html_url":"https://github.com/a/b","name":"b"},"ref":"refs/heads/main"}'
    assert verify_github_signature(secret, body, "sha256=" + __import__("hmac").new(secret.encode(), body, "sha256").hexdigest())
    job = incremental_job_from_push(json.loads(body))
    assert job["incremental"] is True
    assert job["repo_url"].endswith("/b")


def test_http_p2_bundle(tmp_path: Path):
    _tutorial(tmp_path)
    client = _client(tmp_path)
    assert client.get("/api/config").json()["mermaid_version"] == "11.4.1"
    assert client.get("/mcp/tools").json()["readonly"] is True
    assert client.get("/api/tutorials/Demo/glossary").status_code == 200
    assert client.get("/api/tutorials/Demo/quiz").json()["items"]
    assert client.post("/api/tutorials/Demo/star").json()["names"]
    assert client.post("/api/pat/check", json={"token": "ghp_abcdefgh"}).json()["ok"]
    assert "a.py" in client.post("/api/digest", json={"files": [{"path": "a.py", "content": "x"}]}).json()["digest"]
    page = client.get("/t/Demo")
    assert page.status_code == 200
    assert "DeepWiki" in page.text
    assert "reader.js" in page.text
    home = client.get("/")
    assert "空状态试跑公开仓" in home.text
    assert "Fetch 拉取" in home.text
