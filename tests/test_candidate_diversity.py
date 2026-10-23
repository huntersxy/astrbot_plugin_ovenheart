"""Keep useful alternatives visible when vector-near expressions crowd recall."""

import json
import math
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot_plugin_ovenheart.retrieval import diverse_expression_pool
from astrbot_plugin_ovenheart.settings import DEFAULTS
from tests.test_retrieval import TRAITS

pytest_plugins = ["tests.test_retrieval"]


async def test_crowded_selector_can_choose_an_alternative_and_reuse_existing_caches(
    retrieval,
):
    retriever, provider = retrieval
    traits = [
        TRAITS[0] | {"id": i, "content": f"先轻声接住情绪，第{i}种说法", "updated": 1}
        for i in range(1, 25)
    ] + [
        TRAITS[0] | {"id": 25, "content": "用简短的具体肯定接话"},
        TRAITS[0] | {"id": 26, "content": "无关的游戏技巧"},
        TRAITS[0] | {"id": 27, "kind": "behavior", "content": "先核实信息再答"},
    ]
    with sqlite3.connect(retriever.store.path) as db:
        db.executemany(
            "INSERT OR REPLACE INTO traits(id,scope,kind,situation,content,confidence,evidence,status,fingerprint,created,updated) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
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
                    t["updated"],
                )
                for t in traits
            ],
        )
    provider.get_embeddings.return_value = [
        [0.9, math.sqrt(1 - 0.9**2), 0.0] for _ in range(24)
    ] + [
        [0.88, 0.0, math.sqrt(1 - 0.88**2)],
        [0.3, 0.0, math.sqrt(1 - 0.3**2)],
        [1.0, 0.0, 0.0],
    ]
    provider.get_embedding.return_value = [1.0, 0.0, 0.0]
    retriever.context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(completion_text='{"selected_ids":[25]}')
    )
    settings = DEFAULTS | {"embedding_provider_id": "embed"}
    first = await retriever.select(
        "scope-a", traits, "feeling overwhelmed", settings, provider_id="chat"
    )
    offered = json.loads(retriever.context.llm_generate.await_args.kwargs["prompt"])[
        "candidates"
    ]
    assert len(offered) == 24
    assert {1, 25, 27} <= {t["id"] for t in offered}
    assert 26 not in {t["id"] for t in offered}
    assert first["expression_candidate_promotions"] == [25]
    assert [t["id"] for t in first["traits"]] == [25]
    second = await retriever.select(
        "scope-a", traits, "feeling overwhelmed", settings, provider_id="chat"
    )
    assert second["selector_cache_hit"]
    assert (
        provider.get_embeddings.await_count == provider.get_embedding.await_count == 1
    )
    retriever.context.llm_generate.assert_awaited_once()
    preview = await retriever.select(
        "scope-a", traits, "feeling overwhelmed", settings, semantic=False
    )
    assert "expression_candidate_promotions" not in preview
    assert (
        provider.get_embeddings.await_count == provider.get_embedding.await_count == 1
    )
    retriever.context.llm_generate.assert_awaited_once()


def test_fixed_slots_and_strong_relevance_survive_diversity_without_mutation():
    fixed = [
        {"id": 90, "kind": "behavior", "_score": 2},
        {"id": 91, "kind": "jargon", "_score": 1.9},
        {"id": 92, "kind": "expression", "_score": 1.8},  # Not yet indexed.
    ]
    ranked = fixed + [
        {"id": i, "kind": "expression", "_score": 1} for i in range(1, 25)
    ]
    vectors = {i: [1.0, 0.0] for i in range(1, 25)}
    ranked.append({"id": 25, "kind": "expression", "_score": 0.3})
    vectors[25] = [0.0, 1.0]
    original = list(ranked)
    result = diverse_expression_pool(ranked, vectors)
    assert result == original[:24]  # Novelty cannot rescue a much weaker match.
    ranked[-1] = ranked[-1] | {"_score": 0.98}
    result = diverse_expression_pool(ranked, vectors)
    assert result[:3] == fixed and result[3]["id"] == 1
    assert len(result) == 24 and result[-1]["id"] == 25
    assert ranked[:27] == original[:27] and len(ranked) == 28
