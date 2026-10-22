"""Exercise configured planner recall without a second chat-selection call."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("astrbot.api")

from tests.test_participation import requests  # noqa: E402
from tests.test_runtime import event, sources  # noqa: E402

pytest_plugins = ["tests.test_participation"]


@pytest.fixture
async def planner_habits(participant):
    """Seed a crowded real habit store and native-shaped recall providers.

    Args:
        participant: Isolated native plugin with participation enabled.

    Returns:
        Plugin, scope, embedding provider, reranker and relevant habit ID.
    """
    scope = await sources(participant)
    batch = await participant.store.call("batch", scope, 30)
    traits = [
        {
            "kind": "behavior",
            "situation": "比赛复盘时" if i < 13 else "对方情绪低落时",
            "content": f"NOISE_{i}：分析比赛中的走位"
            if i < 13
            else "RARE_BEHAVIOR：先接住感受，不催促行动",
            "confidence": 0.99,
            "evidence": batch,
        }
        for i in range(14)
    ]
    run = await participant.store.call("start_run", scope)
    await participant.store.call(
        "finish_run",
        run,
        scope,
        batch,
        traits,
        participant.settings | {"activation_mode": "auto"},
        "fixture",
    )
    rare = next(
        t["id"]
        for t in (await participant.store.call("guidance", scope))["traits"]
        if "RARE_BEHAVIOR" in t["content"]
    )
    participant.settings.update(
        selection_mode="contextual",
        embedding_provider_id="embed",
        rerank_provider_id="rank",
        max_traits=1,
    )
    embed = SimpleNamespace(
        get_model=lambda: "fixture-vector",
        provider_config={"type": "test"},
        get_embeddings=AsyncMock(
            side_effect=lambda texts: [
                [1.0, 0.0] if "RARE_BEHAVIOR" in text else [0.0, 1.0] for text in texts
            ]
        ),
        get_embedding=AsyncMock(return_value=[1.0, 0.0]),
    )
    ranker = SimpleNamespace(
        rerank=AsyncMock(
            side_effect=lambda **kwargs: [
                SimpleNamespace(
                    index=i, relevance_score=0.99 if "RARE_BEHAVIOR" in text else 0.1
                )
                for i, text in enumerate(kwargs["documents"])
            ]
        )
    )
    participant.context.get_provider_by_id = lambda key: {
        "embed": embed,
        "rank": ranker,
    }.get(key)
    participant.retriever.status[scope] = {
        "mode": "runtime",
        "message": "正式回复检索状态",
    }
    participant.debug = Mock()
    return participant, scope, embed, ranker, rare


@pytest.mark.parametrize("mode", ["semantic", "fallback", "lexical"])
async def test_planner_recall_uses_native_providers_and_preserves_chat_call_count(
    planner_habits, mode
):
    plugin, scope, embed, ranker, rare = planner_habits
    if mode == "lexical":
        plugin.settings["selection_mode"] = "lexical"
    elif mode == "fallback":
        embed.get_embeddings.side_effect = ValueError("fixture unavailable")
        ranker.rerank.side_effect = ValueError("fixture unavailable")
    assert (
        len(await requests(plugin, event("new", text="心里堵得慌，什么都不想干"))) == 1
    )
    plugin.context.llm_generate.assert_awaited_once()
    payload = json.loads(plugin.context.llm_generate.await_args.kwargs["prompt"])
    assert ("RARE_BEHAVIOR" in payload["behavior_guidance"]) == (mode == "semantic")
    diagnostic = next(
        c.kwargs
        for c in plugin.debug.call_args_list
        if c.args[0] == "planner_retrieval"
    )
    if mode == "semantic":
        embed.get_embeddings.assert_awaited_once()
        ranker.rerank.assert_awaited_once()
        assert diagnostic["included_ids"] == [rare]
    elif mode == "lexical":
        embed.get_embeddings.assert_not_awaited()
        ranker.rerank.assert_not_awaited()
    else:
        assert "向量检索不可用" in diagnostic["message"]
        assert "重排序不可用" in diagnostic["message"]
    assert plugin.retriever.status[scope]["mode"] == "runtime"
    assert not (await plugin.store.call("state", scope))["applications"]
    assert plugin.retriever.reranking == 0


async def test_planner_recall_cancels_before_chat_when_participation_is_paused(
    planner_habits,
):
    plugin, _, embed, _, _ = planner_habits
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def slow(texts):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    embed.get_embeddings.side_effect = slow
    work = asyncio.create_task(requests(plugin, event("pause", text="心里堵得慌")))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await plugin.update_settings({"participation_mode": "off"})
        assert await asyncio.wait_for(work, 2) == []
        assert cancelled.is_set()
        plugin.context.llm_generate.assert_not_awaited()
    finally:
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)


async def test_planner_drops_a_habit_disabled_during_recall(planner_habits):
    plugin, _, embed, _, rare = planner_habits
    entered, release = asyncio.Event(), asyncio.Event()
    original = embed.get_embeddings.side_effect

    async def slow(texts):
        entered.set()
        await release.wait()
        return original(texts)

    embed.get_embeddings.side_effect = slow
    work = asyncio.create_task(requests(plugin, event("disable", text="心里堵得慌")))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        scope = (await plugin.store.call("state"))["scopes"][0]["id"]
        await plugin.store.call(
            "mutate", scope, "trait", {"id": rare, "status": "disabled"}
        )
        release.set()
        assert len(await asyncio.wait_for(work, 2)) == 1
        payload = json.loads(plugin.context.llm_generate.await_args.kwargs["prompt"])
        assert "RARE_BEHAVIOR" not in payload["behavior_guidance"]
    finally:
        release.set()
        await asyncio.gather(work, return_exceptions=True)
