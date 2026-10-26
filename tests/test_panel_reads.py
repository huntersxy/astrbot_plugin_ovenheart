"""Panel views keep heavy data off the first frame and do not queue behind writes."""

import asyncio
import json
import sqlite3
from unittest.mock import AsyncMock, Mock

import pytest

from tests.test_runtime import api_call, sources

pytest_plugins = ["tests.test_runtime"]


async def test_runtime_paths_follow_async_generators_without_exposing_locals(plugin):
    gate = asyncio.Event()

    async def stalled_stream():
        private_message = "runtime-diagnostic-private-message"
        await gate.wait()
        yield private_message

    async def consume():
        async for _ in stalled_stream():
            pass

    tasks = [asyncio.create_task(consume()) for _ in range(2)]
    try:
        await asyncio.sleep(0)
        result = await plugin.page_api.runtime()
        matching = [
            item
            for item in result["data"]
            if any(":stalled_stream" in frame for frame in item["await_path"])
        ]
        assert len(matching) == 1 and matching[0]["count"] == 2
        assert any(":wait" in frame for frame in matching[0]["await_path"])
        assert "runtime-diagnostic-private-message" not in json.dumps(result)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_light_views_select_scope_once_without_traits_or_history(
    plugin, monkeypatch
):
    scope = await sources(plugin)
    await plugin.learn(scope)
    full = (await api_call(plugin.page_api.state, query=f"scope={scope}"))["data"]
    assert full["traits"] and full["runs"] and "tools" in full
    traits = Mock(side_effect=AssertionError("Heavy traits loaded"))
    monkeypatch.setattr(plugin.store, "_state_traits", traits)
    inventory = Mock(side_effect=AssertionError("Tool inventory loaded"))
    monkeypatch.setattr(plugin.page_api, "tool_inventory", inventory)
    for view in ["profile", "summary", "settings"]:
        data = (await api_call(plugin.page_api.state, query=f"view={view}"))["data"]
        assert data["scope"]["id"] == scope
        assert data["scope"]["profile"] == full["scope"]["profile"]
        assert data["traits"] == data["runs"] == []
        assert "tools" not in data and "auxiliary_usage" not in data
        assert len(json.dumps(data)) < len(json.dumps(full))
    history = (
        await api_call(plugin.page_api.state, query=f"view=history&scope={scope}")
    )["data"]
    assert history["runs"] == full["runs"] and history["traits"] == []
    traits.assert_not_called()
    inventory.assert_not_called()


async def test_panel_read_bypasses_write_queue_but_keeps_snapshot_isolation(plugin):
    scope = await sources(plugin)
    async with plugin.store.lock:
        data = await asyncio.wait_for(
            api_call(plugin.page_api.state, query=f"view=profile&scope={scope}"), 1
        )
    assert data["data"]["scope"]["id"] == scope
    before = await plugin.store.read("scope_profile", scope)
    with sqlite3.connect(plugin.store.path) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE scopes SET label='uncommitted' WHERE id=?", (scope,))
        during = await plugin.store.read("scope_profile", scope)
        assert during["label"] == before["label"]
        writer.rollback()


async def test_read_only_paths_cannot_write_and_pending_count_is_covered(plugin):
    scope = await sources(plugin)
    with pytest.raises(ValueError):
        await plugin.store.read("mutate", scope, "profile", {"identity": "bad"})
    with pytest.raises(ValueError):
        await plugin.store.read("auxiliary_usage", [{"calls": 1}])
    with sqlite3.connect(plugin.store.path) as db:
        plan = str(
            db.execute(
                "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM messages WHERE scope=? AND consumed=0 AND sender IN (?)",
                (scope, "alice"),
            ).fetchall()
        )
        assert "COVERING INDEX messages_pending_sender" in plan


async def test_tools_route_and_invalid_view_follow_native_api_contract(plugin):
    result = (await api_call(plugin.page_api.tools))["data"]
    assert "items" in result and "available" in result
    result = await api_call(plugin.page_api.state, query="view=untrusted")
    assert result["status"] == "error"


async def test_busy_database_has_safe_actionable_message(plugin, monkeypatch):
    monkeypatch.setattr(
        plugin.store,
        "read",
        AsyncMock(side_effect=sqlite3.OperationalError("private-db-path")),
    )
    result = await api_call(plugin.page_api.state, query="view=profile")
    assert result["status"] == "error" and "稍后刷新" in result["message"]
    assert "private-db-path" not in result["message"]
