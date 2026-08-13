"""robots.txt, implemented rather than approximated.

The standard library's parser predates RFC 9309 and does not support ``*`` or ``$`` inside
a path, so a rule like ``Disallow: /*/draft`` silently matches nothing and the crawler
fetches what the site asked it not to. The rules here follow RFC 9309: groups are merged
per user agent, the longest matching rule wins, and a tie goes to ``Allow``.

The failure modes are asymmetric on purpose. A site with no robots.txt has not restricted
anything, so a 4xx means "crawl". A site that could not answer has told us nothing, so a
5xx or a transport failure means "do not crawl" until it can.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final, Self

from app.ingestion.web.fetcher import USER_AGENT_PRODUCT, FetchError, SafeFetcher
from app.ingestion.web.safety import SsrfBlocked
from app.ingestion.web.urls import CanonicalUrl, UnsafeUrl, normalize_url

#: Google serves and parses at most 500 KiB; anything beyond that is not a policy document.
MAX_ROBOTS_BYTES: Final = 500 * 1024

ROBOTS_PATH: Final = "/robots.txt"

_DISALLOW: Final = "disallow"
_ALLOW: Final = "allow"


@dataclass(frozen=True, slots=True)
class RobotsRule:
    """One ``Allow`` or ``Disallow`` line, compiled to a matcher."""

    allow: bool
    pattern: str
    matcher: re.Pattern[str]

    @property
    def specificity(self) -> int:
        """How specific the rule is. RFC 9309 measures this as the pattern's length."""
        return len(self.pattern)

    def matches(self, path: str) -> bool:
        return self.matcher.match(path) is not None


@dataclass(frozen=True, slots=True)
class RobotsPolicy:
    """The rules that apply to one crawler on one origin."""

    rules: tuple[RobotsRule, ...] = ()
    sitemaps: tuple[CanonicalUrl, ...] = ()
    crawl_delay: float | None = None
    #: Set when the site could not tell us its policy, which is not the same as no policy.
    unavailable: bool = False

    @classmethod
    def allow_all(cls) -> Self:
        return cls()

    @classmethod
    def deny_all(cls) -> Self:
        return cls(rules=(_compile_rule(allow=False, pattern="/"),), unavailable=True)

    def can_fetch(self, url: str | CanonicalUrl) -> bool:
        """Decide one address. Unmatched paths are allowed, as the standard requires."""
        path = _match_target(url)
        best: RobotsRule | None = None
        for rule in self.rules:
            if not rule.matches(path):
                continue
            if best is None or _outranks(rule, best):
                best = rule
        return best.allow if best is not None else True


def robots_url_for(url: CanonicalUrl) -> CanonicalUrl:
    """Return the robots.txt that governs ``url``. Policy is per origin, including port."""
    return CanonicalUrl(scheme=url.scheme, host=url.host, port=url.port, path=ROBOTS_PATH)


def parse_robots(text: str, *, user_agent: str) -> RobotsPolicy:
    """Compile the group that applies to ``user_agent``.

    Sitemap lines are global to the file, so they are collected regardless of which group
    they appear in — including before any ``User-agent`` line.
    """
    token = _product_token(user_agent)
    groups: list[_Group] = []
    current: _Group | None = None
    accepting_agents = False
    sitemaps: list[CanonicalUrl] = []

    for raw_line in text.splitlines():
        field, value = _split_line(raw_line)
        if field is None:
            continue
        if field == "sitemap":
            _append_sitemap(sitemaps, value)
            continue
        if field == "user-agent":
            if current is None or not accepting_agents:
                current = _Group()
                groups.append(current)
                accepting_agents = True
            current.agents.append(value.lower())
            continue
        if current is None:
            # A rule that precedes every ``User-agent`` line belongs to no group.
            continue
        accepting_agents = False
        if field in (_ALLOW, _DISALLOW):
            current.directives.append((field, value))
        elif field == "crawl-delay":
            current.crawl_delay = _parse_delay(value)

    selected = _select_group(groups, token)
    if selected is None:
        return RobotsPolicy(sitemaps=tuple(sitemaps))
    # An empty value is the documented way to say the directive constrains nothing.
    rules = tuple(
        _compile_rule(allow=name == _ALLOW, pattern=value)
        for name, value in selected.directives
        if value
    )
    return RobotsPolicy(
        rules=rules, sitemaps=tuple(sitemaps), crawl_delay=selected.crawl_delay
    )


