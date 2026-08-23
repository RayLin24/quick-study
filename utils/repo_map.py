from __future__ import annotations

import os
import re

DEFAULT_MAP_CHARS = 100_000
MAX_SYMBOLS_PER_FILE = 15
MAX_FILES_PER_ABSTRACTION = 12

_SYMBOL_PATTERNS = [
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(\w+)", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.M),
    re.compile(r"^\s*(?:export\s+)?(?:type|interface|enum)\s+(\w+)", re.M),
    re.compile(r"^\s*(?:async\s+)?def\s+(\w+)", re.M),
    re.compile(r"^\s*class\s+(\w+)", re.M),
    re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)", re.M),
    re.compile(r"^\s*func\s+(\w+)", re.M),
    re.compile(
        r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?(?:\(|function)",
        re.M,
    ),
]

_DOWNRANK = (
    "/partners/",
    "/vendor/",
    "/third_party/",
    "/third-party/",
    "/node_modules/",
    "/dist/",
    "/generated/",
    "/fixtures/",
    "/testdata/",
)
_UPRANK = (
    "readme",
    "__init__",
    "/index.",
    "mod.rs",
    "/main.",
    "/app.",
    "/core/",
    "/src/",
    "/lib/",
)

_CAMEL = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])")


def _posix(path: str) -> str:
    return path.replace("\\", "/")


def extract_symbols(path: str, content: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    text = content or ""
    for pattern in _SYMBOL_PATTERNS:
        for name in pattern.findall(text):
            if not name or name in seen:
                continue
            if name.startswith("_") and name != "__init__":
                continue
            seen.add(name)
            found.append(name)
            if len(found) >= MAX_SYMBOLS_PER_FILE:
                return found
    return found


def file_importance(path: str, content: str) -> int:
    p = _posix(path).lower()
    score = 0
    score -= p.count("/") * 2
    for token in _DOWNRANK:
        if token in f"/{p}/" or token.strip("/") in p.split("/"):
            score -= 20
            break
    for token in _UPRANK:
        if token in p:
            score += 15
    score += min(len(extract_symbols(path, content)), 10)
    return score


def _parent_dir(path: str) -> str:
    p = _posix(path)
    if "/" not in p:
        return ""
    return p.rsplit("/", 1)[0]


def _basename(path: str) -> str:
    return _posix(path).rsplit("/", 1)[-1]


def _map_char_limit(max_chars: int | None) -> int:
    if max_chars is not None:
        return max_chars
    return int(os.getenv("LLM_CONTEXT_CHARS", str(DEFAULT_MAP_CHARS)))


def _skeleton_lines(files_data: list) -> list[str]:
    lines: list[str] = []
    current_dir = None
    for i, (path, _content) in enumerate(files_data):
        directory = _parent_dir(path)
        if directory != current_dir:
            header = f"{directory}/" if directory else "."
            lines.append(header)
            current_dir = directory
        lines.append(f"  {i} {_basename(path)}")
    return lines


def _flat_lines(files_data: list) -> list[str]:
    return [f"{i} {_posix(path)}" for i, (path, _content) in enumerate(files_data)]


def build_repo_map(files_data, *, max_chars: int | None = None) -> tuple[str, dict]:
    """Path + symbol map covering every file.

    Symbols are added only while the character budget remains. The file list
    itself is never truncated: every index stays visible to the LLM.
    """
    limit = _map_char_limit(max_chars)
    header = (
        f"Repo map: {len(files_data)} files. "
        "Prefer project-wide core abstractions, not every adapter/plugin."
    )
    grouped = [header] + _skeleton_lines(files_data)
    flat = [header] + _flat_lines(files_data)
    if len("\n".join(grouped)) <= limit:
        lines = grouped
        index_to_line = {}
        cursor = 1
        current_dir = None
        for i, (path, _content) in enumerate(files_data):
            directory = _parent_dir(path)
            if directory != current_dir:
                current_dir = directory
                cursor += 1
            index_to_line[i] = cursor
            cursor += 1
    else:
        lines = flat
        index_to_line = {i: i + 1 for i in range(len(files_data))}

    used = len("\n".join(lines))
    files_with_symbols = 0
    if used < limit:
        ranked = sorted(
            range(len(files_data)),
            key=lambda i: file_importance(files_data[i][0], files_data[i][1]),
            reverse=True,
        )
        for i in ranked:
            path, content = files_data[i]
            symbols = extract_symbols(path, content)
            if not symbols:
                continue
            line_no = index_to_line[i]
            decorated = f"{lines[line_no]} | {', '.join(symbols)}"
            extra = len(decorated) - len(lines[line_no])
            if used + extra > limit:
                continue
            lines[line_no] = decorated
            used += extra
            files_with_symbols += 1

    text = "\n".join(lines)
    stats = {
        "file_count": len(files_data),
        "chars": len(text),
        "files_with_symbols": files_with_symbols,
        "max_chars": limit,
    }
    return text, stats


def _name_tokens(name: str) -> list[str]:
    tokens = []
    for part in _CAMEL.findall(name or ""):
        token = part.lower()
        if len(token) > 2:
            tokens.append(token)
    return tokens


def expand_abstraction_files(
    abstractions: list,
    files_data,
    *,
    max_files: int = MAX_FILES_PER_ABSTRACTION,
) -> list:
    paths = [_posix(path).lower() for path, _content in files_data]
    expanded = []
    for abstr in abstractions:
        selected = {
            idx
            for idx in abstr.get("files") or []
            if isinstance(idx, int) and 0 <= idx < len(paths)
        }
        dirs = {paths[i].rsplit("/", 1)[0] if "/" in paths[i] else "" for i in selected}
        tokens = _name_tokens(abstr.get("name") or "")
        scored = []
        for i, path in enumerate(paths):
            if i in selected:
                continue
            parent = path.rsplit("/", 1)[0] if "/" in path else ""
            score = 0
            if parent in dirs:
                score += 5
            for token in tokens:
                if token in path:
                    score += 8
            if score:
                scored.append((score, i))
        scored.sort(reverse=True)
        for _score, i in scored:
            if len(selected) >= max_files:
                break
            selected.add(i)
        item = dict(abstr)
        item["files"] = sorted(selected)
        expanded.append(item)
    return expanded
