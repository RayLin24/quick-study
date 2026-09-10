from __future__ import annotations

from pathlib import Path

from utils.ask_tutorial import AskRefused, AskResult, ask_tutorial_detailed
from web.render import first_heading, list_tutorials, resolve_tutorial_file

TOOLS = (
    {
        "name": "list_tutorials",
        "description": "List generated tutorials (read-only).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_chapter",
        "description": "Read one tutorial markdown file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tutorial": {"type": "string"},
                "filename": {"type": "string", "default": "index.md"},
            },
            "required": ["tutorial"],
        },
    },
    {
        "name": "ask",
        "description": "Ask a question against a generated tutorial.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tutorial": {"type": "string"},
                "question": {"type": "string"},
                "include_source": {"type": "boolean", "default": False},
            },
            "required": ["tutorial", "question"],
        },
    },
)


def list_mcp_tools() -> list[dict]:
    return [dict(item) for item in TOOLS]


def call_mcp_tool(output_dir: Path, name: str, arguments: dict | None = None) -> dict:
    args = arguments or {}
    output = Path(output_dir)
    if name == "list_tutorials":
        return {"items": list_tutorials(output)}
    if name == "read_chapter":
        tutorial = str(args.get("tutorial") or "").strip()
        filename = str(args.get("filename") or "index.md").strip() or "index.md"
        path = resolve_tutorial_file(output, tutorial, filename)
        return {"filename": path.name, "text": path.read_text(encoding="utf-8")}
    if name == "ask":
        tutorial = str(args.get("tutorial") or "").strip()
        question = str(args.get("question") or "").strip()
        folder = output / tutorial
        raw = ask_tutorial_detailed(
            folder,
            question,
            include_source=bool(args.get("include_source")),
        )
        if isinstance(raw, AskResult):
            return {
                "answer": raw.answer,
                "used_chapters": raw.used_chapters,
                "routed": raw.routed,
                "include_source": raw.include_source,
                "source_snippets": raw.source_snippets,
                "evidence": raw.evidence,
            }
        return {"answer": raw}
    raise ValueError(f"unknown or write-denied MCP tool: {name}")


def mcp_catalog() -> dict:
    return {
        "name": "quick-study",
        "readonly": True,
        "tools": list_mcp_tools(),
        "denied": ["generate", "start_job", "delete_tutorial"],
    }
