"""Test semantic retrieval, invalidation, isolation and bounded failure paths."""

import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_ovenheart.core.learning import build_guidance
from astrbot_plugin_ovenheart.core.retrieval import (
    HabitRetriever,
    normalized_vector,
    rank_traits,
)
from astrbot_plugin_ovenheart.core.settings import DEFAULTS, PROFILE_DEFAULTS
from astrbot_plugin_ovenheart.core.store import Store

TRAITS = [
    {
        "id": 1,
        "kind": "expression",
        "situation": "朋友情绪低落时",
        "content": "先倾听，不急着给建议",
        "confidence": 0.9,
        "status": "active",
        "updated": 1,
    },
    {
        "id": 2,
        "kind": "expression",
        "situation": "聊游戏时",
        "content": "轻松调侃游戏中的操作",
        "confidence": 0.95,
        "status": "active",
        "updated": 2,
    },
]


def test_quoted_single_character_jargon_is_found_without_embeddings():
    trait = TRAITS[0] | {
        "kind": "jargon",
        "content": "『典』：例子已归档",
        "situation": "归档时",
    }
    assert rank_traits([trait], "典？", DEFAULTS, {trait["id"]: 0})
    assert "词汇" in build_guidance(PROFILE_DEFAULTS, [trait], DEFAULTS, "典？")
    assert not rank_traits([trait], "下雨", DEFAULTS, {trait["id"]: 0})


def test_ascii_word_fragment_does_not_recall_an_unrelated_local_definition():
    trait = TRAITS[0] | {
        "kind": "jargon",
        "content": "「LUX」：甲方报告",
        "situation": "报告时",
    }
    assert not rank_traits([trait], "deluxe", DEFAULTS, {trait["id"]: 0})
    assert rank_traits([trait], "ＬＵＸ？", DEFAULTS, {trait["id"]: 0})


@pytest.fixture
async def retrieval(tmp_path):
    store = Store(tmp_path / "persona.sqlite3")
    await store.call("initialize")
    await store.call(
        "capture",
        "scope-a",
        "qq:g:1",
        "default",
        "test",
        "one",
        "u",
        "测试素材",
        DEFAULTS,
    )
    # Real scoped rows exercise the cache's JOIN rather than mocking persistence.
    with sqlite3.connect(store.path) as db:
        for t in TRAITS:
            db.execute(
                "INSERT INTO traits(id,scope,kind,situation,content,confidence,evidence,status,fingerprint,created,updated) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    t["id"],
                    "scope-a",
                    t["kind"],
                    t["situation"],
                    t["content"],
                    t["confidence"],
                    "[]",
                    "active",
                    str(t["id"]),
                    1,
                    1,
                ),
            )
    provider = SimpleNamespace(
        get_model=lambda: "test-model",
        provider_config={"type": "test", "key": "must-not-be-persisted"},
        get_embeddings=AsyncMock(return_value=[[1.0, 0.0], [0.0, 1.0]]),
        get_embedding=AsyncMock(return_value=[1.0, 0.0]),
    )
    context = SimpleNamespace(get_provider_by_id=lambda identifier: provider)
    return HabitRetriever(context, store), provider


async def test_semantic_synonyms_rank_without_matching_characters(retrieval):
    retriever, provider = retrieval
    settings = DEFAULTS | {"selection_mode": "hybrid", "embedding_provider_id": "embed"}
    result = await retriever.select(
        "scope-a", TRAITS, "心里堵得慌，什么都不想干", settings
    )
    assert result["mode"] == "hybrid"
    assert [t["id"] for t in result["traits"]] == [1]
    assert result["traits"][0]["_reason"] == "语义匹配"
    await retriever.select("scope-a", TRAITS, "心里堵得慌，什么都不想干", settings)
    assert (
        provider.get_embeddings.await_count == 1
        and provider.get_embedding.await_count == 1
    )
    with sqlite3.connect(retriever.store.path) as db:
        signature = db.execute("SELECT provider FROM embeddings LIMIT 1").fetchone()[0]
    assert not await retriever.store.call("vectors", signature, "scope-b")


