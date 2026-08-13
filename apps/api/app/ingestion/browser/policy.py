"""When a page is worth a browser.

Rendering costs a container start, so the decision is made on evidence rather than
optimism: only a page whose static extraction demonstrably came back thin is retried. A
page that read fine has nothing to gain, and a page with no body at all has no script for
a browser to run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.ingestion.web.extract import ExtractedDocument, needs_browser_render

#: A crawl that wants to render more pages than this is not a documentation site.
DEFAULT_MAX_RENDERS: Final = 25


@dataclass(slots=True)
class RenderBudget:
    """How many renders one crawl may still spend."""

    max_renders: int = DEFAULT_MAX_RENDERS
    spent: int = 0

    @property
    def exhausted(self) -> bool:
        return self.spent >= self.max_renders

    def spend(self) -> None:
        self.spent += 1


def should_render(document: ExtractedDocument, budget: RenderBudget) -> bool:
    """Whether ``document`` should be retried in the isolated browser."""
    return not budget.exhausted and needs_browser_render(document)
