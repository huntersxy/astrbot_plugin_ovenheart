"""Verify review triage, historical recovery and atomic operator selections."""

import sqlite3

import pytest

from astrbot_plugin_ovenheart.core.learning import (
    LEARNING_REVIEW_SYSTEM,
    LEARNING_SYSTEM,
    LEARNING_TRANSFER_RULE,
    review_issue,
)
from tests.test_store import DEFAULTS, TRAIT, seed

pytest_plugins = ["tests.test_store"]


async def add(store, scope="a" * 24, approved=False):
    """Create a real reviewed high-score candidate.

    Args:
        store: Temporary SQLite store.
        scope: Native owner scope.
        approved: Independent reviewer verdict.

    Returns:
        Newly stored library entry.
    """
    batch = await seed(store, scope)
    reason = "没有机器人自身发言作为证据，不能认定机器人日常以单行短句为主；旧习惯不能替代本批证据。"
    audit = {
        "approved": approved,
        "reason": "" if approved else reason,
        "issue": "" if approved else review_issue(reason),
        **{key: TRAIT[key] for key in ("kind", "situation", "content")},
        "evidence_ids": [m["id"] for m in batch[:2]],
        "examples": [],
    }
    trait = TRAIT | {
        "evidence": [dict(m) for m in batch[:2]],
        "_review_approved": approved,
        "_review_audit": audit,
    }
    run = await store.call("start_run", scope)
    await store.call(
        "finish_run",
        run,
        scope,
        batch,
        [trait],
        DEFAULTS,
        "",
        None,
        None,
        None,
        {"enabled": True, "candidates": [audit]},
    )
    return (await store.call("library", scope))["traits"][0]


async def test_high_score_does_not_override_review_and_bulk_decision_is_restorable(
    store,
):
    trait = await add(store)
    assert trait["confidence"] == 0.95 and trait["status"] == "pending"
    assert trait["review"]["approved"] is False
    assert trait["review"]["issue"] == "learning_target"
    library = await store.call("library", "all", "all", "pending", 0, "learning_target")
    assert library["total"] == 1 and library["review_counts"]["flagged"] == 1
    entry = {key: trait[key] for key in ("id", "scope", "updated")}
    assert await store.call("review_batch", [entry], "rejected") == 1
    after = (await store.call("library", "all", "all", "rejected"))["traits"][0]
    assert after["manual"] == 1 and after["review"] == trait["review"]
    revision = (await store.call("state", trait["scope"]))["revisions"][0]
    await store.call("mutate", trait["scope"], "restore", {"id": revision["id"]})
    restored = (await store.call("library"))["traits"][0]
    assert restored["review"] == trait["review"]
    await store.call(
        "mutate",
        trait["scope"],
        "trait",
        {"id": trait["id"], "content": "编辑后的规则"},
    )
    assert (await store.call("library", "all", "all", "pending", 0, "unknown"))[
        "total"
    ] == 1


async def test_bulk_review_rejects_entire_stale_cross_scope_selection(store):
    first = await add(store, approved=True)
    second = await add(store, "b" * 24)
    entries = [
        {key: t[key] for key in ("id", "scope", "updated")} for t in (first, second)
    ]
    await store.call(
        "mutate", second["scope"], "trait", {"id": second["id"], "content": "有改动"}
    )
    with pytest.raises(ValueError, match="全部未处理"):
        await store.call("review_batch", entries, "active")
    assert (await store.call("library"))["total"] == 2
    with pytest.raises(ValueError):
        await store.call("review_batch", [entries[0], entries[0]], "active")
    assert await store.call("review_batch", [entries[0]], "active") == 1
    assert (await store.call("library", "all", "all", "active", 0, "passed"))[
        "total"
    ] == 1




async def test_counts_cover_all_237_entries_before_review_filter_and_pagination(store):
    first = await add(store, approved=True)
    with sqlite3.connect(store.path) as db:
        for index in range(236):
            db.execute(
                "INSERT INTO traits(scope,kind,situation,content,confidence,evidence,status,fingerprint,created,updated) VALUES (?,'expression',?,'待观察',.99,'[]','pending',?,1,1)",
                (first["scope"], str(index), str(index)),
            )
    page = await store.call("library", "all", "expression", "pending", 200)
    assert page["total"] == 237 and len(page["traits"]) == 37
    assert page["review_counts"] == {"passed": 1, "flagged": 0, "unknown": 236}
    selected = await store.call(
        "library", "all", "expression", "pending", 200, "passed"
    )
    assert selected["total"] == 1 and selected["offset"] == 0
    assert selected["traits"][0]["id"] == first["id"]


def test_human_expression_transfer_is_explicit_in_both_default_prompts():
    assert LEARNING_TRANSFER_RULE in LEARNING_SYSTEM
    assert LEARNING_TRANSFER_RULE in LEARNING_REVIEW_SYSTEM
    assert "没有机器人自身发言不是拒绝表达候选的理由" in LEARNING_TRANSFER_RULE
