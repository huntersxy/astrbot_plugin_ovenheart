"""Validate contextual selection, native rerank boundaries and stale application."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_ovenheart.retrieval import parse_selection
from astrbot_plugin_ovenheart.settings import DEFAULTS
from tests.test_retrieval import TRAITS

pytest_plugins = ["tests.test_retrieval", "tests.test_runtime"]


@pytest.mark.parametrize(
    "result",
    [
        {"selected_ids": [3]},
        {"selected_ids": [True]},
        {"selected_ids": [1, 1]},
        {"selected_ids": [1, 2]},
        {"selected_ids": "1"},
        {"selected_ids": [], "content": "rewritten habit"},
        {"traits": []},
    ],
)
def test_selection_rejects_unknown_duplicate_rewritten_and_excess_results(result):
    with pytest.raises(ValueError):
        parse_selection(json.dumps(result), TRAITS, 1)


def test_empty_selection_and_complete_json_wrappers_are_valid():
    assert parse_selection('{"selected_ids":[]}', TRAITS, 5) == []
    assert parse_selection('结果：```json\n{"selected_ids":[2]}\n```', TRAITS, 5) == [2]


async def test_contextual_selection_can_recover_synonyms_and_choose_nothing(retrieval):
    retriever, _ = retrieval
    retriever.context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(completion_text='{"selected_ids":[1]}')
    )
    traits = TRAITS + [TRAITS[1] | {"id": 99, "status": "rejected"}]
    result = await retriever.select(
        "scope-a",
        traits,
        "心里堵得慌，什么都不想干",
        DEFAULTS,
        provider_id="chat",
        identity="小夏，温柔的朋友",
    )
    assert result["mode"] == "contextual" and [t["id"] for t in result["traits"]] == [1]
    payload = json.loads(retriever.context.llm_generate.await_args.kwargs["prompt"])
    assert payload["identity_anchor"] == "小夏，温柔的朋友"
    assert {t["id"] for t in payload["candidates"]} == {1, 2}
    assert "task" in payload and "evidence" not in payload["candidates"][0]
    retriever.context.llm_generate.return_value.completion_text = '{"selected_ids":[]}'
    empty = await retriever.select("scope-a", traits, "只回答几点了", DEFAULTS)
    assert empty["traits"] == [] and empty["mode"] == "contextual"


async def test_selector_failure_never_injects_unmatched_conditionals(retrieval):
    retriever, _ = retrieval
    retriever.context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(completion_text='{"selected_ids":[999]}')
    )
    result = await retriever.select("scope-a", TRAITS, "游戏操作", DEFAULTS)
    assert result["mode"] == "contextual_fallback"
    assert result["traits"] == []
    assert "结果格式无效" in result["message"]
    none = await retriever.select("scope-a", TRAITS, "天气", DEFAULTS)
    assert none["traits"] == []


async def test_free_preview_and_busy_selector_never_call_models(retrieval):
    retriever, provider = retrieval
    retriever.context.llm_generate = AsyncMock()
    settings = DEFAULTS | {
        "embedding_provider_id": "embed",
        "rerank_provider_id": "rank",
    }
    await retriever.select("scope-a", TRAITS, "游戏", settings, semantic=False)
    provider.get_embeddings.assert_not_awaited()
    retriever.context.llm_generate.assert_not_awaited()
    retriever.selecting = 2
    result = await retriever.select("scope-a", TRAITS, "游戏", DEFAULTS)
    assert "繁忙" in result["message"]
    retriever.context.llm_generate.assert_not_awaited()


async def test_selector_deadline_cancels_work_and_releases_capacity(retrieval):
    retriever, _ = retrieval
    cancelled = asyncio.Event()

    async def slow(**kwargs):
        try:
            await asyncio.sleep(30)
        finally:
            cancelled.set()

    retriever.context.llm_generate = slow
    result = await retriever.select(
        "scope-a", TRAITS, "游戏", DEFAULTS | {"selector_timeout": 0.02}
    )
    assert result["mode"] == "contextual_fallback" and "超时" in result["message"]
    assert cancelled.is_set() and retriever.selecting == 0


async def test_native_rerank_indexes_are_mapped_to_original_candidates(retrieval):
    retriever, _ = retrieval
    ranker = SimpleNamespace(
        rerank=AsyncMock(
            return_value=[
                SimpleNamespace(index=1, relevance_score=0.9),
                SimpleNamespace(index=0, relevance_score=0.1),
            ]
        )
    )
    retriever.context.get_provider_by_id = lambda identifier: ranker
    result = await retriever.select(
        "scope-a",
        TRAITS,
        "天气",
        DEFAULTS | {"selection_mode": "hybrid", "rerank_provider_id": "rank"},
    )
    offered = ranker.rerank.await_args.kwargs["documents"]
    assert result["mode"] == "rerank"
    assert result["traits"][0]["content"] in offered[1]
    assert retriever.reranking == 0


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [(0, 0.5)],
        [(0, 0.5), (0, 0.9)],
        [(0, 0.5), (3, 0.9)],
        [(True, 0.5), (0, 0.9)],
        [(0, float("nan")), (1, 0.9)],
    ],
)
async def test_bad_rerank_responses_preserve_original_recall(retrieval, rows):
    retriever, _ = retrieval
    ranker = SimpleNamespace(
        rerank=AsyncMock(
            return_value=[SimpleNamespace(index=i, relevance_score=s) for i, s in rows]
        )
    )
    retriever.context.get_provider_by_id = lambda identifier: ranker
    settings = DEFAULTS | {"selection_mode": "hybrid", "rerank_provider_id": "rank"}
    result = await retriever.select("scope-a", TRAITS, "游戏操作", settings)
    assert result["mode"] == "lexical" and result["traits"][0]["id"] == 2
    assert "重排序不可用" in result["message"] and retriever.reranking == 0


async def test_native_contextual_hook_rechecks_profile_after_model_wait(plugin):
    from astrbot.api.provider import ProviderRequest

    from tests.test_runtime import event, sources

    scope = await sources(plugin)
    await plugin.learn(scope)
    trait = (await plugin.store.call("state", scope))["traits"][0]
    await plugin.store.call(
        "mutate", scope, "trait", {"id": trait["id"], "status": "active"}
    )
    plugin.settings["selection_mode"] = "contextual"
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(**kwargs):
        payload = json.loads(kwargs["prompt"])
        assert "宿主人格锚点" in payload["identity_anchor"]
        entered.set()
        await release.wait()
        return SimpleNamespace(
            completion_text=json.dumps({"selected_ids": [trait["id"]]})
        )

    plugin.context.llm_generate.side_effect = delayed
    request = ProviderRequest(prompt="好家伙", system_prompt="宿主原有人格")
    work = asyncio.create_task(plugin.apply_persona(event("contextual"), request))
    await entered.wait()
    await plugin.store.call("mutate", scope, "profile", {"identity": "修改后的身份"})
    release.set()
    await work
    assert request.system_prompt == "宿主原有人格"


@pytest.mark.parametrize("choose", [False, True])
async def test_native_contextual_application_preserves_identity_and_exact_selection(
    plugin, choose
):
    from astrbot.api.provider import ProviderRequest

    from tests.test_runtime import event, sources

    scope = await sources(plugin)
    await plugin.learn(scope)
    traits = (await plugin.store.call("state", scope))["traits"]
    for trait in traits:
        await plugin.store.call(
            "mutate", scope, "trait", {"id": trait["id"], "status": "active"}
        )
    expression = next(t for t in traits if t["kind"] == "expression")
    await plugin.store.call(
        "mutate", scope, "profile", {"identity": "小夏，温柔的朋友"}
    )
    plugin.settings["selection_mode"] = "contextual"
    ids = [expression["id"]] if choose else []
    plugin.context.llm_generate.side_effect = None
    plugin.context.llm_generate.return_value = SimpleNamespace(
        completion_text=json.dumps({"selected_ids": ids})
    )
    incoming = event("selected")
    request = ProviderRequest(
        prompt="别开玩笑了",
        system_prompt="宿主人格",
        contexts=[{"role": "assistant", "content": "刚才的回应"}],
    )
    await plugin.apply_persona(incoming, request)
    assert (
        request.system_prompt.startswith("宿主人格")
        and "小夏，温柔的朋友" in request.system_prompt
    )
    assert (expression["content"] in request.system_prompt) is choose
    assert "先回应对方的情绪" not in request.system_prompt
    assert incoming.get_extra("ovenheart_application")["ids"] == ids
    payload = json.loads(plugin.context.llm_generate.await_args.kwargs["prompt"])
    assert "assistant: 刚才的回应" in payload["conversation"]
