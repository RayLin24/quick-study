"""Optional serial transition polish. Default off — does not rewrite chapters in parallel."""

from __future__ import annotations

import re

HEAD_RE = re.compile(r"^# .+$", re.M)


def polish_transitions(chapters: list[str], _order: list | None = None, *, call=None) -> list[str]:
    """Insert a short handoff after each chapter heading except the first."""
    out = []
    for i, text in enumerate(chapters):
        body = text or ""
        if i == 0:
            out.append(body)
            continue
        if "承接上一章" in body or "上一课" in body:
            out.append(body)
            continue
        note = f"> 承接上一章，继续往下看本章要点。\n"
        if call:
            note = call(
                f"Write two short sentences in the same language connecting to the next chapter.\n\n{body[:400]}",
                use_cache=False,
            )
            note = f"> {note.strip()}\n"
        match = HEAD_RE.search(body)
        if match:
            start = match.end()
            body = body[:start] + "\n\n" + note + body[start:].lstrip("\n")
        else:
            body = note + body
        out.append(body)
    return out
