"""MCP JSON-RPC 2.0 over stdio (Content-Length framing). Cursor-configurable."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import IO

from utils.mcp_tools import call_mcp_tool, mcp_catalog

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "quick-study"
SERVER_VERSION = "0.4.0"


class McpStdioServer:
    def __init__(
        self,
        output_dir: Path,
        *,
        stdin: IO[bytes] | None = None,
        stdout: IO[bytes] | None = None,
    ):
        self.output_dir = Path(output_dir)
        self.stdin = stdin or sys.stdin.buffer
        self.stdout = stdout or sys.stdout.buffer

    def read_message(self) -> dict | None:
        headers: dict[str, str] = {}
        while True:
            line = self.stdin.readline()
            if not line:
                return None
            if line in {b"\r\n", b"\n"}:
                break
            raw = line.decode("utf-8", errors="replace").strip()
            if ":" in raw:
                key, value = raw.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        length = int(headers.get("content-length") or "0")
        if length <= 0:
            return None
        blob = self.stdin.read(length)
        if not blob:
            return None
        data = json.loads(blob.decode("utf-8"))
        return data if isinstance(data, dict) else None

    def write_message(self, payload: dict) -> None:
        blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(blob)}\r\n\r\n".encode("ascii")
        self.stdout.write(header + blob)
        self.stdout.flush()

    def handle(self, message: dict) -> dict | None:
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params") or {}
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            }
        if method == "notifications/initialized" or method == "initialized":
            return None
        if method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        if method == "tools/list":
            catalog = mcp_catalog()
            tools = []
            for item in catalog["tools"]:
                tools.append(
                    {
                        "name": item["name"],
                        "description": item.get("description") or "",
                        "inputSchema": item.get("input_schema") or {"type": "object"},
                    }
                )
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools}}
        if method == "tools/call":
            name = str(params.get("name") or "")
            arguments = params.get("arguments") or {}
            try:
                result = call_mcp_tool(self.output_dir, name, arguments)
                text = json.dumps(result, ensure_ascii=False, indent=2)
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": text}],
                        "isError": False,
                    },
                }
            except Exception as exc:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": str(exc)}],
                        "isError": True,
                    },
                }
        if msg_id is None:
            return None
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    def serve(self) -> int:
        while True:
            try:
                message = self.read_message()
            except (OSError, json.JSONDecodeError, ValueError):
                return 1
            if message is None:
                return 0
            reply = self.handle(message)
            if reply is not None:
                self.write_message(reply)


def encode_message(payload: dict) -> bytes:
    blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return f"Content-Length: {len(blob)}\r\n\r\n".encode("ascii") + blob


def run_stdio(output_dir: Path, *, stdin=None, stdout=None) -> int:
    return McpStdioServer(output_dir, stdin=stdin, stdout=stdout).serve()