async def test_model_and_edited_content_invalidate_cached_vectors(retrieval):
    retriever, provider = retrieval
    settings = DEFAULTS | {"selection_mode": "hybrid", "embedding_provider_id": "embed"}
    await retriever.select("scope-a", TRAITS, "心里堵得慌", settings)
    provider.get_embeddings.return_value = [[1.0, 0.0]]
    await retriever.select(
        "scope-a",
        [TRAITS[0] | {"content": "先安静陪着"}, TRAITS[1]],
        "心里堵得慌",
        settings,
    )
    assert provider.get_embeddings.await_count == 2
    assert provider.get_embeddings.await_args.args[0] == ["朋友情绪低落时\n先安静陪着"]
    provider.get_model = lambda: "new-model"
    provider.get_embeddings.return_value = [[1.0, 0.0], [0.0, 1.0]]
    await retriever.select("scope-a", TRAITS, "心里堵得慌", settings)
    assert (
        provider.get_embeddings.await_count == 3
        and provider.get_embedding.await_count == 2
    )


async def test_bad_embeddings_fall_back_without_persisting_invalid_vectors(retrieval):
    retriever, provider = retrieval
    provider.get_embeddings.return_value = [[float("nan"), 0], [0, 1]]
    result = await retriever.select(
        "scope-a",
        TRAITS,
        "游戏操作",
        DEFAULTS | {"selection_mode": "hybrid", "embedding_provider_id": "embed"},
    )
    assert result["mode"] == "lexical" and "回退" in result["message"]
    assert result["traits"][0]["id"] == 2
    with sqlite3.connect(retriever.store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 0


async def test_native_embedding_config_model_and_endpoint_invalidate_cache(retrieval):
    retriever, provider = retrieval
    provider.get_model = lambda: ""
    provider.provider_config.update(
        embedding_model="BAAI/bge-m3", embedding_api_base="https://example.test/v1"
    )
    settings = DEFAULTS | {"selection_mode": "hybrid", "embedding_provider_id": "embed"}
    await retriever.select("scope-a", TRAITS, "今天想聊游戏", settings)
    provider.provider_config["embedding_model"] = "different-model"
    await retriever.select("scope-a", TRAITS, "今天想聊游戏", settings)
    provider.provider_config["embedding_api_base"] = "https://other.test/v1"
    await retriever.select("scope-a", TRAITS, "今天想聊游戏", settings)
    assert (
        provider.get_embeddings.await_count == provider.get_embedding.await_count == 3
    )


async def test_deadline_cancels_model_work_and_preview_is_free(retrieval):
    retriever, provider = retrieval
    settings = DEFAULTS | {
        "selection_mode": "hybrid",
        "embedding_provider_id": "embed",
        "retrieval_timeout": 0.5,
    }
    cancelled = asyncio.Event()

    async def delayed(texts):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    provider.get_embeddings.side_effect = delayed
    result = await retriever.select("scope-a", TRAITS, "今天想聊游戏", settings)
    assert result["mode"] == "lexical" and cancelled.is_set()
    result = await retriever.select(
        "scope-a", TRAITS, "今天想聊游戏", settings, semantic=False
    )
    assert "免费预览" in result["message"] and provider.get_embeddings.await_count == 1


@pytest.mark.parametrize(
    "value", [[], [0, 0], [float("inf")], [True], "bad", [1] * 8193]
)
def test_malformed_vectors_are_rejected(value):
    with pytest.raises(ValueError):
        normalized_vector(value)


def test_explicit_feedback_and_fresh_evidence_change_ranking_without_enabling_rejected():
    original = TRAITS[0]
    habits = [
        original | {"id": 1, "rating": -1},
        original | {"id": 2, "rating": 1},
        original | {"id": 3, "status": "rejected", "rating": 1},
    ]
    ordered = rank_traits(habits, "朋友情绪低落", DEFAULTS)
    assert [t["id"] for t in ordered] == [2, 1]
    assert "must-not-be-persisted" not in json.dumps(ordered)
