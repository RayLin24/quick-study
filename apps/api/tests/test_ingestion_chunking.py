"""Chunking decides what a citation can point at.

A chunk that straddles two sections cites neither cleanly, and a chunk that splits a code
fence produces a quotation that will not compile. Both are structural properties, so they
are tested structurally rather than by output size alone.
"""

from __future__ import annotations

from app.ingestion.web.chunking import TextChunk, chunk_markdown, slugify

DOCUMENT = """# Install Guide

Run the installer with sudo.

## Requirements

Python 3.12 or newer is required.

### Operating systems

Linux and macOS are supported.

## Configuration

Set the supervisor socket path.
"""


def test_a_document_is_split_at_its_headings() -> None:
    chunks = chunk_markdown(DOCUMENT)

    assert [chunk.heading for chunk in chunks] == [
        "Install Guide",
        "Requirements",
        "Operating systems",
        "Configuration",
    ]


def test_each_chunk_records_the_heading_path_that_leads_to_it() -> None:
    chunks = chunk_markdown(DOCUMENT)
    paths = {chunk.heading: chunk.heading_path for chunk in chunks}

    assert paths["Requirements"] == "Install Guide > Requirements"
    assert paths["Operating systems"] == "Install Guide > Requirements > Operating systems"
    assert paths["Configuration"] == "Install Guide > Configuration"


def test_chunks_are_numbered_from_zero_in_document_order() -> None:
    chunks = chunk_markdown(DOCUMENT)

    assert [chunk.ordinal for chunk in chunks] == [0, 1, 2, 3]


def test_the_offsets_point_back_into_the_document_they_came_from() -> None:
    for chunk in chunk_markdown(DOCUMENT):
        assert DOCUMENT[chunk.char_start : chunk.char_end].strip() == chunk.text.strip()


def test_each_chunk_carries_an_anchor_derived_from_its_heading() -> None:
    chunks = chunk_markdown(DOCUMENT)

    assert [chunk.anchor for chunk in chunks] == [
        "install-guide",
        "requirements",
        "operating-systems",
        "configuration",
    ]


def test_repeated_headings_get_distinct_anchors() -> None:
    chunks = chunk_markdown("## Setup\n\nOne.\n\n## Setup\n\nTwo.\n")

    assert [chunk.anchor for chunk in chunks] == ["setup", "setup-2"]


def test_every_chunk_is_addressed_by_the_hash_of_its_text() -> None:
    chunks = chunk_markdown(DOCUMENT)

    assert len({chunk.sha256 for chunk in chunks}) == len(chunks)
    assert all(len(chunk.sha256) == 64 for chunk in chunks)


def test_content_before_the_first_heading_is_not_lost() -> None:
    chunks = chunk_markdown("Preamble text that precedes every heading.\n\n# Title\n\nBody.\n")

    assert "Preamble text" in chunks[0].text
    assert chunks[0].heading == ""


def test_an_oversized_section_is_split_into_several_chunks_that_keep_the_heading() -> None:
    long_section = "# Title\n\n" + "\n\n".join(f"Sentence {i} of the body." for i in range(200))

    chunks = chunk_markdown(long_section, max_characters=600)

    assert len(chunks) > 1
    assert all(chunk.heading == "Title" for chunk in chunks)
    assert all(len(chunk.text) <= 600 for chunk in chunks)


def test_split_parts_of_one_section_still_get_distinct_anchors() -> None:
    long_section = "# Title\n\n" + "\n\n".join(f"Sentence {i} of the body." for i in range(200))

    anchors = [chunk.anchor for chunk in chunk_markdown(long_section, max_characters=600)]

    assert len(set(anchors)) == len(anchors)


def test_a_fenced_code_block_is_never_split_across_chunks() -> None:
    code = "\n".join(f"line_{index} = {index}" for index in range(60))
    document = f"# Title\n\nIntro.\n\n```python\n{code}\n```\n\nAfter.\n"

    chunks = chunk_markdown(document, max_characters=400)

    for chunk in chunks:
        assert chunk.text.count("```") % 2 == 0


def test_a_heading_inside_a_code_fence_does_not_start_a_new_chunk() -> None:
    document = "# Title\n\n```bash\n# not a heading\necho hi\n```\n\nAfter.\n"

    chunks = chunk_markdown(document)

    assert [chunk.heading for chunk in chunks] == ["Title"]


def test_an_empty_document_yields_no_chunks() -> None:
    assert chunk_markdown("") == ()
    assert chunk_markdown("   \n\n  ") == ()


def test_a_section_with_only_a_heading_still_produces_a_citable_chunk() -> None:
    chunks = chunk_markdown("# Title\n\n## Empty\n\n## Filled\n\nBody.\n")

    assert [chunk.heading for chunk in chunks] == ["Title", "Empty", "Filled"]


def test_the_token_count_is_recorded_for_budgeting_evidence_packs() -> None:
    chunk = chunk_markdown("# Title\n\nOne two three four five.\n")[0]

    assert chunk.token_count > 0


def test_chunks_are_immutable_records() -> None:
    chunk = chunk_markdown(DOCUMENT)[0]

    assert isinstance(chunk, TextChunk)
    try:
        chunk.text = "tampered"  # type: ignore[misc]
    except (AttributeError, TypeError):
        return
    raise AssertionError("a chunk must not be mutable after it is produced")


class TestSlugify:
    def test_it_lowercases_and_hyphenates(self) -> None:
        assert slugify("Getting Started With Quick Study") == "getting-started-with-quick-study"

    def test_it_drops_punctuation_and_collapses_separators(self) -> None:
        assert slugify("  What's new? (2026)  ") == "whats-new-2026"

    def test_it_keeps_non_ascii_words_readable(self) -> None:
        assert slugify("安装指南") == "安装指南"

    def test_it_never_returns_an_empty_anchor(self) -> None:
        assert slugify("---") == "section"
        assert slugify("") == "section"
