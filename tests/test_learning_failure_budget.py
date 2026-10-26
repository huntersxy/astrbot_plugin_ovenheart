"""Review keeps reserved time; schema repair remains evidence-bound and atomic."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from astrbot_plugin_ovenheart import main
from tests.test_runtime import completion, sources

pytest_plugins = ["tests.test_runtime"]


async def test_extraction_timeout_preserves_sources_and_records_phase_budget(
    plugin, monkeypatch
):
    scope = await sources(plugin)
    plugin.settings["learning_review_enabled"] = True
    monkeypatch.setattr(main, "LEARNING_TIMEOUT_SECONDS", 0.3)

    async def hang(**kwargs):
        await asyncio.Event().wait()

    plugin.context.llm_generate.side_effect = hang
    result = await asyncio.wait_for(plugin.learn(scope), 2)
    assert result["status"] == "error" and "提取" in result["message"]
    state = await plugin.store.call("state", scope)
    failure = state["runs"][0]["review"]["failure"]
    assert failure["phase"] == "extraction"
    assert failure["extraction_budget_seconds"] == pytest.approx(0.2)
    assert failure["review_reserved_seconds"] == pytest.approx(0.1)
    assert state["scope"]["pending"] == 2 and not state["traits"]


async def test_disabled_review_leaves_whole_budget_for_extraction(plugin):
    scope = await sources(plugin)
    plugin.settings["learning_review_enabled"] = False
    result = await plugin.learn(scope)
    assert result["status"] == "success"
    timing = (await plugin.store.call("state", scope))["runs"][0]["review"]["timing"]
    assert timing["review_reserved_seconds"] == 0
    assert timing["extraction_budget_seconds"] == timing["model_budget_seconds"] == 360


async def test_non_expression_examples_are_discarded_before_full_review(plugin):
    scope = await sources(plugin)
    plugin.settings["learning_review_enabled"] = True
    repairs = []
    reviews = []

    async def generate(**kwargs):
        payload = json.loads(kwargs["prompt"])
        if "candidates" in payload:
            reviews.append(payload["candidates"])
            return SimpleNamespace(
                completion_text=json.dumps(
                    {
                        "approved_ids": [
                            c["candidate_id"] for c in payload["candidates"]
                        ],
                        "flagged": [],
                    }
                )
            )
        normal = await completion(**kwargs)
        if payload["enabled_kinds"] == ["jargon"]:
            return normal
        if "correction_task" in payload:
            repairs.append(payload)
            assert "behavior" in payload["format_error"]
            assert (
                "examples" in payload["correction_task"]
                and "evidence_id" in payload["correction_task"]
            )
            return normal
        bad = json.loads(normal.completion_text)
        target = next(c for c in bad["traits"] if c["kind"] == "behavior")
        target["examples"] = [
            {"evidence_id": target["evidence_ids"][0], "text": "bad sample"}
        ]
        return SimpleNamespace(completion_text=json.dumps(bad))

    plugin.context.llm_generate.side_effect = generate
    result = await plugin.learn(scope)
    assert result["status"] == "success" and not repairs and len(reviews) == 1
    state = await plugin.store.call("state", scope)
    assert state["scope"]["pending"] == 0
    assert all(
        not t.get("examples") for t in state["traits"] if t["kind"] != "expression"
    )