def fetch_robots(
    fetcher: SafeFetcher,
    url: CanonicalUrl,
    *,
    user_agent: str = USER_AGENT_PRODUCT,
) -> RobotsPolicy:
    """Retrieve and compile the policy for the origin of ``url``.

    Anything other than a clean answer resolves conservatively: absent means allowed,
    unreachable means denied.
    """
    try:
        response = fetcher.fetch(robots_url_for(url))
    except (FetchError, SsrfBlocked):
        return RobotsPolicy.deny_all()

    if response.status_code >= 500:
        return RobotsPolicy.deny_all()
    if not response.ok:
        return RobotsPolicy.allow_all()
    text = response.content[:MAX_ROBOTS_BYTES].decode(
        response.charset or "utf-8", errors="replace"
    )
    return parse_robots(text, user_agent=user_agent)


@dataclass(slots=True)
class _Group:
    agents: list[str] = field(default_factory=list)
    directives: list[tuple[str, str]] = field(default_factory=list)
    crawl_delay: float | None = None


def _split_line(raw_line: str) -> tuple[str | None, str]:
    line = raw_line.split("#", 1)[0].strip()
    if not line or ":" not in line:
        return None, ""
    field, _, value = line.partition(":")
    return field.strip().lower(), value.strip()


def _product_token(user_agent: str) -> str:
    """Reduce ``QuickStudyBot/0.1 (+https://…)`` to ``quickstudybot``."""
    return user_agent.split("/")[0].strip().lower()


def _select_group(groups: list[_Group], token: str) -> _Group | None:
    """Pick the most specific group that names this crawler, else the catch-all."""
    best: _Group | None = None
    best_length = -1
    wildcard: _Group | None = None
    for group in groups:
        for agent in group.agents:
            if agent == "*":
                wildcard = wildcard or group
            elif token.startswith(agent) and len(agent) > best_length:
                best, best_length = group, len(agent)
    return best or wildcard


def _compile_rule(*, allow: bool, pattern: str) -> RobotsRule:
    return RobotsRule(allow=allow, pattern=pattern, matcher=_compile_pattern(pattern))


def _compile_pattern(pattern: str) -> re.Pattern[str]:
    """Translate a robots path pattern into an anchored regular expression.

    ``*`` stands for any run of characters and a trailing ``$`` anchors the end; every
    other character is literal, which is why the pattern is escaped first.
    """
    body = pattern
    anchored = body.endswith("$")
    if anchored:
        body = body[:-1]
    expression = "".join(
        ".*" if part == "*" else re.escape(part) for part in _split_wildcards(body)
    )
    return re.compile(f"{expression}$" if anchored else expression)


def _split_wildcards(pattern: str) -> list[str]:
    parts: list[str] = []
    literal: list[str] = []
    for character in pattern:
        if character == "*":
            if literal:
                parts.append("".join(literal))
                literal = []
            parts.append("*")
        else:
            literal.append(character)
    if literal:
        parts.append("".join(literal))
    return parts


def _match_target(url: str | CanonicalUrl) -> str:
    """Return the path and query a rule is matched against, in one canonical encoding."""
    canonical = url if isinstance(url, CanonicalUrl) else normalize_url(str(url))
    return f"{canonical.path}?{canonical.query}" if canonical.query else canonical.path


def _outranks(candidate: RobotsRule, incumbent: RobotsRule) -> bool:
    if candidate.specificity != incumbent.specificity:
        return candidate.specificity > incumbent.specificity
    return candidate.allow and not incumbent.allow


def _append_sitemap(sitemaps: list[CanonicalUrl], value: str) -> None:
    try:
        sitemaps.append(normalize_url(value))
    except UnsafeUrl:
        return


def _parse_delay(value: str) -> float | None:
    try:
        delay = float(value)
    except ValueError:
        return None
    return delay if delay >= 0 else None
