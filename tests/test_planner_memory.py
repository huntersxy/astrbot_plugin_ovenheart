"""Native memory participates in the existing bounded decision stage."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from tests.test_participation import requests
from tests.test_runtime import event

pytest_plugins = ["tests.test_runtime", "tests.test_participation"]


@pytest.fixture
async def planner_memory(participant):
    recall = AsyncMock(
        return_value=[
            {
                "id": f"native-{i}",
                "content": "OLDER_SHARED_BACKGROUND" + "记" * 400,
                "backend": "amemorix",
                "metadata": {"participants": ["alice"]},
            }
            for i in range(5)
        ]
    )
    participant.room_memory.context = recall
    return participant, recall, {}


async def test_planner_uses_current_native_scope_with_bounded_context(planner_memory):
    plugin, recall, _ = planner_memory
    incoming = event("native-memory", text="接着上次说")
    assert len(await requests(plugin, incoming)) == 1
    recall.assert_awaited_once()
    scope = incoming.get_extra("ovenheart_decision")["scope"]
    assert recall.await_args.args[:3] == (incoming, scope, "default")
    payload = json.loads(plugin.context.llm_generate.await_args.kwargs["prompt"])
    assert len(payload["memory_context"]) == 3
    assert all(
        len(m["content"]) == 300 and m["for_sequences"] == [1]
        for m in payload["memory_context"]
    )


async def test_memory_timeout_preserves_single_decision_call(planner_memory):
    plugin, recall, _ = planner_memory
    plugin.settings["retrieval_timeout"] = 0.1
    cancelled = asyncio.Event()

    async def slow(*args):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    recall.side_effect = slow
    assert len(await requests(plugin, event("native-memory-timeout"))) == 1
    assert cancelled.is_set()
    plugin.context.llm_generate.assert_awaited_once()
    payload = json.loads(plugin.context.llm_generate.await_args.kwargs["prompt"])
    assert payload["memory_context"] == []
