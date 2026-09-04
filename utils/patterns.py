import fnmatch
import os
from typing import Iterable, Optional, Union

PatternSet = Union[str, Iterable[str], None]


def normalize_patterns(patterns: PatternSet) -> Optional[set[str]]:
    if not patterns:
        return None
    if isinstance(patterns, str):
        return {patterns}
    return {str(item) for item in patterns if str(item)}


def _posix_path(rel_path: str) -> str:
    return (rel_path or "").replace("\\", "/").lstrip("./")


def file_matches_any(rel_path: str, patterns: PatternSet) -> bool:
    """Match include/exclude globs against the relative path *or* the basename.

    `*.py` must hit nested files; `src/*.py` must hit GitHub crawl paths, not
    only a basename like `app.py`.
    """
    items = normalize_patterns(patterns)
    if not items:
        return False
    posix = _posix_path(rel_path)
    name = os.path.basename(posix)
    return any(fnmatch.fnmatch(posix, pat) or fnmatch.fnmatch(name, pat) for pat in items)


def should_include_file(
    rel_path: str,
    include_patterns: PatternSet = None,
    exclude_patterns: PatternSet = None,
) -> bool:
    include = normalize_patterns(include_patterns)
    exclude = normalize_patterns(exclude_patterns)
    if include and not file_matches_any(rel_path, include):
        return False
    if exclude and file_matches_any(rel_path, exclude):
        return False
    return True
