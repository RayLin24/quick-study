"""Recognising the same page twice.

Exact duplicates are cheap: normalise the text and compare digests. Near duplicates are the
ones that cost something — versioned copies of a page where only a release number or a
footer differs — and they are found by comparing the sets of word bigrams two documents
share.

SimHash is the usual choice here and was tried first. It does not work at this scale: a
documentation page is a few hundred words, and with so few shingles the bit margins are
thin enough that a one-word edit moves the fingerprint about as far as an unrelated
document does. Comparing shingle sets directly has no such blind spot. To keep the
comparison cheap for large pages each document is reduced to a bottom-k sketch — the k
smallest shingle hashes — which estimates the Jaccard similarity of the full sets and is
exactly equal to it whenever a document has fewer than k shingles, as most pages do.

Short texts are excluded from near-duplicate matching entirely. Two error pages share
nearly all of their few tokens by coincidence, and calling them duplicates would silently
drop content.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

#: How many shingle hashes represent one document. Larger is more accurate and slower.
SKETCH_SIZE: Final = 256

#: Shared-shingle fraction at or above which two documents are the same document.
DEFAULT_NEAR_THRESHOLD: Final = 0.85

#: Below this many characters shingle overlap is coincidence rather than copying.
MIN_NEAR_DUPLICATE_CHARACTERS: Final = 200

#: Shingle width. Word pairs keep some word order, which single words throw away.
SHINGLE_SIZE: Final = 2

_WORDS: Final = re.compile(r"\w+", re.UNICODE)


class DuplicateKind(StrEnum):
    UNIQUE = "unique"
    EXACT = "exact"
    NEAR = "near"


@dataclass(frozen=True, slots=True)
class DuplicateVerdict:
    """What the index decided about one document, and what it matched."""

    kind: DuplicateKind
    original_key: str | None = None
    similarity: float | None = None

    @property
    def is_duplicate(self) -> bool:
        return self.kind is not DuplicateKind.UNIQUE


def normalise_text(text: str) -> str:
    """Collapse the differences that are not content: case and whitespace."""
    return " ".join(text.lower().split())


def content_fingerprint(text: str) -> str:
    """The exact-duplicate key: a digest of the normalised text."""
    return hashlib.sha256(normalise_text(text).encode("utf-8")).hexdigest()


def shingles(text: str, *, size: int = SHINGLE_SIZE) -> list[str]:
    """Return the overlapping word n-grams that stand for this document's content."""
    words = _WORDS.findall(normalise_text(text))
    if len(words) < size:
        return words
    return [" ".join(words[index : index + size]) for index in range(len(words) - size + 1)]


def content_sketch(text: str, *, sketch_size: int = SKETCH_SIZE) -> tuple[int, ...]:
    """Reduce a document to the ``sketch_size`` smallest of its shingle hashes.

    Taking the smallest hashes is a deterministic sample of the shingle set that is
    independent of document length, which is what makes two sketches comparable.
    """
    hashed = {
        int.from_bytes(hashlib.blake2b(shingle.encode("utf-8"), digest_size=8).digest())
        for shingle in shingles(text)
    }
    return tuple(sorted(hashed)[:sketch_size])


def jaccard_similarity(left: Sequence[int], right: Sequence[int]) -> float:
    """Estimate the shared fraction of two shingle sets from their sketches.

    The bottom-k estimator: take the smallest hashes of the combined sketches and measure
    how many of them both documents contain. When neither sketch was truncated this is the
    exact Jaccard similarity.
    """
    if not left or not right:
        return 0.0
    first, second = set(left), set(right)
    size = min(len(left), len(right))
    universe = sorted(first | second)[:size]
    if not universe:
        return 0.0
    shared = sum(1 for value in universe if value in first and value in second)
    return shared / len(universe)


class DuplicateIndex:
    """Accumulates documents and reports whether each one is worth keeping.

    Deliberately in-memory and per-snapshot: duplicate detection is a property of one
    crawl, and carrying it across snapshots would let an old page suppress a new one.
    """

    def __init__(
        self,
        *,
        near_threshold: float = DEFAULT_NEAR_THRESHOLD,
        min_near_duplicate_characters: int = MIN_NEAR_DUPLICATE_CHARACTERS,
    ) -> None:
        self._near_threshold = near_threshold
        self._min_characters = min_near_duplicate_characters
        self._by_fingerprint: dict[str, str] = {}
        self._sketches: list[tuple[str, tuple[int, ...]]] = []
        self._kept: list[str] = []

    @property
    def kept_keys(self) -> tuple[str, ...]:
        """The keys the index decided to keep, in the order they were offered."""
        return tuple(self._kept)

    def add(self, key: str, text: str) -> DuplicateVerdict:
        """Offer a document and learn whether it duplicates one already seen."""
        fingerprint = content_fingerprint(text)
        original = self._by_fingerprint.get(fingerprint)
        if original is not None:
            return DuplicateVerdict(DuplicateKind.EXACT, original_key=original, similarity=1.0)

        normalised = normalise_text(text)
        if len(normalised) >= self._min_characters:
            sketch = content_sketch(normalised)
            near = self._nearest(sketch)
            if near is not None:
                original_key, similarity = near
                self._by_fingerprint[fingerprint] = original_key
                return DuplicateVerdict(
                    DuplicateKind.NEAR, original_key=original_key, similarity=similarity
                )
            self._sketches.append((key, sketch))

        self._by_fingerprint[fingerprint] = key
        self._kept.append(key)
        return DuplicateVerdict(DuplicateKind.UNIQUE)

    def _nearest(self, sketch: tuple[int, ...]) -> tuple[str, float] | None:
        best: tuple[str, float] | None = None
        for key, known in self._sketches:
            similarity = jaccard_similarity(sketch, known)
            if similarity >= self._near_threshold and (best is None or similarity > best[1]):
                best = (key, similarity)
        return best
