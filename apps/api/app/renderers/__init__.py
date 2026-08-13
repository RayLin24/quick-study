"""Render a tutorial document into its deliverable forms.

Both the Markdown bundle and the published page are produced from the same canonical
model in :mod:`app.tutorial.schema`, because two independent renderings of one tutorial
drift and a reader cannot tell which one the citations belong to.
"""

from app.renderers.markdown import render_markdown_bundle

__all__ = ["render_markdown_bundle"]
