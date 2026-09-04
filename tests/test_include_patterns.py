from utils.patterns import file_matches_any, should_include_file


def test_include_matches_path_and_basename():
    """GitHub crawl used to match basename only, so src/*.py empty-ran."""
    assert file_matches_any("src/app.py", {"src/*.py"})
    assert file_matches_any("pkg/mod.py", {"*.py"})
    assert file_matches_any("src/app.py", {"*.py"})
    assert not file_matches_any("pkg/mod.py", {"src/*.py"})
    assert not file_matches_any("src/app.js", {"src/*.py"})
    assert not file_matches_any("src/app.py", {"lib/*.py"})


def test_should_include_file_aligns_github_with_local():
    assert should_include_file("src/app.py", {"src/*.py"}, None)
    assert should_include_file("utils/call_llm.py", {"*.py"}, {"tests/*"})
    assert not should_include_file("tests/test_x.py", {"*.py"}, {"tests/*"})
    assert not should_include_file("README.md", {"src/*.py"}, None)
