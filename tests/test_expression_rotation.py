"""Prefer fresh comparable style references without invalidating remote caches."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_ovenheart.settings import DEFAULTS
from tests.test_retrieval import TRAITS

pytest_plugins = ["tests.test_retrieval"]

STYLES = [
    TRAITS[0]
    | {
        "situation": "谈心时",
        "content": "先听对方说，再用轻声回应",
        "confidence": 0.95,
    },
    TRAITS[1]
    | {"situation": "谈心时", "content": "用简短语气词表示在听", "confidence": 0.95},
]


async def test_lexical_rotation_uses_only_the_supplied_native_recent_ids(retrieval):
    retriever, provider = retrieval
    cfg = DEFAULTS | {"selection_mode": "lexical", "max_traits": 1}
    recent = await retriever.select(
        "shared", STYLES, "谈心", cfg, recent_expression_ids=[2]
    )
    assert [t["id"] for t in recent["traits"]] == [1]
    assert recent["expression_rotation"] == [1]
    plain = await retriever.select("shared", STYLES, "谈心", cfg)
    assert [t["id"] for t in plain["traits"]] == [2]
    all_recent = await retriever.select(
        "shared", STYLES, "谈心", cfg, recent_expression_ids=[1, 2]
    )
    assert [t["id"] for t in all_recent["traits"]] == [2]
    duplicate = await retriever.select(
        "shared",
        [STYLES[0], STYLES[0] | {"id": 2, "updated": 2}],
        "谈心",
        cfg,
        recent_expression_ids=[2],
    )
    assert "expression_rotation" not in duplicate
    provider.get_embeddings.assert_not_awaited()


@pytest.mark.parametrize("fresh_score, expected", [(0.94, 1), (0.4, 2)])
async def test_rerank_relevance_and_cache_survive_per_request_rotation(
    retrieval, fresh_score, expected
):
    retriever, _ = retrieval
    ranker = SimpleNamespace(
        rerank=AsyncMock(
            side_effect=lambda **kwargs: [
                SimpleNamespace(
                    index=i, relevance_score=0.95 if "语气词" in text else fresh_score
                )
                for i, text in enumerate(kwargs["documents"])
            ]
        )
    )
    retriever.context.get_provider_by_id = lambda key: ranker
    cfg = DEFAULTS | {
        "selection_mode": "hybrid",
        "rerank_provider_id": "rank",
        "max_traits": 1,
    }
    first = await retriever.select(
        "shared", STYLES, "谈心", cfg, recent_expression_ids=[2]
    )
    assert first["traits"][0]["id"] == expected
    second = await retriever.select(
        "shared", STYLES, "谈心", cfg, recent_expression_ids=[1]
    )
    assert second["traits"][0]["id"] == 2 and second["rerank_cache_hit"]
    ranker.rerank.assert_awaited_once()


async def test_literal_jargon_and_unrelated_style_are_not_rotated(retrieval):
    retriever, _ = retrieval
    cfg = DEFAULTS | {"selection_mode": "lexical", "max_traits": 1}
    relevant = STYLES[1] | {"situation": "谈心", "content": "谈心时倾听"}
    unrelated = STYLES[0] | {"situation": "比赛复盘", "content": "复盘操作"}
    strict = await retriever.select(
        "shared", [relevant, unrelated], "谈心", cfg, recent_expression_ids=[2]
    )
    assert strict["traits"][0]["id"] == 2 and "expression_rotation" not in strict
    word = STYLES[0] | {"id": 9, "kind": "jargon", "content": "LUX：平安到家"}
    literal = await retriever.select(
        "shared",
        STYLES + [word],
        "谈心 LUX",
        cfg,
        recent_expression_ids=[2, 9],
        literal_query="谈心 LUX",
    )
    assert literal["traits"][0]["id"] == 9
