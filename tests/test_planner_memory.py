"""Use authorized memory as bounded planning context without extra chat stages."""

import asyncio
import importlib.util
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from astrbot.core.message.components import Reply

from tests.test_participation import requests
from tests.test_runtime import POLICY_PATH, event

pytest_plugins = ["tests.test_runtime", "tests.test_participation"]


@pytest.fixture
async def planner_memory(participant, monkeypatch):
    """Attach a simulated engine behind LivingMemory's actual access policy.

    Args:
        participant: Native autonomous-participation fixture.
        monkeypatch: Isolated module/config replacement.

    Returns:
        Plugin, engine and mutable LivingMemory policy values.
    """
    if not POLICY_PATH.exists():
        pytest.skip("LivingMemory policy checkout is unavailable")
    spec = importlib.util.spec_from_file_location(
        "planner_lm_fixture.core.memory_scope", POLICY_PATH
    )
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    monkeypatch.setitem(sys.modules, spec.name, policy)
    engine = SimpleNamespace(
        search_memories=AsyncMock(
            return_value=[
                SimpleNamespace(
                    doc_id=f"old-{i}", content="OLDER_SHARED_BACKGROUND_" * 60
                )
                for i in range(5)
            ]
        )
    )
    values = {"filtering_settings.memory_scope_mode": "session"}
    manager = SimpleNamespace(filtering_settings={"use_persona_filtering": True})
    manager.get = lambda key, default=None: values.get(key, default)
    memory_plugin = type(
        "MemoryPlugin", (), {"__module__": "planner_lm_fixture.main"}
    )()
    memory_plugin.initializer = SimpleNamespace(
        is_initialized=True, memory_engine=engine
    )
    memory_plugin.config_manager = manager
    metadata = SimpleNamespace(activated=True, star_cls=memory_plugin)
    participant.context.get_registered_star = lambda name: (
        metadata if name == "astrbot_plugin_livingmemory" else None
    )
    participant.debug = Mock()
    return participant, engine, values


async def test_planner_receives_bounded_shared_memory_with_actual_sequence_links(
    planner_memory,
):
    plugin, engine, _ = planner_memory
    plugin.settings.update(participation_min_messages=2, memory_top_k=8)
    assert (
        await requests(plugin, event("old-bob", text="OLDER_BOB_INPUT", sender="bob"))
        == []
    )
    current = event("new-alice", text="那我接着上次的话说")
    current.message_obj.message = [
        Reply(id="outside", sender_id="bob", message_str="QUOTE_MEMORY_CONTEXT")
    ]
    assert len(await requests(plugin, current)) == 1
    engine.search_memories.assert_awaited_once()
    call = engine.search_memories.await_args.kwargs
    assert (
        call["session_id"] == current.unified_msg_origin
        and call["persona_id"] == "default"
    )
    assert call["k"] == 3 and len(call["query"]) <= 500
    assert (
        "OLDER_BOB_INPUT" in call["query"] and "QUOTE_MEMORY_CONTEXT" in call["query"]
    )
    plugin.context.llm_generate.assert_awaited_once()
    payload = json.loads(plugin.context.llm_generate.await_args.kwargs["prompt"])
    memories = payload["memory_context"]
    assert len(memories) == 3 and all(len(m["content"]) == 300 for m in memories)
    assert all(set(m["for_sequences"]) == {1, 2} for m in memories)
    scope = current.get_extra("ovenheart_decision")["scope"]
    assert "OLDER_SHARED_BACKGROUND" not in json.dumps(
        await plugin.store.call("state", scope)
    )


@pytest.mark.parametrize("boundary", ["personal_scope", "timeout", "policy_change"])
async def test_planner_memory_fallback_preserves_the_single_decision_call(
    planner_memory, boundary
):
    plugin, engine, values = planner_memory
    cancelled = asyncio.Event()
    if boundary == "personal_scope":
        values["filtering_settings.memory_scope_mode"] = "user"
    elif boundary == "timeout":
        plugin.settings["retrieval_timeout"] = 0.1

        async def slow(**kwargs):
            """Emulate a remote search that must be cancelled by the shared budget.

            Args:
                **kwargs: Actual engine search options.
            """
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()

        engine.search_memories.side_effect = slow
    else:

        async def revoke(**kwargs):
            """Revoke policy while an authorized search is completing.

            Args:
                **kwargs: Actual engine search options.

            Returns:
                A result that must be discarded before reaching the planner.
            """
            values.update(
                {
                    "access_control.whitelist_enabled": True,
                    "access_control.allowed_ids": "nobody",
                }
            )
            return [SimpleNamespace(doc_id="late", content="REVOKED_MEMORY")]

        engine.search_memories.side_effect = revoke
    assert len(await requests(plugin, event("memory-boundary"))) == 1
    plugin.context.llm_generate.assert_awaited_once()
    payload = json.loads(plugin.context.llm_generate.await_args.kwargs["prompt"])
    assert payload["memory_context"] == [] and "REVOKED_MEMORY" not in json.dumps(
        payload
    )
    if boundary == "personal_scope":
        engine.search_memories.assert_not_awaited()
    else:
        engine.search_memories.assert_awaited_once()
    if boundary == "timeout":
        assert cancelled.is_set()
