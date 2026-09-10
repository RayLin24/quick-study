"""Diagram node bindings: keep files[] on each node; prefer blob jump."""

from __future__ import annotations

from pathlib import Path

from utils.source_format import github_blob_url


def file_paths_for_abstraction(abstr: dict, files: list) -> list[str]:
    paths: list[str] = []
    for idx in abstr.get("files") or []:
        if isinstance(idx, str) and idx.strip():
            paths.append(idx.strip())
            continue
        if isinstance(idx, int) and 0 <= idx < len(files):
            entry = files[idx]
            paths.append(entry[0] if isinstance(entry, (list, tuple)) else str(entry))
    return paths


def build_diagram_nodes(
    abstractions: list[dict],
    *,
    files: list | None = None,
    chapter_filenames: dict | None = None,
    repo_url: str | None = None,
    sha: str | None = None,
) -> list[dict]:
    files = files or []
    chapter_filenames = chapter_filenames or {}
    nodes = []
    for index, abstr in enumerate(abstractions or []):
        paths = file_paths_for_abstraction(abstr, files)
        chapter = chapter_filenames.get(index) or {}
        chapter_file = chapter.get("filename")
        blob = github_blob_url(repo_url or "", paths[0], sha=sha) if paths else None
        nodes.append(
            {
                "id": f"A{index}",
                "title": (abstr.get("name") or "").strip(),
                "files": paths,
                "blob": blob,
                "chapter": chapter_file,
                "href": blob or chapter_file,
            }
        )
    return nodes


def mermaid_node_lines(nodes: list[dict], *, include_files: bool = True) -> list[str]:
    """Emit flowchart node + click lines. files[] stay on the node (tooltip / comment)."""
    lines = []
    for node in nodes:
        title = (node.get("title") or node["id"]).replace('"', "")
        extra = ""
        files = node.get("files") or []
        if include_files and files:
            shown = files[0].replace('"', "")
            extra = f"<br/>{shown}"
        lines.append(f'    {node["id"]}["{title}{extra}"]')
        if files:
            lines.append(f'    %% files {node["id"]}: {",".join(files)}')
        target = node.get("blob") or node.get("chapter")
        if target:
            tooltip = (files[0] if files else node.get("chapter") or title).replace('"', "")
            lines.append(f'    click {node["id"]} "{target}" "{tooltip}"')
    return lines


def load_diagram_nodes(folder: Path, chapters: list[dict] | None = None) -> list[dict]:
    import json

    root = Path(folder)
    meta = {}
    meta_path = root / "meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
    stored = meta.get("diagram_nodes")
    if isinstance(stored, list) and stored:
        return stored
    map_path = root / "abstraction_map.json"
    mapping = []
    if map_path.is_file():
        try:
            mapping = (json.loads(map_path.read_text(encoding="utf-8")) or {}).get("abstractions") or []
        except (OSError, json.JSONDecodeError):
            mapping = []
    by_title = {
        (item.get("title") or "").strip(): item
        for item in (chapters or [])
        if item.get("filename") not in {None, "index.md"}
    }
    sha = meta.get("upstream_commit")
    repo_url = meta.get("repo_url")
    nodes = []
    for index, item in enumerate(mapping):
        title = (item.get("name") or "").strip()
        paths = list(item.get("files") or [])
        chapter = by_title.get(title) or {}
        blob = github_blob_url(repo_url or "", paths[0], sha=sha) if paths and repo_url and sha else None
        nodes.append(
            {
                "id": f"A{index}",
                "title": title,
                "files": paths,
                "blob": blob,
                "chapter": chapter.get("filename"),
                "href": blob or chapter.get("href") or chapter.get("filename"),
            }
        )
    return nodes
