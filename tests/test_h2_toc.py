from web.render import add_h2_ids, markdown_to_html


def test_markdown_h2_gets_ids_and_toc():
    html = markdown_to_html("# Title\n\n## 动机\n\n正文\n\n## 实现\n\n更多", "Demo")
    assert 'id="动机"' in html or 'id="' in html
    _body, toc = add_h2_ids(html)
    texts = [item["text"] for item in toc]
    assert "动机" in texts
    assert "实现" in texts
    assert all(item["id"] for item in toc)
