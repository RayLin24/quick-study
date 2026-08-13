"""Rendering a page in a browser, when and only when static extraction was not enough.

A browser executes whatever the page ships, so this is the largest attack surface in the
system and the most tightly bounded: entered only on evidence that static extraction
failed, capped per crawl, confined to a container with no secrets, no host mounts and a
read-only filesystem, and restricted to one egress path whose every request is re-checked.
"""
