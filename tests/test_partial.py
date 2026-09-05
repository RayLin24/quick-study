from pathlib import Path

from utils.partial import isolate_cancelled_output, is_library_entry
from web.render import list_tutorials


def test_isolate_moves_folder_under_partial(tmp_path: Path):
    output = tmp_path / "output"
    demo = output / "Demo"
    demo.mkdir(parents=True)
    (demo / "index.md").write_text("# half\n", encoding="utf-8")
    dest = isolate_cancelled_output(output, "Demo", job_id="abc123")
    assert dest is not None
    assert not demo.exists()
    assert dest.parent.name == ".partial"
    assert (dest / "index.md").is_file()
    assert list_tutorials(output) == []


def test_isolate_skips_missing_and_traversal(tmp_path: Path):
    output = tmp_path / "output"
    output.mkdir()
    assert isolate_cancelled_output(output, "Nope", job_id="x") is None
    assert isolate_cancelled_output(output, "../secret", job_id="x") is None
    assert isolate_cancelled_output(output, ".partial", job_id="x") is None


def test_list_tutorials_skips_dot_and_partial(tmp_path: Path):
    output = tmp_path / "output"
    ready = output / "Ready"
    ready.mkdir(parents=True)
    (ready / "index.md").write_text("# r\n", encoding="utf-8")
    partial = output / ".partial" / "Ready-1"
    partial.mkdir(parents=True)
    (partial / "index.md").write_text("# cancelled\n", encoding="utf-8")
    hidden = output / ".hidden"
    hidden.mkdir()
    (hidden / "index.md").write_text("# no\n", encoding="utf-8")
    assert [item["name"] for item in list_tutorials(output)] == ["Ready"]
    assert is_library_entry(ready)
    assert not is_library_entry(output / ".partial")
