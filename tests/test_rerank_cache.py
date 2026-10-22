"""Verify bounded rerank reuse without freezing mutable habit metadata."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_ovenheart.settings import DEFAULTS
from tests.test_retrieval import TRAITS

pytest_plugins = ["tests.test_retrieval"]


@pytest.fixture
def cached_ranker(retrieval):
    """Provide a real retriever and a deterministic native rerank contract.

    Args:
        retrieval: Isolated retriever with a real SQLite store.

    Returns:
        Retriever, provider and hybrid settings.
    """
    retriever, _ = retrieval
    provider = SimpleNamespace(
        provider_config={
            "rerank_model": "fixture",
            "rerank_api_base": "https://test.invalid",
            "rerank_api_key": "SECRET_SENTINEL",
        },
        rerank=AsyncMock(
            return_value=[
                SimpleNamespace(index=0, relevance_score=0.1),
                SimpleNamespace(index=1, relevance_score=0.9),
            ]
        ),
    )
    retriever.context.get_provider_by_id = lambda key: provider
    return (
        retriever,
        provider,
        DEFAULTS | {"selection_mode": "hybrid", "rerank_provider_id": "rank"},
    )


async def test_warm_rerank_reuses_scores_with_fresh_metadata_even_when_remote_busy(
    cached_ranker,
):
    retriever, provider, settings = cached_ranker
    first = await retriever.select("scope-a", TRAITS, "天气", settings)
    retriever.reranking = 2
    current = [TRAITS[0] | {"use_count": 99}, TRAITS[1]]
    second = await retriever.select("scope-a", current, "天气", settings)
    provider.rerank.assert_awaited_once()
    assert second["rerank_cache_hit"] and "已复用重排序缓存" in second["message"]
    assert [t["id"] for t in second["traits"]] == [t["id"] for t in first["traits"]]
    assert next(t for t in second["traits"] if t["id"] == 1)["use_count"] == 99
    assert "SECRET_SENTINEL" not in str(retriever.rerank_cache)
    assert "先倾听" not in str(retriever.rerank_cache)
    assert retriever.reranking == 2


async def test_rerank_fingerprint_and_expiry_follow_actual_native_inputs(cached_ranker):
    retriever, provider, settings = cached_ranker
    cases = [
        ("scope-a", "问题", TRAITS),
        ("scope-a", "另一个问题", TRAITS),
        ("scope-b", "另一个问题", TRAITS),
        ("scope-b", "另一个问题", list(reversed(TRAITS))),
        ("scope-b", "另一个问题", [TRAITS[0] | {"content": "新的表达"}, TRAITS[1]]),
    ]
    for scope, query, traits in cases:
        await retriever.rerank_pool(
            traits, query, settings, time.monotonic() + 2, scope=scope
        )
    assert provider.rerank.await_count == 5
    for key, value in (
        ("rerank_model", "new-model"),
        ("rerank_api_base", "https://other.invalid"),
    ):
        provider.provider_config[key] = value
        await retriever.rerank_pool(
            TRAITS, "问题", settings, time.monotonic() + 2, scope="scope-a"
        )
    assert provider.rerank.await_count == 7
    for key, (_, scores) in list(retriever.rerank_cache.items()):
        retriever.rerank_cache[key] = (time.monotonic() - 301, scores)
    await retriever.rerank_pool(
        TRAITS, "问题", settings, time.monotonic() + 2, scope="scope-a"
    )
    assert provider.rerank.await_count == 8


async def test_failures_and_cancellation_never_publish_cached_rerank_scores(
    cached_ranker,
):
    retriever, provider, settings = cached_ranker
    provider.rerank.return_value = []
    for _ in range(2):
        result = await retriever.select("scope-a", TRAITS, "天气", settings)
        assert "重排序不可用" in result["message"]
    assert provider.rerank.await_count == 2 and not retriever.rerank_cache
    retriever.reranking = 2
    assert (
        "重排序繁忙"
        in (await retriever.select("scope-a", TRAITS, "天气", settings))["message"]
    )
    assert provider.rerank.await_count == 2
    retriever.reranking = 0
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def slow(**kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    provider.rerank.side_effect = slow
    work = asyncio.create_task(retriever.select("scope-a", TRAITS, "天气", settings))
    try:
        await asyncio.wait_for(entered.wait(), 2)
    finally:
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)
    assert (
        cancelled.is_set() and retriever.reranking == 0 and not retriever.rerank_cache
    )


async def test_replaced_provider_cannot_receive_a_late_old_cache_write(cached_ranker):
    retriever, old, settings = cached_ranker
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow(**kwargs):
        entered.set()
        await release.wait()
        return [
            SimpleNamespace(index=0, relevance_score=0.9),
            SimpleNamespace(index=1, relevance_score=0.1),
        ]

    old.rerank.side_effect = slow
    work = asyncio.create_task(
        retriever.rerank_pool(TRAITS, "问题", settings, time.monotonic() + 2)
    )
    try:
        await asyncio.wait_for(entered.wait(), 2)
        new = SimpleNamespace(
            provider_config=old.provider_config.copy(),
            rerank=AsyncMock(
                return_value=[
                    SimpleNamespace(index=0, relevance_score=0.1),
                    SimpleNamespace(index=1, relevance_score=0.9),
                ]
            ),
        )
        retriever.context.get_provider_by_id = lambda key: new
        await retriever.rerank_pool(TRAITS, "问题", settings, time.monotonic() + 2)
        release.set()
        await work
        result = await retriever.rerank_pool(
            TRAITS, "问题", settings, time.monotonic() + 2
        )
        assert result[0]["id"] == 2 and result[0]["_rerank_cached"]
        new.rerank.assert_awaited_once()
    finally:
        release.set()
        await asyncio.gather(work, return_exceptions=True)
