"""Read-only MCP tools: CLI --list/--call, HTTP, and Cursor-configurable stdio JSON-RPC."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from utils.mcp_tools import call_mcp_tool, mcp_catalog


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Quick Study read-only MCP tools")
    parser.add_argument("--output", default=os.getenv("QUICK_STUDY_OUTPUT") or "output")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--call", help="tool name")
    parser.add_argument("--args", default="{}", help="JSON arguments")
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="MCP JSON-RPC 2.0 over stdin/stdout (Content-Length framing).",
    )
    args = parser.parse_args(argv)
    if args.stdio:
        from utils.mcp_stdio import run_stdio

        return run_stdio(Path(args.output))
    if args.list or not args.call:
        print(json.dumps(mcp_catalog(), ensure_ascii=False, indent=2))
        return 0
    result = call_mcp_tool(Path(args.output), args.call, json.loads(args.args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
