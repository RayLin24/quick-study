"""Monorepo package picker (#24): scan roots → include patterns."""

from __future__ import annotations

from pathlib import Path

MARKERS = (
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "composer.json",
)
NEST_DIRS = ("apps", "packages", "services", "libs", "cmd")


def _posix(path: str) -> str:
    return str(path).replace("\\", "/").strip("/")


def include_for(rel: str, *, is_dir: bool) -> str:
    rel = _posix(rel)
    if is_dir:
        return f"{rel}/**" if rel else "**"
    parent = str(Path(rel).parent).replace("\\", "/")
    if parent in {".", ""}:
        return Path(rel).name
    return f"{parent}/**"


def _from_paths(paths: list[str]) -> list[dict]:
    found: dict[str, dict] = {}
    for raw in paths:
        posix = _posix(raw)
        parts = [p for p in posix.split("/") if p and p != "."]
        if not parts:
            continue
        name = parts[-1]
        if name in MARKERS:
            parent = "/".join(parts[:-1])
            key = parent or "."
            found[key] = {
                "name": Path(parent).name if parent else "root",
                "path": key,
                "kind": name,
                "include": include_for(parent, is_dir=True) if parent else "**",
            }
        if parts[0] in NEST_DIRS and len(parts) >= 2:
            pkg = "/".join(parts[:2])
            found.setdefault(
                pkg,
                {
                    "name": parts[1],
                    "path": pkg,
                    "kind": f"{parts[0]}/",
                    "include": f"{pkg}/**",
                },
            )
    return sorted(found.values(), key=lambda item: item["path"])


def scan_packages(root: str | Path | None = None, paths: list[str] | None = None) -> list[dict]:
    """Scan a local tree or an explicit path list for package roots."""
    if paths is not None:
        return _from_paths([str(p) for p in paths])
    if root is None:
        return []
    base = Path(root)
    if not base.is_dir():
        raise ValueError("目录不存在")
    collected: list[str] = []
    for marker in MARKERS:
        for hit in base.rglob(marker):
            try:
                collected.append(hit.relative_to(base).as_posix())
            except ValueError:
                continue
    for nest in NEST_DIRS:
        nest_dir = base / nest
        if nest_dir.is_dir():
            for child in nest_dir.iterdir():
                if child.is_dir() and not child.name.startswith("."):
                    collected.append(f"{nest}/{child.name}/")
    return _from_paths(collected)


def join_includes(packages: list[dict]) -> str:
    seen = []
    for item in packages or []:
        pattern = (item.get("include") or "").strip()
        if pattern and pattern not in seen:
            seen.append(pattern)
    return " ".join(seen)
