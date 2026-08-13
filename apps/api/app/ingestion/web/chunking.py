"""Cutting a document into the units a citation can point at.

Chunk boundaries are chosen structurally, not by character count: a chunk is a section of
the document, identified by the heading path that leads to it, so a reference resolves to
something a reader can find on the page. Only when a section is too large for the evidence
budget is it split further, and never through a fenced code block — a quotation of half a
code block is worse than no quotation at all.

Offsets are absolute into the Markdown the chunk came from, so a citation can be verified
against the stored artifact rather than trusted.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Final

#: Roughly a screen of prose: large enough to carry an argument, small enough that several
#: fit in one evidence pack.
DEFAULT_MAX_CHARACTERS: Final = 1500

_ATX_HEADING: Final = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE: Final = re.compile(r"^\s*(```|~~~)")
_WORDS: Final = re.compile(r"\w+", re.UNICODE)
_NON_SLUG: Final = re.compile(r"[^\w\s-]", re.UNICODE)
_SLUG_SEPARATORS: Final = re.compile(r"[\s_-]+", re.UNICODE)

HEADING_PATH_SEPARATOR: Final = " > "
FALLBACK_ANCHOR: Final = "section"


@dataclass(frozen=True, slots=True)
class TextChunk:
    """One citable slice of a document."""

    ordinal: int
    heading: str
    heading_path: str
    anchor: str
    text: str
    char_start: int
    char_end: int
    token_count: int
    sha256: str


def chunk_markdown(
    markdown: str,
    *,
    max_characters: int = DEFAULT_MAX_CHARACTERS,
) -> tuple[TextChunk, ...]:
    """Split ``markdown`` into section-aligned chunks, in document order."""
    lines = _scan_lines(markdown)
    if not lines:
        return ()
    fenced = _fence_states(lines)
    sections = _sections(lines, fenced)

    chunks: list[TextChunk] = []
    anchors = _AnchorAllocator()
    for section in sections:
        for start, end in _split_section(markdown, lines, fenced, section, max_characters):
            text = markdown[start:end].strip()
            if not text:
                continue
            chunks.append(
                TextChunk(
                    ordinal=len(chunks),
                    heading=section.heading,
                    heading_path=section.heading_path,
                    anchor=anchors.allocate(section.heading),
                    text=text,
                    char_start=start,
                    char_end=end,
                    token_count=len(_WORDS.findall(text)),
                    sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                )
            )
    return tuple(chunks)


def slugify(value: str) -> str:
    """Turn a heading into a stable anchor, never returning an empty one."""
    text = _NON_SLUG.sub("", unicodedata.normalize("NFKC", value).strip().lower())
    return _SLUG_SEPARATORS.sub("-", text).strip("-") or FALLBACK_ANCHOR


@dataclass(frozen=True, slots=True)
class _Line:
    start: int
    end: int
    text: str


@dataclass(frozen=True, slots=True)
class _Section:
    heading: str
    heading_path: str
    first_line: int
    last_line: int


class _AnchorAllocator:
    """Hands out anchors, suffixing repeats so every chunk has a distinct one."""

    def __init__(self) -> None:
        self._used: dict[str, int] = {}

    def allocate(self, heading: str) -> str:
        base = slugify(heading)
        count = self._used.get(base, 0) + 1
        self._used[base] = count
        return base if count == 1 else f"{base}-{count}"


def _scan_lines(markdown: str) -> list[_Line]:
    lines: list[_Line] = []
    offset = 0
    for raw in markdown.splitlines(keepends=True):
        content = raw.rstrip("\r\n")
        lines.append(_Line(offset, offset + len(content), content))
        offset += len(raw)
    return lines


def _fence_states(lines: list[_Line]) -> list[bool]:
    """Mark every line that is inside or delimits a fenced code block.

    Headings and blank lines inside a fence are code, not structure, so every later
    decision consults this instead of the line's own text.
    """
    states: list[bool] = []
    inside = False
    for line in lines:
        if _FENCE.match(line.text):
            states.append(True)
            inside = not inside
        else:
            states.append(inside)
    return states


def _sections(lines: list[_Line], fenced: list[bool]) -> list[_Section]:
    headings = [
        (index, match)
        for index, line in enumerate(lines)
        if not fenced[index] and (match := _ATX_HEADING.match(line.text))
    ]
    if not headings:
        return [_Section("", "", 0, len(lines) - 1)]

    sections: list[_Section] = []
    if headings[0][0] > 0:
        sections.append(_Section("", "", 0, headings[0][0] - 1))

    ancestors: list[tuple[int, str]] = []
    for position, (index, match) in enumerate(headings):
        level = len(match.group(1))
        title = match.group(2).strip()
        while ancestors and ancestors[-1][0] >= level:
            ancestors.pop()
        ancestors.append((level, title))
        last_line = (
            headings[position + 1][0] - 1 if position + 1 < len(headings) else len(lines) - 1
        )
        sections.append(
            _Section(
                heading=title,
                heading_path=HEADING_PATH_SEPARATOR.join(name for _, name in ancestors),
                first_line=index,
                last_line=last_line,
            )
        )
    return sections


def _split_section(
    markdown: str,
    lines: list[_Line],
    fenced: list[bool],
    section: _Section,
    max_characters: int,
) -> list[tuple[int, int]]:
    start = lines[section.first_line].start
    end = lines[section.last_line].end
    if end - start <= max_characters:
        return [(start, end)]
    return _pack(markdown, _blocks(lines, fenced, section), max_characters)


def _blocks(
    lines: list[_Line],
    fenced: list[bool],
    section: _Section,
) -> list[tuple[int, int, bool]]:
    """Group a section's lines into paragraph-like blocks that are never split further."""
    blocks: list[tuple[int, int, bool]] = []
    start: int | None = None
    end = 0
    holds_fence = False
    for index in range(section.first_line, section.last_line + 1):
        line = lines[index]
        if not line.text.strip() and not fenced[index]:
            if start is not None:
                blocks.append((start, end, holds_fence))
                start, holds_fence = None, False
            continue
        if start is None:
            start = line.start
        end = line.end
        holds_fence = holds_fence or fenced[index]
    if start is not None:
        blocks.append((start, end, holds_fence))
    return blocks


def _pack(
    markdown: str,
    blocks: list[tuple[int, int, bool]],
    max_characters: int,
) -> list[tuple[int, int]]:
    """Fill chunks with whole blocks, keeping every code fence intact."""
    parts: list[tuple[int, int]] = []
    current: tuple[int, int] | None = None
    for start, end, holds_fence in blocks:
        if current is not None and end - current[0] > max_characters:
            parts.append(current)
            current = None
        if current is not None:
            current = (current[0], end)
            continue
        if end - start <= max_characters:
            current = (start, end)
        elif holds_fence:
            # A code block over budget is emitted whole: half a fence cites nothing.
            parts.append((start, end))
        else:
            parts.extend(_hard_split(markdown, start, end, max_characters))
    if current is not None:
        parts.append(current)
    return parts


def _hard_split(
    markdown: str,
    start: int,
    end: int,
    max_characters: int,
) -> list[tuple[int, int]]:
    """Last resort for a single block over budget: cut at whitespace."""
    pieces: list[tuple[int, int]] = []
    cursor = start
    while end - cursor > max_characters:
        limit = cursor + max_characters
        boundary = markdown.rfind(" ", cursor, limit)
        cut = boundary if boundary > cursor else limit
        pieces.append((cursor, cut))
        cursor = cut
    if cursor < end:
        pieces.append((cursor, end))
    return pieces
