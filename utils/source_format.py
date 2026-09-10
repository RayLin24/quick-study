from __future__ import annotations

import os
import re
from urllib.parse import quote, urlparse

SOURCE_LINE_RE = re.compile(
    r"(?:^\s*)(?:\*source:|#\s*source:|\*\s*source:)\s*`?([^\s*`]+)`?",
    re.IGNORECASE | re.M,
)
UNIFIED_SOURCE = "*source: {path}*"
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)
LINE_ANCHOR_RE = re.compile(r"#L(\d+)(?:-L?(\d+))?$", re.IGNORECASE)
LINE_SUFFIX_RE = re.compile(r":L(\d+)$")


def symbol_source_lines_enabled() -> bool:
    return (os.getenv("SYMBOL_SOURCE_LINES") or "").strip().lower() in {"1", "true", "yes"}


def split_source_ref(ref: str) -> tuple[str, int | None]:
    text = (ref or "").strip().strip("`")
    match = LINE_SUFFIX_RE.search(text)
    if not match:
        return text, None
    return text[: match.start()], int(match.group(1))


def append_blob_line(url: str | None, line: int | None) -> str | None:
    if not url or not line:
        return url
    if "#L" in url:
        return url
    return f"{url}#L{int(line)}"


def unified_source_line(path: str, line: int | None = None) -> str:
    clean, parsed = split_source_ref(path)
    use_line = line if line is not None else parsed
    if use_line:
        return f"*source: {clean}:L{int(use_line)}*"
    return UNIFIED_SOURCE.format(path=clean)


def normalize_source_markup(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        path, line = split_source_ref(match.group(1).strip())
        return unified_source_line(path, line)

    return SOURCE_LINE_RE.sub(repl, text or "")


def _split_line_anchor(path: str) -> tuple[str, int | None, int | None]:
    text = (path or "").strip()
    match = LINE_ANCHOR_RE.search(text)
    if not match:
        return text, None, None
    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else None
    return text[: match.start()], start, end


def _repo_base(repo_url: str) -> tuple[str, str]:
    """Return (kind, owner/repo web base without /tree/...)."""
    raw = (repo_url or "").rstrip("/")
    if raw.endswith(".git"):
        raw = raw[:-4]
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    parts = [p for p in (parsed.path or "").split("/") if p and p != "-"]
    if "tree" in parts:
        parts = parts[: parts.index("tree")]
    if "blob" in parts:
        parts = parts[: parts.index("blob")]
    if "src" in parts:
        parts = parts[: parts.index("src")]
    if host.endswith("gitlab.com") or "/-/blob" in raw or "/-/tree" in raw:
        if parts and parts[-1] == "-":
            parts = parts[:-1]
        return "gitlab", f"{parsed.scheme}://{parsed.netloc}/{'/'.join(parts)}"
    if host.endswith("gitea.com") or host.endswith("gitea.io"):
        return "gitea", f"{parsed.scheme}://{parsed.netloc}/{'/'.join(parts[:2])}"
    return "github", f"{parsed.scheme}://{parsed.netloc}/{'/'.join(parts[:2])}"


def github_blob_url(
    repo_url: str,
    path: str,
    *,
    sha: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str | None:
    """Pin a blob URL to the generation commit. Never emit ``/blob/HEAD/``."""
    if not repo_url or not path:
        return None
    file_path, path_start, path_end = _split_line_anchor(path)
    if path_start is None:
        file_path, suffix_line = split_source_ref(file_path)
        path_start = suffix_line
    start_line = start_line if start_line is not None else path_start
    end_line = end_line if end_line is not None else path_end
    pin = (sha or "").strip()
    if not pin or not SHA_RE.fullmatch(pin):
        return None
    kind, base = _repo_base(repo_url)
    encoded = quote(file_path.lstrip("/"), safe="/")
    if kind == "gitlab":
        href = f"{base}/-/blob/{pin}/{encoded}"
    elif kind == "gitea":
        href = f"{base}/src/commit/{pin}/{encoded}"
    else:
        href = f"{base}/blob/{pin}/{encoded}"
    if start_line:
        href += f"#L{int(start_line)}"
        if end_line and int(end_line) != int(start_line):
            href += f"-L{int(end_line)}"
    return href


HTML_SOURCE_RE = re.compile(
    r"(?:<em>source:</em>|<em>source:)\s*`?([^<*`\s]+)(?:</em>)?",
    re.IGNORECASE,
)


def _source_buttons(
    path: str,
    *,
    repo_url: str | None,
    local_dir: str | None,
    sha: str | None = None,
) -> str:
    from utils.editor_open import resolve_editor_url

    clean, line = split_source_ref(path)
    href = github_blob_url(repo_url, clean, sha=sha, start_line=line) if repo_url else None
    label = f"{clean}:L{line}" if line else clean
    editor = resolve_editor_url(clean, local_dir=local_dir)
    open_btn = f'<a class="open-editor" href="{editor["vscode"]}" data-path="{clean}">在编辑器打开</a>'
    if href:
        return f'*source: <a href="{href}" target="_blank" rel="noreferrer">{label}</a>* {open_btn}'
    return (
        f'*source: <code class="source-path" data-path="{clean}">{label}</code>'
        f'<button type="button" class="copy-path" data-path="{clean}">复制路径</button> {open_btn}'
    )


def linkify_source_html(
    html: str,
    *,
    repo_url: str | None = None,
    local_dir: str | None = None,
    sha: str | None = None,
) -> str:
    def repl_md(match: re.Match[str]) -> str:
        return _source_buttons(match.group(1).strip(), repo_url=repo_url, local_dir=local_dir, sha=sha)

    def repl_html(match: re.Match[str]) -> str:
        return _source_buttons(match.group(1).strip(), repo_url=repo_url, local_dir=local_dir, sha=sha)

    text = SOURCE_LINE_RE.sub(repl_md, html or "")
    return HTML_SOURCE_RE.sub(repl_html, text)
