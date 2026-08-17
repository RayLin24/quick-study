"""LLM-backed outline and chapter nodes, driven by a scripted model."""

from __future__ import annotations

import json

from llm_support import FakeChatModel, reply

from app.db.models.enums import RunPhase
from app.workflows.tutorial.generation import build_tutorial_nodes
from app.workflows.tutorial.recording import NodeCall
from app.workflows.tutorial.state import new_tutorial_state, tutorial_request

OUTLINE = {
    "title": "Flask 入门",
    "summary": "从路由写到第一个应用",
    "chapters": [
        {
            "slug": "what-flask-is",
            "title": "Flask 是什么",
            "intent": "说明微框架的定位",
            "questions": ["Flask 解决什么问题?"],
            "path_hints": ["https://github.com/pallets/flask"],
            "symbols": [],
        }
    ],
}

CHAPTER = {
    "title": "Flask 是什么",
    "summary": "一个用 Python 写网站的微框架",
    "markdown": "# Flask 是什么\n\nFlask 是一个微框架。\n",
}


def _call() -> NodeCall:
    return NodeCall(
        node="outline",
        phase=RunPhase.OUTLINE,
        run_id="run-1",
        project_id="proj-1",
        pipeline_version="1",
        input_hash="a" * 64,
        idempotency_key="k",
        attempt=1,
    )


def test_the_outline_node_uses_the_model_proposal() -> None:
    model = FakeChatModel(script=[reply(json.dumps(OUTLINE))])
    nodes = build_tutorial_nodes(model)
    state = new_tutorial_state(
        run_id="run-1",
        project_id="proj-1",
        thread_id="thread-1",
        request=tutorial_request(
            title="Flask 入门",
            sources=[{"kind": "github_repo", "locator": "https://github.com/pallets/flask"}],
        ),
    )

    outcome = nodes.outline(state, _call())

    assert outcome.update["outline"]["title"] == "Flask 入门"
    assert outcome.update["outline"]["chapters"][0]["slug"] == "what-flask-is"
    assert outcome.model == "fake-model"
    assert outcome.tokens_in == 100


def test_the_chapter_node_stores_generated_markdown() -> None:
    model = FakeChatModel(script=[reply(json.dumps(CHAPTER))])
    nodes = build_tutorial_nodes(model)
    state = new_tutorial_state(
        run_id="run-1",
        project_id="proj-1",
        thread_id="thread-1",
        request=tutorial_request(title="Flask 入门"),
    )
    state["outline"] = {
        "version": 1,
        "title": "Flask 入门",
        "summary": "",
        "chapters": [
            {"slug": "what-flask-is", "title": "Flask 是什么", "ordinal": 0, "summary": "定位"}
        ],
    }

    outcome = nodes.chapters(state, _call())

    draft = outcome.update["chapters"]["what-flask-is"]
    assert "Flask 是一个微框架" in draft["markdown"]
    assert draft["status"] == "drafted"
