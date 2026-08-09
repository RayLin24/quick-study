"""e2e 冒烟：只读接口 + 静态托管 + 任务创建校验。"""
import json
import urllib.request

BASE = "http://127.0.0.1:8600"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return r.status, r.read()


status, body = get("/api/jobs")
jobs = json.loads(body)
assert status == 200 and len(jobs) == 1 and jobs[0]["id"] == "demo1", jobs
print("[ok] GET /api/jobs ->", jobs[0]["url"], jobs[0]["status"])

status, body = get("/api/jobs/demo1")
detail = json.loads(body)
p = detail["progress"]
assert p["detail"]["crawl"]["parsed"] >= 150, p["detail"]["crawl"]
assert p["detail"]["writing"] == {"written": 20, "total": 20}, p["detail"].get("writing")
assert p["cost"]["tokens_in"] > 0
print("[ok] 进度推导:", p["detail"]["crawl"], "/ 20 章 / token",
          p["cost"]["tokens_in"], "+", p["cost"]["tokens_out"])

status, body = get("/api/jobs/demo1/book")
book = json.loads(body)
assert book["book_title"] and len(book["chapters"]) == 20
assert all(c["filename"] for c in book["chapters"])
assert book["glossary"]["terms"]
print("[ok] GET /book ->", book["book_title"], "| 章节:", len(book["chapters"]))

fn = book["chapters"][0]["filename"]
from urllib.parse import quote
status, body = get("/api/jobs/demo1/chapters/" + quote(fn))
ch = json.loads(body)
assert ch["markdown"].startswith("# 第1章"), ch["markdown"][:30]
print("[ok] GET /chapters ->", fn, len(ch["markdown"]), "chars")

status, body = get("/")
assert status == 200 and b"quickstudy" in body
print("[ok] GET / -> SPA index.html (", len(body), "bytes )")

# 创建校验：坏 URL 必须 400
req = urllib.request.Request(BASE + "/api/jobs", method="POST",
                             data=json.dumps({"url": "bad"}).encode(),
                             headers={"Content-Type": "application/json"})
try:
    urllib.request.urlopen(req, timeout=10)
    raise AssertionError("bad URL should not pass")
except urllib.error.HTTPError as e:
    assert e.code == 400
    print("[ok] POST /api/jobs bad url -> 400")

print("SMOKE ALL PASS")
