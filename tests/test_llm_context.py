from nodes import IdentifyAbstractions, build_code_context


def test_build_code_context_caps_total_size():
    files = [(f"f{i}.py", "x" * 4000) for i in range(500)]
    context, file_info, stats = build_code_context(
        files, max_total_chars=20_000, max_file_chars=1_000
    )
    assert len(file_info) == 500
    assert stats["files_with_content"] < 500
    assert stats["chars"] <= 20_000
    assert len(context) <= 20_000


def test_build_code_context_keeps_stable_indices_for_later_files():
    files = [("early.py", "aaa"), ("late.py", "bbb")]
    context, file_info, stats = build_code_context(
        files, max_total_chars=80, max_file_chars=3
    )
    assert file_info == [(0, "early.py"), (1, "late.py")]
    assert "File Index 0: early.py" in context


def test_identify_prep_does_not_embed_every_file(monkeypatch):
    monkeypatch.setenv("LLM_CONTEXT_CHARS", "5000")
    monkeypatch.setenv("LLM_FILE_CHARS", "200")
    files = [(f"a/{i}.py", f"print({i})\n" + ("y" * 800)) for i in range(80)]
    shared = {
        "files": files,
        "project_name": "demo",
        "language": "Chinese",
        "use_cache": True,
        "max_abstraction_num": 5,
    }
    context, listing, count, *_ = IdentifyAbstractions().prep(shared)
    assert count == 80
    assert "79.py" in context or "79.py" in listing
    assert len(context) < 20_000
