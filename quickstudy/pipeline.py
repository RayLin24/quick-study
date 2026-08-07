"""M1 流水线编排：root 探测 → 指纹 → 双引擎发现 → 并发抓取 → 解析 → manifest/report。

每步幂等可断点续跑：产物按 url_hash 命名落盘，重跑时已存在且内容未变的页面
（ETag/内容hash 命中）直接复用，不重复请求。
"""
from __future__ import annotations

import asyncio
import logging

from quickstudy.config import TaskConfig
from quickstudy.discovery import discover
from quickstudy.fetch.fetcher import Fetcher, FetchResult
from quickstudy.fingerprint import detect_fingerprint, get_adapter
from quickstudy.manifest import Manifest, detect_license, summarize_versions
from quickstudy.parse.page import parse_page
from quickstudy.parse.simhash import hamming
from quickstudy.report import build_report
from quickstudy.storage import Workspace
from quickstudy.urltools import normalize_url, same_site, url_hash

log = logging.getLogger(__name__)

_FETCH_CONCURRENCY = 8   # 协程并发；域名级节奏由令牌桶控制
_DEDUP_THRESHOLD = 3     # simhash 海明距离阈值


class PageSink:
    """抓取结果落盘（raw/{hash}.html）+ 运行期索引，BFS 与批量抓取共用。"""

    def __init__(self, ws: Workspace):
        self.ws = ws
        self.fetched: dict[str, FetchResult] = {}

    def __call__(self, result: FetchResult) -> None:
        if result.ok:
            self.ws.write_bytes(f"raw/{url_hash(result.url)}.html", result.html)
        self.fetched[normalize_url(result.url)] = result


async def run_m1(cfg: TaskConfig) -> dict:
    ws = Workspace(cfg.task_dir)
    manifest = Manifest(ws)
    sink = PageSink(ws)
    log.info("任务目录: %s", ws.dir)

    async with Fetcher(cfg) as fetcher:
        # 1) 根页探测 + 指纹识别
        root_url = normalize_url(cfg.root_url)
        root_result = await fetcher.fetch(root_url, known=manifest.known_fetch_meta(root_url))
        if not root_result.ok and not root_result.skipped == "unchanged":
            hint = ""
            if root_result.status in (401, 403, 429):
                hint = ("；站点疑似有反爬拦截（Cloudflare 等）。按 design.md 4.1 约定不硬刚，"
                        "可选路径：安装 playwright 后用渲染通道重试，或用户自行导出文档后离线导入")
            raise RuntimeError(f"根页抓取失败: {root_result.error or root_result.status}{hint}")
        if root_result.ok:
            sink(root_result)
        root_html = _load_html(ws, root_result)
        fp = detect_fingerprint(root_html)
        adapter = get_adapter(fp["adapter"])
        log.info("站点指纹: %s (信号: %s, generator: %s)",
                 fp["adapter"], fp["signal"], fp["generator"] or "-")

        # 2) 双引擎发现
        discovery = await discover(cfg, fetcher, adapter, root_html, on_page=sink)
        log.info("发现页面: sitemap=%d sidebar=%d 最终=%d",
                 discovery.counts.get("sitemap", 0),
                 discovery.counts.get("sidebar", 0),
                 discovery.counts.get("final", 0))

        # 3) 并发抓取（BFS 已抓的页面由 sink.fetched 去重）
        sem = asyncio.Semaphore(_FETCH_CONCURRENCY)

        async def fetch_one(url: str) -> None:
            if url in sink.fetched:
                return
            known = manifest.known_fetch_meta(url)
            async with sem:
                result = await fetcher.fetch(url, known=known)
            sink(result)

        await asyncio.gather(*(fetch_one(p.url) for p in discovery.pages))

    # 4) 解析 + 近重复检测
    parsed_docs: list[dict] = []
    fetch_records: dict[str, dict] = {}
    duplicates: dict[str, str] = {}
    fingerprints: list[tuple[int, str]] = []

    for page in discovery.pages:
        url = normalize_url(page.url)
        pid = url_hash(url)
        result = sink.fetched.get(url)
        fetch_records[url] = {} if result is None else {
            "status": result.status, "render": result.render,
            "error": result.error, "skipped": result.skipped,
            "elapsed_ms": result.elapsed_ms,
        }
        if result is not None:
            manifest.update_page(url, etag=result.etag, last_modified=result.last_modified,
                                 content_hash=result.content_hash, status=result.status,
                                 render=result.render)

        html = _load_html(ws, result, pid) if result is not None else ""
        if not html:
            manifest.update_page(url, parsed=False, page_id=pid)
            continue

        version = adapter.detect_version(url)
        md, doc = parse_page(pid, url, html, adapter, root_url, version)
        if doc.get("ok"):
            fp_int = int(doc["simhash"], 16)
            dup_of = next((u for fpv, u in fingerprints
                           if hamming(fp_int, fpv) <= _DEDUP_THRESHOLD), None)
            if dup_of:
                duplicates[url] = dup_of
                doc["duplicate_of"] = dup_of
            else:
                fingerprints.append((fp_int, url))
            ws.write_text(f"parsed/{pid}.md", md)
        ws.write_json(f"parsed/{pid}.json", doc)
        manifest.update_page(url, parsed=bool(doc.get("ok")), page_id=pid,
                             version=version, url_class=page.url_class,
                             sidebar_index=page.sidebar_index,
                             word_count=doc.get("word_count", 0),
                             duplicate_of=doc.get("duplicate_of", ""))
        parsed_docs.append(doc)

    # 5) manifest + 报告
    # 跨页合并侧边栏观测（ADR-002）：折叠式侧边栏靠单页拿不到全站目录
    sidebar_obs: dict[str, dict] = {}
    for doc in parsed_docs:
        for obs in doc.get("sidebar_observed", []):
            if not same_site(obs["url"], root_url):
                continue  # 导航里的外链（GitHub 仓库等）不进目录观测
            entry = sidebar_obs.setdefault(obs["url"], {"index": obs["index"],
                                                        "section": obs["section"], "seen_on": 0})
            entry["index"] = min(entry["index"], obs["index"]) if obs["index"] >= 0 else entry["index"]
            entry["section"] = entry["section"] or obs["section"]
            entry["seen_on"] += 1

    license_info = detect_license(root_html)
    versions = summarize_versions([p.get("version", "") for p in manifest.data["pages"].values()])
    manifest.save(root_url=cfg.root_url, adapter=adapter.name, fingerprint=fp,
                  license=license_info, versions=versions, scope=discovery.scope,
                  sidebar_observations=sidebar_obs)
    report = build_report(ws, discovery, fetch_records, parsed_docs, duplicates)
    report["license"] = license_info
    report["versions"] = versions
    ws.write_json("report.json", report)
    return report


def _load_html(ws: Workspace, result: FetchResult | None, pid: str | None = None) -> str:
    if result is not None and result.html:
        return result.html.decode("utf-8", errors="replace")
    # 增量重跑 unchanged：从 raw 快照读
    if pid is None and result is not None:
        pid = url_hash(result.url)
    if pid:
        p = ws.path("raw", f"{pid}.html")
        if p.exists():
            return p.read_text(encoding="utf-8", errors="replace")
    return ""
