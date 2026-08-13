"""Documentation sites repeat themselves; the corpus must not.

Exact duplicates come from mirrored URLs and trailing-slash variants. Near duplicates come
from versioned copies of the same page, where only a version string or a footer differs.
Both waste the evidence budget and skew retrieval, so both are detected before indexing.
"""

from __future__ import annotations

from app.ingestion.web.dedupe import (
    DuplicateIndex,
    DuplicateKind,
    content_fingerprint,
    content_sketch,
    jaccard_similarity,
    shingles,
)

GUIDE = (
    "Install the gateway service with the supervisor process. "
    "The supervisor reloads configuration without downtime and restarts failed workers. "
    "Run the installer as root and confirm the service is listening on port 8443."
)

OPERATORS = (
    "Kubernetes operators reconcile custom resources by watching the API server "
    "and issuing patches until the observed state matches the declared state."
)


def similarity(left: str, right: str) -> float:
    return jaccard_similarity(content_sketch(left), content_sketch(right))


def test_the_fingerprint_ignores_whitespace_and_case_but_not_words() -> None:
    assert content_fingerprint("Hello   World\n") == content_fingerprint("hello world")
    assert content_fingerprint("hello world") != content_fingerprint("hello worlds")


def test_the_fingerprint_is_a_sha256_digest() -> None:
    assert len(content_fingerprint(GUIDE)) == 64
    assert int(content_fingerprint(GUIDE), 16) >= 0


def test_shingles_keep_word_order() -> None:
    assert shingles("alpha beta gamma") == ["alpha beta", "beta gamma"]
    assert shingles("alpha beta gamma") != shingles("gamma beta alpha")


def test_identical_text_is_completely_similar() -> None:
    assert similarity(GUIDE, GUIDE) == 1.0


def test_a_one_word_edit_barely_moves_the_similarity() -> None:
    assert similarity(GUIDE, GUIDE.replace("port 8443", "port 9443")) >= 0.9


def test_unrelated_text_shares_almost_nothing() -> None:
    assert similarity(GUIDE, OPERATORS) < 0.2


def test_an_empty_document_is_similar_to_nothing() -> None:
    assert similarity("", GUIDE) == 0.0


def test_the_sketch_is_bounded_regardless_of_document_length() -> None:
    long_text = " ".join(f"word{index}" for index in range(20_000))

    assert len(content_sketch(long_text, sketch_size=256)) == 256


def test_a_truncated_sketch_still_recognises_a_near_copy() -> None:
    long_text = " ".join(f"word{index}" for index in range(20_000))
    edited = long_text.replace("word10 ", "changed ")

    assert jaccard_similarity(content_sketch(long_text), content_sketch(edited)) >= 0.9


class TestDuplicateIndex:
    def test_the_first_document_is_unique(self) -> None:
        index = DuplicateIndex()

        verdict = index.add("https://docs.example.test/a", GUIDE)

        assert verdict.kind is DuplicateKind.UNIQUE
        assert verdict.original_key is None

    def test_byte_identical_content_is_reported_as_an_exact_duplicate(self) -> None:
        index = DuplicateIndex()
        index.add("https://docs.example.test/a", GUIDE)

        verdict = index.add("https://docs.example.test/a/", GUIDE)

        assert verdict.kind is DuplicateKind.EXACT
        assert verdict.original_key == "https://docs.example.test/a"

    def test_content_differing_only_in_whitespace_is_still_exact(self) -> None:
        index = DuplicateIndex()
        index.add("a", GUIDE)

        assert index.add("b", f"  {GUIDE.upper()}  ").kind is DuplicateKind.EXACT

    def test_a_versioned_copy_of_the_same_page_is_a_near_duplicate(self) -> None:
        index = DuplicateIndex()
        index.add("https://docs.example.test/v1/guide", GUIDE)

        verdict = index.add(
            "https://docs.example.test/v2/guide", GUIDE.replace("8443", "9443")
        )

        assert verdict.kind is DuplicateKind.NEAR
        assert verdict.original_key == "https://docs.example.test/v1/guide"
        assert verdict.similarity is not None and verdict.similarity >= 0.85

    def test_a_genuinely_different_page_is_kept(self) -> None:
        index = DuplicateIndex()
        index.add("https://docs.example.test/install", GUIDE)

        verdict = index.add("https://docs.example.test/operators", OPERATORS)

        assert verdict.kind is DuplicateKind.UNIQUE

    def test_text_too_short_to_judge_is_never_called_a_near_duplicate(self) -> None:
        """Two short pages share most of their tokens by accident, not by copying."""
        index = DuplicateIndex()
        index.add("a", "Not found.")

        assert index.add("b", "Not found!").kind is DuplicateKind.UNIQUE

    def test_the_near_duplicate_threshold_is_configurable(self) -> None:
        strict = DuplicateIndex(near_threshold=1.0)
        strict.add("a", GUIDE)

        assert strict.add("b", GUIDE.replace("8443", "9443")).kind is DuplicateKind.UNIQUE

    def test_the_index_reports_which_keys_it_decided_to_keep(self) -> None:
        index = DuplicateIndex()
        index.add("a", GUIDE)
        index.add("b", GUIDE)
        index.add("c", OPERATORS)

        assert index.kept_keys == ("a", "c")

    def test_re_adding_the_same_key_does_not_grow_the_index(self) -> None:
        index = DuplicateIndex()
        index.add("a", GUIDE)

        assert index.add("a", GUIDE).kind is DuplicateKind.EXACT
        assert index.kept_keys == ("a",)

    def test_a_near_duplicate_is_attributed_to_the_closest_page_seen(self) -> None:
        index = DuplicateIndex()
        index.add("unrelated", OPERATORS)
        index.add("original", GUIDE)

        verdict = index.add("copy", GUIDE.replace("8443", "9443"))

        assert verdict.original_key == "original"
