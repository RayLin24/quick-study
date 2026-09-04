"""Start the local web UI. Non-loopback binds require QUICK_STUDY_TOKEN."""

from __future__ import annotations

import argparse
import os
import sys

import uvicorn

from web.bind import BindRefused, assert_safe_bind


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the Quick Study web UI.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    try:
        assert_safe_bind(args.host)
    except BindRefused as exc:
        print(str(exc), file=sys.stderr)
        print("\n*** 启动已中止：没有令牌的公网绑定会被拒绝。***\n", file=sys.stderr)
        return 2
    os.environ["QUICK_STUDY_BIND"] = args.host
    uvicorn.run("webapp:app", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
