"""Read-only MCP-style tool server (list / read / ask). No generate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from utils.mcp_tools import call_mcp_tool, mcp_catalog


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Quick Study read-only MCP tools")
    parser.add_argument("--output", default="output")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--call", help="tool name")
    parser.add_argument("--args", default="{}", help="JSON arguments")
    args = parser.parse_args(argv)
    if args.list or not args.call:
        print(json.dumps(mcp_catalog(), ensure_ascii=False, indent=2))
        return 0
    result = call_mcp_tool(Path(args.output), args.call, json.loads(args.args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
