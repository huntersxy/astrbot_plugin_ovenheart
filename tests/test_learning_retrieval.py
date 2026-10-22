"""Check semantic consolidation references without reactivating stored decisions."""

import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("astrbot.api")

from tests.test_runtime import event, sources  # noqa: E402

pytest_plugins = ["tests.test_runtime"]


@pytest.fixture
async def known_library(plugin):
    """Build an old rejected entry outside the latest forty references.

    Args:
        plugin: Native isolated plugin fixture.

    Returns:
        Plugin, source scope, old rejected ID and simulated embedding provider.
    """
    scope = await sources(plugin)
    batch = await plugin.store.call("batch", scope, 30)
    traits = [
        {
            "kind": "behavior",
            "situation": "闲聊时",
            "content": "OLD_REFERENCE：先倾听对方的感受"
            if i == 0
            else f"比赛策略复盘{i}",
            "confidence": 0.95 if i == 0 else 0.99,
            "evidence": batch,
        }
        for i in range(61)
    ]
    run = await plugin.store.call("start_run", scope)
    await plugin.store.call(
        "finish_run",
        run,
        scope,
        batch,
        traits,
        plugin.settings | {"activation_mode": "auto"},
        "fixture",
    )
    old = next(
        t["id"]
        for t in (await plugin.store.call("state", scope))["traits"]
        if "OLD_REFERENCE" in t["content"]
    )
    await plugin.store.call("mutate", scope, "trait", {"id": old, "status": "rejected"})
    with sqlite3.connect(plugin.store.path) as db:
        db.execute("UPDATE traits SET updated=1,last_seen=1 WHERE id=?", (old,))
    await plugin.capture(event("new-1", text="心里堵得慌，什么都不想干"))
    await plugin.capture(event("new-2", text="能不能先陪我待一会儿"))
    provider = SimpleNamespace(
        get_model=lambda: "fixture-embedding",
        provider_config={"type": "fixture"},
        get_embeddings=AsyncMock(
            side_effect=lambda texts: [
                [1.0, 0.0] if "OLD_REFERENCE" in t else [0.0, 1.0] for t in texts
            ]
        ),
        get_embedding=AsyncMock(return_value=[1.0, 0.0]),
    )
    plugin.context.get_provider_by_id = lambda key: provider
    plugin.settings.update(selection_mode="contextual", embedding_provider_id="embed")
    plugin.context.llm_generate.side_effect = None
    plugin.context.llm_generate.return_value = SimpleNamespace(
        completion_text='{"traits":[]}'
    )
    plugin.retriever.status[scope] = {"mode": "runtime", "message": "正式回复状态"}
    plugin.debug = Mock()
    return plugin, scope, old, provider


async def test_learning_recalls_old_rejected_counterparts_without_a_chat_selector(
    known_library,
):
    plugin, scope, old, provider = known_library
    assert (await plugin.learn(scope))["status"] == "success"
    offered = []
    for call in plugin.context.llm_generate.await_args_list:
        payload = json.loads(call.kwargs["prompt"])
        offered.extend(payload["known_traits"])
    counterpart = next(t for t in offered if t["id"] == old)
    assert counterpart["status"] == "rejected"
    assert plugin.context.llm_generate.await_count == 2
    provider.get_embeddings.assert_awaited_once()
    assert plugin.retriever.status[scope]["mode"] == "runtime"
    assert (
        next(
            t
            for t in (await plugin.store.call("state", scope))["traits"]
            if t["id"] == old
        )["status"]
        == "rejected"
    )


async def test_learning_reference_service_failure_retains_lexical_fallback(
    known_library,
):
    plugin, scope, _, provider = known_library
    provider.get_embeddings.side_effect = ValueError("fixture unavailable")
    assert (await plugin.learn(scope))["status"] == "success"
    diagnostic = next(
        c.kwargs
        for c in plugin.debug.call_args_list
        if c.args[0] == "learning_retrieval"
    )
    assert "回退文字" in diagnostic["message"]
    assert plugin.context.llm_generate.await_count == 2


async def test_learning_query_covers_the_whole_batch_with_bounded_head_tail_text(
    known_library,
):
    plugin, scope, _, provider = known_library
    for index in range(28):
        await plugin.capture(
            event(
                f"long-{index}",
                text=f"START_{index:02d}" + "长消息" * 300 + f"END_{index:02d}",
            )
        )
    assert (await plugin.learn(scope))["status"] == "success"
    query = provider.get_embedding.await_args.args[0]
    assert len(query) <= 2400
    for index in range(28):
        assert f"START_{index:02d}" in query and f"END_{index:02d}" in query
    assert "心里堵得慌" in query and "能不能先陪我" in query


async def test_profile_change_during_reference_recall_keeps_materials_and_skips_chat(
    known_library,
):
    plugin, scope, _, provider = known_library
    entered, release = asyncio.Event(), asyncio.Event()
    original = provider.get_embeddings.side_effect

    async def slow(texts):
        entered.set()
        await release.wait()
        return original(texts)

    provider.get_embeddings.side_effect = slow
    work = asyncio.create_task(plugin.learn(scope))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await plugin.store.call(
            "mutate", scope, "profile", {"identity": "更新后的底稿"}
        )
        release.set()
        result = await asyncio.wait_for(work, 2)
        assert result["status"] == "error" and "底稿已改变" in result["message"]
        plugin.context.llm_generate.assert_not_awaited()
        assert len(await plugin.store.call("batch", scope, 30)) == 2
    finally:
        release.set()
        await asyncio.gather(work, return_exceptions=True)
