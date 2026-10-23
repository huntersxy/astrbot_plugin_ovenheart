"""Reuse exact validated choices without stale inputs or additional remote slots."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_ovenheart.settings import DEFAULTS
from tests.test_retrieval import TRAITS

pytest_plugins = ["tests.test_retrieval"]


async def test_hits_work_while_busy_and_rebuild_current_metadata_including_empty_choices(
    retrieval,
):
    retriever, _ = retrieval
    retriever.context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(completion_text='{"selected_ids":[1]}')
    )
    await retriever.select("native-a", TRAITS, "谈谈心情", DEFAULTS, provider_id="chat")
    retriever.selecting = 2
    current = [TRAITS[0] | {"confidence": 0.6, "rating": 1, "updated": 99}, TRAITS[1]]
    hit = await retriever.select(
        "native-a", current, "谈谈心情", DEFAULTS, provider_id="chat"
    )
    assert hit["selector_cache_hit"] and hit["traits"][0]["confidence"] == 0.6
    retriever.context.llm_generate.assert_awaited_once()
    assert retriever.selecting == 2
    retriever.selecting = 0
    retriever.selector_cache.clear()
    retriever.context.llm_generate.return_value.completion_text = '{"selected_ids":[]}'
    await retriever.select(
        "native-a", TRAITS, "不需要建议", DEFAULTS, provider_id="chat"
    )
    empty = await retriever.select(
        "native-a", TRAITS, "不需要建议", DEFAULTS, provider_id="chat"
    )
    assert (
        empty["mode"] == "contextual"
        and empty["selector_cache_hit"]
        and empty["traits"] == []
    )
    assert retriever.context.llm_generate.await_count == 2


async def test_scope_config_and_sample_changes_do_not_reuse_old_choices(retrieval):
    retriever, provider = retrieval
    retriever.context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(completion_text='{"selected_ids":[1]}')
    )
    await retriever.select(
        "native-a", TRAITS, "同一句输入", DEFAULTS, provider_id="chat"
    )
    other = await retriever.select(
        "native-b", TRAITS, "同一句输入", DEFAULTS, provider_id="chat"
    )
    assert not other["selector_cache_hit"]
    provider.provider_config["temperature"] = 0.5
    changed = await retriever.select(
        "native-a", TRAITS, "同一句输入", DEFAULTS, provider_id="chat"
    )
    assert not changed["selector_cache_hit"]
    samples = [TRAITS[0] | {"examples": [{"text": "真实短说法"}]}, TRAITS[1]]
    changed = await retriever.select(
        "native-a", samples, "同一句输入", DEFAULTS, provider_id="chat"
    )
    assert not changed["selector_cache_hit"]
    assert retriever.context.llm_generate.await_count == 4
    assert all("must-not-be-persisted" not in key for key in retriever.selector_cache)


async def test_invalid_and_cancelled_completions_never_seed_the_cache(retrieval):
    retriever, _ = retrieval
    retriever.context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(completion_text='{"selected_ids":[999]}')
    )
    bad = await retriever.select(
        "native-a", TRAITS, "本轮输入", DEFAULTS, provider_id="chat"
    )
    assert bad["mode"] == "contextual_fallback" and not retriever.selector_cache
    entered = asyncio.Event()

    async def slow(**kwargs):
        """Hold one native-shaped request until cancellation.

        Args:
            **kwargs: Selector request options.
        """
        entered.set()
        await asyncio.sleep(30)

    retriever.context.llm_generate.side_effect = slow
    work = asyncio.create_task(
        retriever.select("native-a", TRAITS, "本轮输入", DEFAULTS, provider_id="chat")
    )
    await asyncio.wait_for(entered.wait(), 2)
    work.cancel()
    with pytest.raises(asyncio.CancelledError):
        await work
    assert retriever.selecting == 0 and not retriever.selector_cache


async def test_expiry_and_provider_replacement_prevent_stale_publication(retrieval):
    retriever, _ = retrieval
    retriever.context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(completion_text='{"selected_ids":[1]}')
    )
    await retriever.select("native-a", TRAITS, "重复输入", DEFAULTS, provider_id="chat")
    for key, entry in list(retriever.selector_cache.items()):
        retriever.selector_cache[key] = (time.monotonic() - 61, entry[1], entry[2])
    expired = await retriever.select(
        "native-a", TRAITS, "重复输入", DEFAULTS, provider_id="chat"
    )
    assert (
        not expired["selector_cache_hit"]
        and retriever.context.llm_generate.await_count == 2
    )
    retriever.selector_cache.clear()
    replacement = SimpleNamespace(get_model=lambda: "replacement", provider_config={})

    async def replace(**kwargs):
        """Swap the native provider while an old request is completing.

        Args:
            **kwargs: Selector request options.

        Returns:
            Valid output from the old request, which must not be cached.
        """
        retriever.context.get_provider_by_id = lambda key: replacement
        return SimpleNamespace(completion_text='{"selected_ids":[1]}')

    retriever.context.llm_generate.side_effect = replace
    await retriever.select("native-a", TRAITS, "交换期间", DEFAULTS, provider_id="chat")
    assert not retriever.selector_cache
    retriever.context.llm_generate.side_effect = None
    retriever.context.llm_generate.return_value.completion_text = '{"selected_ids":[2]}'
    fresh = await retriever.select(
        "native-a", TRAITS, "交换期间", DEFAULTS, provider_id="chat"
    )
    assert not fresh["selector_cache_hit"] and fresh["traits"][0]["id"] == 2
    assert retriever.context.llm_generate.await_count == 4
