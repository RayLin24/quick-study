"""测试夹具：模拟三类文档生成器的静态 HTML 与 sitemap。"""
from __future__ import annotations

MKDOCS_SIDEBAR = """
<nav class="md-nav md-nav--primary">
  <ul class="md-nav__list">
    <li class="md-nav__item"><a class="md-nav__link" href="/">Home</a></li>
    <li class="md-nav__item"><a class="md-nav__link" href="/learn/">Learn</a></li>
    <li class="md-nav__item"><a class="md-nav__link" href="/tutorial/first-steps/">First Steps</a></li>
    <li class="md-nav__item"><a class="md-nav__link" href="/tutorial/path-params/">Path Parameters</a></li>
    <li class="md-nav__item"><a class="md-nav__link" href="/advanced/middleware/">Middleware</a></li>
  </ul>
</nav>
"""


def mkdocs_page(title: str, body_html: str, sidebar: str = MKDOCS_SIDEBAR) -> str:
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="generator" content="mkdocs-1.6.1, mkdocs-material-9.5.0">
<title>{title} - Demo Docs</title>
</head><body>
<div class="md-container">
  <header class="md-header">header noise</header>
  <div class="md-main"><div class="md-main__inner">
    <div class="md-sidebar md-sidebar--primary">{sidebar}</div>
    <div class="md-content">
      <article class="md-content__inner md-typeset">
        <h1>{title}</h1>
        {body_html}
      </article>
    </div>
    <div class="md-sidebar md-sidebar--secondary"><nav class="md-nav--secondary">toc</nav></div>
  </div></div>
  <footer class="md-footer">Licensed under <a rel="license" href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a></footer>
</div>
</body></html>"""


def sphinx_page(title: str, body_html: str) -> str:
    return f"""<!DOCTYPE html>
<html><head>
<meta name="generator" content="Sphinx 7.2.6">
<title>{title}</title></head>
<body>
<div class="document">
  <div class="sphinxsidebar"><ul>
    <li><a href="/index.html">Home</a></li>
    <li><a href="/usage.html">Usage</a></li>
  </ul></div>
  <div class="body" role="main"><h1>{title}</h1>{body_html}</div>
  <div class="footer">footer noise</div>
</div></body></html>"""


DOC_BODY = """
<p>FastAPI is a modern web framework. Create a file <code>main.py</code> with:</p>
<pre><code class="language-python">from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def read_root():
    return {"Hello": "World"}
</code></pre>
<p>Run it with <code>uvicorn main:app --reload</code>.</p>
<table>
<thead><tr><th>Parameter</th><th>Default</th><th>Description</th></tr></thead>
<tbody><tr><td>port</td><td>8000</td><td>Listen port</td></tr>
<tr><td>host</td><td>127.0.0.1</td><td>Bind host</td></tr></tbody>
</table>
<h2>Next steps</h2>
<p>See <a href="/tutorial/path-params/">Path Parameters</a> and
<a href="https://external.example.com/other">external link</a>.</p>
<img src="/img/diagram.png" alt="request lifecycle">
"""

SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://docs.example.com/</loc></url>
  <url><loc>https://docs.example.com/learn/</loc></url>
  <url><loc>https://docs.example.com/tutorial/first-steps/</loc></url>
  <url><loc>https://docs.example.com/tutorial/path-params/</loc></url>
  <url><loc>https://docs.example.com/advanced/middleware/</loc></url>
  <url><loc>https://docs.example.com/hidden-page/</loc></url>
  <url><loc>https://docs.example.com/blog/launch/</loc></url>
</urlset>
"""

ROBOTS_TXT = """User-agent: *
Disallow: /internal/
Sitemap: https://docs.example.com/sitemap.xml
"""

JS_SHELL_PAGE = """<!DOCTYPE html><html><head><title>app</title></head>
<body><div id="root"></div>
<script src="/assets/bundle.1.js"></script><script src="/assets/bundle.2.js"></script>
<script src="/assets/bundle.3.js"></script></body></html>"""
