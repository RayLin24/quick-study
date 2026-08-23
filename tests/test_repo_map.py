from utils.repo_map import build_repo_map, expand_abstraction_files, extract_symbols
from nodes import IdentifyAbstractions


def test_extract_symbols_python_and_typescript():
    py = extract_symbols(
        "src/runnable.py",
        "class Runnable:\n    async def invoke(self):\n        pass\n\ndef helper():\n    pass\n",
    )
    assert "Runnable" in py
    assert "invoke" in py
    ts = extract_symbols(
        "src/agent.ts",
        "export class Agent {\n}\nexport function runAgent() {}\nexport interface Tool {}\n",
    )
    assert "Agent" in ts
    assert "runAgent" in ts
    assert "Tool" in ts


def test_build_repo_map_includes_every_file_under_budget():
    files = [
        (f"libs/core/mod_{i:03d}.py", f"class Core{i}:\n    def run(self):\n        pass\n")
        for i in range(40)
    ]
    files += [
        (f"libs/partners/p{i}/adapter.py", f"class Adapter{i}:\n    pass\n")
        for i in range(40)
    ]
    text, stats = build_repo_map(files, max_chars=8_000)
    assert stats["file_count"] == 80
    assert stats["chars"] <= 8_000
    assert len(text) <= 8_000
    assert "39 " in text and "mod_039.py" in text
    assert "adapter.py" in text
    assert "Core0" in text


def test_build_repo_map_never_drops_files_when_paths_are_long():
    files = [
        (f"libs/partners/p{i:04d}/deep/path/file.py", "class Adapter: pass")
        for i in range(1766)
    ]
    text, stats = build_repo_map(files, max_chars=50_000)
    assert stats["file_count"] == 1766
    assert "1765 " in text
    assert "p1765" in text


def test_identify_prep_uses_repo_map_when_bodies_do_not_fit(monkeypatch):
    monkeypatch.setenv("LLM_CONTEXT_CHARS", "4000")
    monkeypatch.setenv("LLM_FILE_CHARS", "200")
    files = [
        (f"pkg/mod_{i:02d}.py", f"class Widget{i}:\n    def run_{i}(self):\n        pass\n" + ("y" * 400))
        for i in range(50)
    ]
    files.append(
        ("pkg/zzz_last.py", "class LastHope:\n    def finish(self):\n        pass\n" + ("z" * 400))
    )
    shared = {
        "files": files,
        "project_name": "demo",
        "language": "Chinese",
        "use_cache": True,
        "max_abstraction_num": 5,
    }
    context, listing, count, *rest = IdentifyAbstractions().prep(shared)
    assert count == 51
    assert "zzz_last.py" in context or "zzz_last.py" in listing
    assert "LastHope" in context
    assert context.count("y") < 2000


def test_expand_abstraction_files_adds_same_dir_and_name_matches():
    files = [
        ("src/runnable.py", "class Runnable: pass"),
        ("src/sequence.py", "class RunnableSequence: pass"),
        ("other/unrelated.py", "class Foo: pass"),
    ]
    out = expand_abstraction_files(
        [{"name": "Runnable", "description": "x", "files": [0]}],
        files,
        max_files=8,
    )
    assert 0 in out[0]["files"]
    assert 1 in out[0]["files"]
    assert 2 not in out[0]["files"]
