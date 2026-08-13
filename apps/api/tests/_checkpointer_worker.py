"""Probe graphs and the out-of-process worker used by the checkpointer contract tests.

This module is imported by the tests and also executed as a script: the recovery test
starts it as a real subprocess and kills it while a node is running, which is the only
way to prove that a hard-killed worker leaves a resumable checkpoint behind. It is
deliberately not named ``test_*`` so pytest does not collect it.
"""

from __future__ import annotations

import operator
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


class ProbeState(TypedDict):
    visited: Annotated[list[str], operator.add]
    approved: bool


def _record(path: Path | None, line: str) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{line}\n")


def build_approval_probe(effects: Path | None = None) -> StateGraph:
    """A graph that stops for a human between two ordinary nodes."""

    def first(state: ProbeState) -> dict[str, Any]:
        _record(effects, "first")
        return {"visited": ["first"]}

    def gate(state: ProbeState) -> dict[str, Any]:
        decision = interrupt({"question": "approve?"})
        _record(effects, "gate")
        return {"visited": ["gate"], "approved": bool(decision["approved"])}

    def last(state: ProbeState) -> dict[str, Any]:
        _record(effects, "last")
        return {"visited": ["last"]}

    builder = StateGraph(ProbeState)
    builder.add_node("first", first)
    builder.add_node("gate", gate)
    builder.add_node("last", last)
    builder.add_edge(START, "first")
    builder.add_edge("first", "gate")
    builder.add_edge("gate", "last")
    builder.add_edge("last", END)
    return builder


def build_stall_probe(
    marker: Path | None = None,
    effects: Path | None = None,
    middle: Callable[[ProbeState], dict[str, Any]] | None = None,
) -> StateGraph:
    """A graph whose middle node hangs, so the process can be killed inside it."""

    def first(state: ProbeState) -> dict[str, Any]:
        _record(effects, "first")
        return {"visited": ["first"]}

    def stall(state: ProbeState) -> dict[str, Any]:
        _record(effects, "stall-entered")
        if marker is not None:
            marker.write_text("running", encoding="utf-8")
        time.sleep(600)
        return {"visited": ["stall"]}

    def last(state: ProbeState) -> dict[str, Any]:
        _record(effects, "last")
        return {"visited": ["last"]}

    builder = StateGraph(ProbeState)
    builder.add_node("first", first)
    builder.add_node("stall", middle or stall)
    builder.add_node("last", last)
    builder.add_edge(START, "first")
    builder.add_edge("first", "stall")
    builder.add_edge("stall", "last")
    builder.add_edge("last", END)
    return builder


def main() -> None:
    url, thread_id, marker, effects = sys.argv[1:5]

    from app.workflows.checkpointing import MySQLCheckpointerProvider
    from app.workflows.tutorial.runner import DURABILITY

    provider = MySQLCheckpointerProvider(url)
    with provider.checkpointer() as checkpointer:
        graph = build_stall_probe(Path(marker), Path(effects)).compile(checkpointer=checkpointer)
        # Deliberately the runner's own setting: the recovery test is what proves it is
        # strong enough, so weakening it there has to fail here.
        graph.invoke(
            {"visited": [], "approved": False},
            {"configurable": {"thread_id": thread_id}},
            durability=DURABILITY,
        )


if __name__ == "__main__":
    main()
