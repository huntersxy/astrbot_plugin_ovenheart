"""Exercise transaction, review and rollback invariants using a real SQLite DB."""

import asyncio

import pytest

from astrbot_plugin_ovenheart.settings import DEFAULTS
from astrbot_plugin_ovenheart.store import Store


@pytest.fixture
async def store(tmp_path):
    instance = Store(tmp_path / "persona.sqlite3")
    await instance.call("initialize")
    return instance


async def seed(store, scope="a" * 24, count=3):
    """Insert a real batch and return its snapshot.

    Args:
        store: Temporary database store.
        scope: Target session/persona key.
        count: Message count.

    Returns:
        Pending source messages.
    """
    for i in range(count):
        await store.call(
            "capture",
            scope,
            "qq:GroupMessage:123",
            "default",
            "测试群",
            str(i),
            "alice",
            f"原始消息 {i}",
            DEFAULTS,
        )
    return await store.call("batch", scope, 30)


TRAIT = {
    "kind": "expression",
    "situation": "开玩笑时",
    "content": "简短反问",
    "confidence": 0.95,
    "evidence": [
        {"id": 1, "sender": "alice", "text": "原始消息", "created": 1},
        {"id": 2, "sender": "alice", "text": "原始消息2", "created": 2},
    ],
}


async def test_capture_deduplicates_and_separates_scope(store):
    await asyncio.gather(*(seed(store) for _ in range(3)))
    await seed(store, "b" * 24)
    assert len(await store.call("batch", "a" * 24, 30)) == 3
    assert len(await store.call("batch", "b" * 24, 30)) == 3


async def test_failure_preserves_sources_and_success_consumes_only_snapshot(store):
    scope = "a" * 24
    batch = await seed(store)
    run = await store.call("start_run", scope)
    await store.call("finish_run", run, scope, batch, [], DEFAULTS, "", "模型请求超时")
    assert len(await store.call("batch", scope, 30)) == 3
    await seed(store, count=4)
    run = await store.call("start_run", scope)
    await store.call("finish_run", run, scope, batch, [TRAIT], DEFAULTS, "未启用记忆")
    remaining = await store.call("batch", scope, 30)
    assert len(remaining) == 1 and remaining[0]["source_id"] == "3"
    state = await store.call("state", scope)
    assert state["traits"][0]["status"] == "pending"
    assert state["runs"][0]["status"] == "success"


async def test_auto_activation_needs_threshold_and_preserves_human_rejection(store):
    scope = "a" * 24
    batch = await seed(store)
    settings = DEFAULTS | {"activation_mode": "auto"}
    run = await store.call("start_run", scope)
    await store.call(
        "finish_run",
        run,
        scope,
        batch,
        [TRAIT, TRAIT | {"content": "低置信度", "confidence": 0.6}],
        settings,
        "",
    )
    traits = (await store.call("state", scope))["traits"]
    approved = next(t for t in traits if t["status"] == "active")
    assert any(t["status"] == "pending" for t in traits)
    await store.call(
        "mutate",
        scope,
        "trait",
        {"id": approved["id"], "status": "rejected", "content": "人工已修改"},
    )
    run = await store.call("start_run", scope)
    await store.call("finish_run", run, scope, batch, [TRAIT], settings, "")
    stored = next(
        t
        for t in (await store.call("state", scope))["traits"]
        if t["id"] == approved["id"]
    )
    assert stored["status"] == "rejected" and stored["content"] == "人工已修改"


async def test_restore_is_scoped_and_reversible(store):
    scope = "a" * 24
    batch = await seed(store)
    await seed(store, "b" * 24)
    await store.call("mutate", scope, "profile", {"identity": "温和且有边界"})
    revision = (await store.call("state", scope))["revisions"][0]["id"]
    run = await store.call("start_run", scope)
    await store.call(
        "finish_run",
        run,
        scope,
        batch,
        [TRAIT],
        DEFAULTS | {"activation_mode": "auto"},
        "",
    )
    with pytest.raises(ValueError):
        await store.call("mutate", "b" * 24, "restore", {"id": revision})
    await store.call("mutate", scope, "restore", {"id": revision})
    state = await store.call("state", scope)
    assert state["scope"]["profile"]["identity"] == ""
    assert state["traits"][0]["status"] == "disabled"
    undo = state["revisions"][0]["id"]
    await store.call("mutate", scope, "restore", {"id": undo})
    state = await store.call("state", scope)
    assert state["scope"]["profile"]["identity"] == "温和且有边界"
    assert state["traits"][0]["status"] == "active"


async def test_restart_marks_incomplete_runs_and_keeps_pending(store):
    scope = "a" * 24
    await seed(store)
    await store.call("start_run", scope)
    await store.call("initialize")
    state = await store.call("state", scope)
    assert state["runs"][0]["status"] == "error" and state["scope"]["pending"] == 3


async def test_persisted_pause_is_checked_inside_commit_transaction(store):
    """Reject a batch even if its in-memory settings snapshot predates a pause.

    Args:
        store: Temporary SQLite store.
    """
    scope = "a" * 24
    batch = await seed(store)
    run = await store.call("start_run", scope)
    await store.call("settings", {"learning_enabled": False})
    with pytest.raises(ValueError, match="暂停"):
        await store.call("finish_run", run, scope, batch, [TRAIT], DEFAULTS, "")
    state = await store.call("state", scope)
    assert state["scope"]["pending"] == 3 and not state["traits"]


async def test_persisted_target_change_is_checked_inside_commit_transaction(store):
    scope = "a" * 24
    batch = await seed(store)
    run = await store.call("start_run", scope)
    await store.call("settings", {"target_senders": ["new-target"]})
    with pytest.raises(ValueError, match="学习对象"):
        await store.call(
            "finish_run", run, scope, batch, [TRAIT], DEFAULTS, "", "", None, []
        )
    assert not await store.call(
        "batch", scope, 100, ["new-target", "x'); DROP TABLE messages; --"]
    )
    state = await store.call("state", scope)
    assert state["scope"]["pending"] == 3 and not state["traits"]


async def test_queued_capture_rechecks_persisted_target_and_pause(store):
    scope = "b" * 24
    await store.call("settings", {"target_senders": ["new-target"]})
    assert not await store.call(
        "capture",
        scope,
        "qq:g:1",
        "default",
        "test",
        "old",
        "old-target",
        "迟到的旧对象消息",
        DEFAULTS,
    )
    assert not (await store.call("state"))["scopes"]
    await store.call("settings", {"learning_enabled": False})
    assert not await store.call(
        "capture",
        scope,
        "qq:g:1",
        "default",
        "test",
        "peer",
        "peer",
        "暂停后的旁人语境",
        DEFAULTS,
        True,
    )
