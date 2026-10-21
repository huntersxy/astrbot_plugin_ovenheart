"""Verify parallel extraction joins, cancellation and single atomic commits."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from astrbot_plugin_ovenheart.learning import LEARNING_REVIEW_SYSTEM
from astrbot_plugin_ovenheart.settings import LEARNING_TIMEOUT_SECONDS
from tests.test_runtime import completion, sources

pytest_plugins = ["tests.test_runtime"]


async def test_extraction_overlaps_but_review_waits_and_receives_stable_order(
    plugin, monkeypatch
):
    scope = await sources(plugin)
    plugin.settings["learning_review_enabled"] = True
    entered = set()
    finished = set()
    both_started = asyncio.Event()
    word_finished = asyncio.Event()
    release_habits = asyncio.Event()
    review_seen = []
    deadlines = []
    original_pass = plugin._learn_pass
    original_review = plugin._review_learning

    async def observe_pass(payload, batch, settings, known, provider_id, deadline):
        deadlines.append(deadline)
        return await original_pass(
            payload, batch, settings, known, provider_id, deadline
        )

    async def observe_review(traits, base, provider_id, deadline):
        deadlines.append(deadline)
        return await original_review(traits, base, provider_id, deadline)

    monkeypatch.setattr(plugin, "_learn_pass", observe_pass)
    monkeypatch.setattr(plugin, "_review_learning", observe_review)

    async def generate(**kwargs):
        payload = json.loads(kwargs["prompt"])
        if kwargs["system_prompt"] == LEARNING_REVIEW_SYSTEM:
            assert finished == {"habits", "word"}
            review_seen.append(payload)
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
        phase = "word" if payload["enabled_kinds"] == ["jargon"] else "habits"
        entered.add(phase)
        if len(entered) == 2:
            both_started.set()
        await both_started.wait()
        if phase == "habits":
            await release_habits.wait()
        result = await completion(**kwargs)
        finished.add(phase)
        if phase == "word":
            word_finished.set()
        return result

    plugin.context.llm_generate = generate
    started = time.monotonic()
    task = asyncio.create_task(plugin.learn(scope))
    try:
        await asyncio.wait_for(word_finished.wait(), 2)
        state = await plugin.store.call("state", scope)
        assert not state["traits"] and state["scope"]["pending"] == 2
        assert not review_seen and finished == {"word"}
        release_habits.set()
        result = await asyncio.wait_for(task, 2)
    finally:
        release_habits.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert result["status"] == "success" and result["count"] == 4
    assert len(deadlines) == 3 and len(set(deadlines)) == 1
    assert started + LEARNING_TIMEOUT_SECONDS <= deadlines[0]
    assert deadlines[0] <= time.monotonic() + LEARNING_TIMEOUT_SECONDS
    assert [c["kind"] for c in review_seen[0]["candidates"]] == [
        "personality",
        "expression",
        "behavior",
        "jargon",
    ]
    state = await plugin.store.call("state", scope)
    assert state["scope"]["pending"] == 0 and len(state["runs"]) == 1
    assert all(t["support_count"] == 1 for t in state["traits"])


@pytest.mark.parametrize("failing_phase", ["habits", "word"])
async def test_failed_extraction_cancels_and_awaits_the_other_phase_before_return(
    plugin, failing_phase
):
    scope = await sources(plugin)
    plugin.settings["learning_review_enabled"] = True
    entered = set()
    both_started = asyncio.Event()
    cancelled = asyncio.Event()
    calls = []

    async def generate(**kwargs):
        payload = json.loads(kwargs["prompt"])
        assert kwargs["system_prompt"] != LEARNING_REVIEW_SYSTEM
        phase = "word" if payload["enabled_kinds"] == ["jargon"] else "habits"
        calls.append(phase)
        entered.add(phase)
        if len(entered) == 2:
            both_started.set()
        await both_started.wait()
        if phase == failing_phase:
            raise RuntimeError("SECRET_PROVIDER_SENTINEL")
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    plugin.context.llm_generate = generate
    result = await asyncio.wait_for(plugin.learn(scope), 2)
    assert result["status"] == "error" and cancelled.is_set()
    assert "SECRET_PROVIDER_SENTINEL" not in result["message"]
    assert set(calls) == {"habits", "word"} and len(calls) == 2
    state = await plugin.store.call("state", scope)
    assert state["scope"]["pending"] == 2 and not state["traits"]
    assert state["runs"][0]["status"] == "error" and scope not in plugin.busy


async def test_cancelling_learning_awaits_both_extraction_requests(plugin):
    scope = await sources(plugin)
    entered = set()
    cancelled = set()
    both_started = asyncio.Event()

    async def generate(**kwargs):
        payload = json.loads(kwargs["prompt"])
        phase = "word" if payload["enabled_kinds"] == ["jargon"] else "habits"
        entered.add(phase)
        if len(entered) == 2:
            both_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.add(phase)

    plugin.context.llm_generate = generate
    task = asyncio.create_task(plugin.learn(scope))
    try:
        await asyncio.wait_for(both_started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert cancelled == {"habits", "word"} and scope not in plugin.busy
    state = await plugin.store.call("state", scope)
    assert not state["traits"] and state["scope"]["pending"] == 2
    assert state["runs"][0]["status"] == "error"
