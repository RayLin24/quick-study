from __future__ import annotations

from web.render import build_cover_svg


def light_cover_svg(title: str) -> str:
    """Thin wrapper so callers can import a dedicated cover helper."""
    return build_cover_svg(title)
