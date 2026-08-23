from nodes import parse_llm_yaml


def test_parse_llm_yaml_recovers_utf8_mojibake():
    good = "可运行组件"
    broken = good.encode("utf-8").decode("latin-1")
    text = (
        "```yaml\n"
        "- name: |\n"
        f"    {broken}\n"
        "  description: |\n"
        "    demo desc\n"
        "  file_indices:\n"
        "    - 0\n"
        "```"
    )
    data = parse_llm_yaml(text)
    assert data[0]["name"].strip() == good


def test_parse_llm_yaml_strips_c1_controls():
    text = (
        "```yaml\n"
        "- name: |\n"
        "    Foo\x88Bar\n"
        "  description: |\n"
        "    demo desc\n"
        "  file_indices:\n"
        "    - 1\n"
        "```"
    )
    data = parse_llm_yaml(text)
    assert "Foo" in data[0]["name"]
    assert "Bar" in data[0]["name"]
