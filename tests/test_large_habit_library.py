"""Keep older habits and human decisions eligible beyond dashboard bounds."""

import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

from astrbot_plugin_ovenheart.settings import SHARED_SCOPE
from tests.test_store import seed

pytest_plugins = [
    "tests.test_store",
    "tests.test_runtime",
    "tests.test_learning_retrieval",
]


@pytest.fixture
async def crowded_library(known_library):
    """Preindex an old entry, then add more than a thousand newer candidates.

    Args:
        known_library: Native fixture with an old rejected reference.

    Returns:
        Plugin, native scope, old ID and a simulated embedding provider.
    """
    plugin, scope, old, provider = known_library
    original = next(
        t for t in (await plugin.store.call("state", scope))["traits"] if t["id"] == old
    )
    await plugin.retriever.semantic_scores(
        scope, [original], "", plugin.settings, index_only=True
    )
    with sqlite3.connect(plugin.store.path) as db:
        db.executemany(
            "INSERT INTO traits(scope,kind,situation,content,confidence,evidence,status,fingerprint,created,updated) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    scope,
                    "behavior",
                    "比赛复盘",
                    f"无关策略{i}",
                    0.95,
                    "[]",
                    "active",
                    f"crowd-{i}",
                    time.time(),
                    time.time(),
                )
                for i in range(1001)
            ],
        )
    provider.get_embeddings.reset_mock()
    return plugin, scope, old, provider


async def test_cached_old_habit_still_participates_in_large_runtime_recall(
    crowded_library,
):
    plugin, scope, old, provider = crowded_library
    with sqlite3.connect(plugin.store.path) as db:
        db.execute("UPDATE traits SET status='active' WHERE id=?", (old,))
    state = await plugin.store.call("guidance", scope)
    assert old in {t["id"] for t in state["traits"]}
    assert len(state["traits"]) > 1000
    assert all("evidence" not in t for t in state["traits"])
    result = await plugin.retriever.select(
        scope,
        state["traits"],
        "心里堵得慌",
        plugin.settings | {"selection_mode": "hybrid", "max_traits": 1},
    )
    assert [t["id"] for t in result["traits"]] == [old]
    assert len(provider.get_embeddings.await_args.args[0]) == 64
    plugin.context.llm_generate.assert_not_awaited()


async def test_old_rejection_is_offered_and_cannot_be_relearned_after_1000_entries(
    crowded_library,
):
    plugin, scope, old, _ = crowded_library
    plugin.settings.update(
        learn_personality=False, learn_expression=False, learn_jargon=False
    )
    offered = []

    async def completion(**kwargs):
        """Request consolidation with the genuinely offered rejected target.

        Args:
            **kwargs: Real learning model arguments.

        Returns:
            A controlled evidence-linked learning proposal.
        """
        payload = json.loads(kwargs["prompt"])
        offered.extend(payload["known_traits"])
        return SimpleNamespace(
            completion_text=json.dumps(
                {
                    "traits": [
                        {
                            "kind": "behavior",
                            "situation": "心情低落时",
                            "content": "先认真听对方倾诉",
                            "confidence": 0.95,
                            "evidence_ids": [m["id"] for m in payload["observations"]],
                            "merge_id": old,
                        }
                    ]
                }
            )
        )

    plugin.context.llm_generate.side_effect = completion
    result = await plugin.learn(scope)
    assert result["status"] == "success" and result["count"] == 0
    assert next(t for t in offered if t["id"] == old)["status"] == "rejected"
    assert len(offered) <= 80 and plugin.context.llm_generate.await_count == 1
    with sqlite3.connect(plugin.store.path) as db:
        assert (
            db.execute("SELECT status FROM traits WHERE id=?", (old,)).fetchone()[0]
            == "rejected"
        )
        assert db.execute("SELECT COUNT(*) FROM traits").fetchone()[0] == 1062


async def test_compact_learning_scope_matches_writable_shared_targets(store):
    source, other = "a" * 24, "b" * 24
    await seed(store, source)
    await seed(store, other)
    rows = [
        (source, "expression", "active"),
        (other, "expression", "active"),
        (source, "jargon", "active"),
        (other, "jargon", "active"),
        (SHARED_SCOPE, "jargon", "active"),
        (SHARED_SCOPE, "behavior", "rejected"),
        (SHARED_SCOPE, "expression", "active"),
    ]
    with sqlite3.connect(store.path) as db:
        for index, (scope, kind, status) in enumerate(rows, 1):
            db.execute(
                "INSERT INTO traits(id,scope,kind,situation,content,confidence,evidence,status,fingerprint,created,updated) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    index,
                    scope,
                    kind,
                    "fixture",
                    f"scope fixture{index}",
                    0.9,
                    "[]",
                    status,
                    str(index),
                    1,
                    1,
                ),
            )
    runtime = await store.call("guidance", source, True)
    shared_learning = await store.call("guidance", source, True, True)
    isolated_learning = await store.call("guidance", source, False, True)
    assert {t["id"] for t in runtime["traits"]} == {1, 2, 3, 7}
    assert {t["id"] for t in shared_learning["traits"]} == {3, 6, 7}
    assert {t["id"] for t in isolated_learning["traits"]} == {1, 3}
    assert shared_learning["scope"]["umo"] == "qq:GroupMessage:123"
