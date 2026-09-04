from __future__ import annotations


def build_issue_guide_prompt(title: str, body: str, *, comments: list[str] | None = None) -> str:
    extras = "\n".join(f"- {item}" for item in (comments or [])[:12])
    return (
        f"Turn this repository issue into a study guide.\n"
        f"Title: {title}\n\n"
        f"Body:\n{(body or '')[:4000]}\n\n"
        f"Comments:\n{extras or '- none'}\n\n"
        "Summarize the problem, point to likely files, and list questions a reader should answer."
    )
