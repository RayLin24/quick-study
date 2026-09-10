"""Dry-run preview: crawl only, never write chapters or call the LLM."""

from __future__ import annotations

import os
from pathlib import Path

from utils.crawl_github_files import GitHubCrawlError, crawl_github_files, parse_github_http_url
from utils.crawl_local_files import crawl_local_files
from utils.errors import format_error
from utils.patterns import DEFAULT_EXCLUDE_PATTERNS, DEFAULT_INCLUDE_PATTERNS

# identify + relationships + order, then one write call per chapter (upper bound).
FIXED_LLM_CALLS = 3


class PreviewError(ValueError):
    """Crawl failed during dry-run."""


def estimate_llm_calls(max_abstractions: int) -> dict:
    chapters = max(1, int(max_abstractions or 10))
    write = chapters
    total = FIXED_LLM_CALLS + write
    return {
        "identify": 1,
        "relationships": 1,
        "order": 1,
        "write_chapters": write,
        "total": total,
        "note": "写章按章节上限估算；缓存命中不消耗额度。",
    }


def estimate_bundle(max_abstractions: int) -> dict:
    """Calls + USD only. No crawl. Used when max_abstractions changes."""
    from utils.provider_cost import estimate_preview_calls

    calls = estimate_llm_calls(max_abstractions)
    return {"estimated_calls": calls, "cost": estimate_preview_calls(calls["total"])}


def derive_project_name(payload: dict) -> str:
    name = str(payload.get("name") or payload.get("project_name") or "").strip()
    if name:
        return name
    repo_url = payload.get("repo_url")
    if repo_url:
        try:
            _owner, repo_name, _remainder = parse_github_http_url(repo_url)
            return repo_name
        except GitHubCrawlError:
            return str(repo_url).rstrip("/").split("/")[-1].replace(".git", "")
    local_dir = payload.get("local_dir")
    if local_dir:
        return Path(local_dir).resolve().name
    return "project"


def collect_files(payload: dict) -> list[tuple[str, str]]:
    """Same crawl as FetchRepo, without the over-threshold hard fail."""
    repo_url = payload.get("repo_url")
    local_dir = payload.get("local_dir")
    include = payload.get("include_patterns") or payload.get("include")
    exclude = payload.get("exclude_patterns") or payload.get("exclude")
    if include:
        include_patterns = set(include) if not isinstance(include, set) else include
    else:
        include_patterns = set(DEFAULT_INCLUDE_PATTERNS)
    if exclude:
        exclude_patterns = set(exclude) if not isinstance(exclude, set) else exclude
    else:
        exclude_patterns = set(DEFAULT_EXCLUDE_PATTERNS)
    max_file_size = payload.get("max_file_size") or payload.get("max_size") or 100000
    token = payload.get("github_token") or payload.get("token") or os.environ.get("GITHUB_TOKEN")

    if repo_url:
        try:
            result = crawl_github_files(
                repo_url=repo_url,
                token=token,
                include_patterns=include_patterns,
                exclude_patterns=exclude_patterns,
                max_file_size=int(max_file_size),
                use_relative_paths=True,
            )
        except GitHubCrawlError as exc:
            raise PreviewError(format_error(exc)) from exc
    elif local_dir:
        result = crawl_local_files(
            directory=str(local_dir),
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            max_file_size=int(max_file_size),
            use_relative_paths=True,
        )
    else:
        raise PreviewError(format_error("请选择 GitHub 仓库或本地目录"))

    if not isinstance(result, dict):
        raise PreviewError(format_error("crawl returned no result for this URL"))
    if result.get("stats", {}).get("error"):
        raise PreviewError(format_error(result["stats"]["error"]))

    files_list = sorted(result.get("files", {}).items(), key=lambda item: item[0])
    if not files_list:
        raise PreviewError(format_error("Failed to fetch files"))
    return files_list


def preview_generation(payload: dict) -> dict:
    """Crawl only. Never writes output or calls the LLM."""
    from nodes import CRAWL_FILE_THRESHOLD as NODE_THRESHOLD
    from nodes import build_code_context

    files_list = collect_files(payload)
    from utils.crawl_cache import save_crawl_cache

    save_crawl_cache(payload, files_list)
    file_count = len(files_list)
    include_specified = bool(payload.get("include_specified"))
    try:
        max_abstractions = int(payload.get("max_abstractions") or payload.get("max_abstraction_num") or 10)
    except (TypeError, ValueError):
        max_abstractions = 10
    threshold = int(os.getenv("CRAWL_FILE_THRESHOLD", str(NODE_THRESHOLD)))
    over_threshold = file_count > threshold and not include_specified
    _context, _info, stats = build_code_context(files_list)
    map_mode = stats["files_with_content"] < stats["file_count"]
    estimates = estimate_llm_calls(max_abstractions)
    sample = [path for path, _content in files_list[:20]]
    warning = None
    if over_threshold:
        warning = (
            f"爬到 {file_count} 个文件（阈值 {threshold}）。"
            "正式跑会被拒绝。请设置 include / exclude / max-size 缩小范围。"
        )
    return {
        "ok": not over_threshold,
        "dry_run": True,
        "project_name": derive_project_name(payload),
        "file_count": file_count,
        "files_sample": sample,
        "truncated_files": stats.get("truncated_files", 0),
        "files_with_content": stats.get("files_with_content", 0),
        "map_mode": map_mode,
        "over_threshold": over_threshold,
        "threshold": threshold,
        "estimated_calls": estimates,
        "warning": warning,
    }


def format_preview_report(preview: dict) -> str:
    est = preview.get("estimated_calls") or {}
    lines = [
        "DRY-RUN 预检（只爬不写，未调用 LLM）",
        f"项目: {preview.get('project_name')}",
        f"文件数: {preview.get('file_count')}",
        f"写入上下文: {preview.get('files_with_content')}（截断 {preview.get('truncated_files')}）",
        f"map_mode: {preview.get('map_mode')}",
        (
            f"估 LLM 调用: {est.get('total')} "
            f"(identify={est.get('identify')} + relationships={est.get('relationships')} "
            f"+ order={est.get('order')} + write≤{est.get('write_chapters')})"
        ),
    ]
    if preview.get("warning"):
        lines.append(f"警告: {preview['warning']}")
    sample = preview.get("files_sample") or []
    if sample:
        lines.append("文件样例:")
        lines.extend(f"  - {path}" for path in sample)
    lines.append("确认后再去掉 --dry-run / 在网页点「确认生成」。")
    return "\n".join(lines)
