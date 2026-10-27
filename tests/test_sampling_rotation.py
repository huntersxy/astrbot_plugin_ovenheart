"""Rotate capacity-limited target groups after successful source processing."""

import sqlite3
from collections import Counter

import pytest

from astrbot_plugin_ovenheart.core.store import Store
from tests.test_store import DEFAULTS

pytest_plugins = ["tests.test_store"]
TARGETS = ["alice", "bob", "carol"]


@pytest.fixture
async def rotation_queue(store):
    """Queue oldest-first sources that cannot all fit in one learning batch.

    Args:
        store: Temporary SQLite store.

    Returns:
        Store and isolated native source scope.
    """
    scope = "a" * 24
    for sender in TARGETS:
        for index in range(8):
            await store.call(
                "capture",
                scope,
                "qq:GroupMessage:123",
                "default",
                "fixture",
                f"{sender}-{index}",
                sender,
                f"{sender} source {index}",
                DEFAULTS,
            )
    return store, scope


async def test_small_batches_rotate_all_targets_across_restart(rotation_queue):
    store, scope = rotation_queue
    processed = Counter()
    for turn in range(3):
        batch = await store.call("batch", scope, 4, TARGETS, [], 2)
        assert sorted(Counter(m["sender"] for m in batch).values()) == [2, 2]
        assert [m["id"] for m in batch] == sorted(m["id"] for m in batch)
        processed.update(m["sender"] for m in batch)
        run = await store.call("start_run", scope)
        await store.call(
            "finish_run",
            run,
            scope,
            batch,
            [],
            DEFAULTS | {"target_senders": TARGETS},
            "fixture",
        )
        if turn == 0:
            store = Store(store.path)
            await store.call("initialize")
    assert processed == {"alice": 4, "bob": 4, "carol": 4}


async def test_failure_preserves_rotation_and_another_scope_starts_independently(
    rotation_queue,
):
    store, scope = rotation_queue
    batch = await store.call("batch", scope, 4, TARGETS)
    run = await store.call("start_run", scope)
    await store.call(
        "finish_run", run, scope, batch, [], DEFAULTS, "fixture", "fixture failure"
    )
    assert await store.call("batch", scope, 4, TARGETS) == batch
    with sqlite3.connect(store.path) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM learning_sender_state").fetchone()[0] == 0
        )
    run = await store.call("start_run", scope)
    await store.call("finish_run", run, scope, batch, [], DEFAULTS, "fixture")
    assert "carol" in {
        m["sender"] for m in await store.call("batch", scope, 4, TARGETS)
    }
    other = "b" * 24
    for sender in TARGETS:
        for index in range(2):
            await store.call(
                "capture",
                other,
                "qq:GroupMessage:456",
                "default",
                "fixture",
                f"{sender}-{index}",
                sender,
                "Other scoped source",
                DEFAULTS,
            )
    assert {m["sender"] for m in await store.call("batch", other, 4, TARGETS)} == {
        "alice",
        "bob",
    }


@pytest.mark.parametrize("cleanup", ["expiry", "clear"])
async def test_rotation_metadata_expires_or_clears(rotation_queue, cleanup):
    store, scope = rotation_queue
    batch = await store.call("batch", scope, 4, TARGETS)
    run = await store.call("start_run", scope)
    await store.call("finish_run", run, scope, batch, [], DEFAULTS, "fixture")
    if cleanup == "expiry":
        with sqlite3.connect(store.path) as db:
            db.execute("UPDATE learning_sender_state SET updated=0")
        await store.call("prune", 14)
    else:
        await store.call("mutate", scope, "clear_messages", {})
    with sqlite3.connect(store.path) as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM learning_sender_state WHERE scope=?", (scope,)
            ).fetchone()[0]
            == 0
        )
    assert not (await store.call("state", scope))["traits"]
    assert len(await store.call("batch", scope, 100)) == (
        20 if cleanup == "expiry" else 0
    )
