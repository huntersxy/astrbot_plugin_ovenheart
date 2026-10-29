"""Guards that keep chat alive when Jev, memory prepare or schema upgrades fail."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.integrations.jev import JevService
from core.memory.room_memory import RoomMemory
from core.store import SCHEMA_COLUMNS, SCHEMA_VERSION, Store


@pytest.fixture
async def store(tmp_path):
    instance = Store(tmp_path / "ovenheart.sqlite3")
    await instance.call("initialize")
    yield instance
    await instance.close()


async def test_initialize_records_schema_version(store):
    connection = sqlite3.connect(store.path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute("SELECT version FROM schema_meta WHERE id=1").fetchone()
        assert row["version"] == SCHEMA_VERSION
        traits = {
            info["name"]
            for info in connection.execute("PRAGMA table_info(traits)").fetchall()
        }
        assert {"examples", "review_audit", "support_count"} <= traits
    finally:
        connection.close()


async def test_migrate_schema_adds_missing_columns(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE context_heads (
                scope TEXT PRIMARY KEY, cid TEXT NOT NULL, epoch TEXT NOT NULL,
                sequence INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL
            );
            CREATE TABLE decisions (
                id INTEGER PRIMARY KEY, scope TEXT NOT NULL, action TEXT NOT NULL,
                confidence REAL NOT NULL, reason TEXT NOT NULL, intent TEXT NOT NULL,
                mode TEXT NOT NULL, outcome TEXT NOT NULL, created REAL NOT NULL
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY, scope TEXT NOT NULL, source_id TEXT NOT NULL,
                sender TEXT NOT NULL, text TEXT NOT NULL, created REAL NOT NULL,
                consumed INTEGER NOT NULL DEFAULT 0, UNIQUE(scope, source_id)
            );
            CREATE TABLE runs (
                id INTEGER PRIMARY KEY, scope TEXT NOT NULL, status TEXT NOT NULL,
                started REAL NOT NULL, finished REAL, count INTEGER NOT NULL DEFAULT 0,
                message TEXT NOT NULL DEFAULT '', memory TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE traits (
                id INTEGER PRIMARY KEY, scope TEXT NOT NULL, kind TEXT NOT NULL,
                situation TEXT NOT NULL, content TEXT NOT NULL, confidence REAL NOT NULL,
                evidence TEXT NOT NULL, status TEXT NOT NULL, fingerprint TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL,
                UNIQUE(scope, fingerprint)
            );
            CREATE TABLE learning_runtime (
                scope TEXT PRIMARY KEY, attempted REAL NOT NULL DEFAULT 0,
                failures INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE scopes (
                id TEXT PRIMARY KEY, umo TEXT NOT NULL, persona TEXT NOT NULL,
                label TEXT NOT NULL, profile TEXT NOT NULL, updated REAL NOT NULL
            );
            CREATE TABLE applications (
                id INTEGER PRIMARY KEY, scope TEXT NOT NULL, event_key TEXT NOT NULL UNIQUE,
                trait_ids TEXT NOT NULL, mode TEXT NOT NULL, created REAL NOT NULL
            );
            CREATE TABLE auxiliary_usage (
                bucket INTEGER NOT NULL, stage TEXT NOT NULL, provider TEXT NOT NULL,
                model TEXT NOT NULL, calls INTEGER NOT NULL, successes INTEGER NOT NULL,
                errors INTEGER NOT NULL, cancelled INTEGER NOT NULL, reported INTEGER NOT NULL,
                input_cached INTEGER NOT NULL, input_other INTEGER NOT NULL,
                output INTEGER NOT NULL, system_chars INTEGER NOT NULL,
                prompt_chars INTEGER NOT NULL, seconds REAL NOT NULL,
                PRIMARY KEY(bucket,stage,provider,model)
            );
            CREATE TABLE context_messages (
                scope TEXT NOT NULL, epoch TEXT NOT NULL, source TEXT NOT NULL,
                sequence INTEGER NOT NULL, created REAL NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(scope,epoch,source)
            );
            CREATE TABLE embeddings (
                trait_id INTEGER NOT NULL, provider TEXT NOT NULL, digest TEXT NOT NULL,
                vector TEXT NOT NULL, PRIMARY KEY(trait_id, provider)
            );
            CREATE TABLE learning_sender_state (
                scope TEXT NOT NULL, sender TEXT NOT NULL, last_order INTEGER NOT NULL,
                updated REAL NOT NULL, PRIMARY KEY(scope,sender)
            );
            CREATE TABLE participation_state (
                scope TEXT PRIMARY KEY, last_reply REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE prompts (
                key TEXT PRIMARY KEY, body TEXT NOT NULL, updated REAL NOT NULL
            );
            CREATE TABLE revisions (
                id INTEGER PRIMARY KEY, scope TEXT NOT NULL, action TEXT NOT NULL,
                snapshot TEXT NOT NULL, created REAL NOT NULL
            );
            CREATE TABLE settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
            CREATE TABLE trait_aliases (
                scope TEXT NOT NULL, fingerprint TEXT NOT NULL, trait_id INTEGER NOT NULL,
                PRIMARY KEY(scope, fingerprint)
            );
            CREATE TABLE turn_traces (
                id INTEGER PRIMARY KEY, scope TEXT NOT NULL, event_key TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL, details TEXT NOT NULL, created REAL NOT NULL,
                updated REAL NOT NULL
            );
            CREATE TABLE memory_review_state (
                scope TEXT PRIMARY KEY, attempted REAL NOT NULL, details TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE self_portraits (
                persona TEXT PRIMARY KEY, description TEXT NOT NULL, image BLOB,
                mime_type TEXT NOT NULL, content_hash TEXT NOT NULL, updated REAL NOT NULL
            );
            """
        )
        connection.commit()
    finally:
        connection.close()

    store = Store(path)
    await store.call("initialize")
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        for table, columns in SCHEMA_COLUMNS.items():
            names = {
                info["name"]
                for info in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for name, _ in columns:
                assert name in names, f"{table}.{name} missing after migrate"
        assert (
            connection.execute("SELECT version FROM schema_meta WHERE id=1").fetchone()[
                "version"
            ]
            == SCHEMA_VERSION
        )
    finally:
        connection.close()
        await store.close()


async def test_migrate_schema_rejects_future_version(tmp_path):
    path = tmp_path / "future.sqlite3"
    store = Store(path)
    await store.call("initialize")
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "UPDATE schema_meta SET version=?", (SCHEMA_VERSION + 10,)
        )
        connection.commit()
    finally:
        connection.close()
    await store.close()

    upgraded = Store(path)
    with pytest.raises(ValueError, match="高于插件支持"):
        await upgraded.call("initialize")
    await upgraded.close()


async def test_jev_unavailable_plan_marks_fallback():
    profile = {
        "identity": "",
        "reply_style": "",
        "behavior": "",
        "learning_enabled": True,
        "apply_enabled": True,
    }
    plugin = SimpleNamespace(
        settings={
            "jev_enabled": True,
            "jev_participation_enabled": True,
            "jev_reply_threshold": 0.6,
            "jev_max_state_chars": 6000,
            "jev_timeout_sec": 4,
            "planner_max_wait": 30,
            "participation_silence_seconds": 0,
            "session_isolation": True,
            "mention_names": [],
        },
        settings_revision=1,
        prompts_revision=1,
        config={
            "jev_connection": {
                "api_key": "x",
                "base_url": "http://invalid",
                "model": "m",
            }
        },
        resolve_scope=AsyncMock(return_value=("a" * 24, "persona", "label")),
        permitted=AsyncMock(return_value=True),
        store=SimpleNamespace(
            call=AsyncMock(return_value={"scope": {"profile": profile}})
        ),
        context=SimpleNamespace(
            persona_manager=SimpleNamespace(
                get_persona_v3_by_id=lambda _: {"prompt": "identity"}
            )
        ),
        retriever=SimpleNamespace(
            select=AsyncMock(
                return_value={"traits": [], "mode": "lexical", "message": ""}
            )
        ),
        room_memory=SimpleNamespace(context=AsyncMock(return_value=[])),
    )
    service = JevService(plugin)
    service.client.evaluate = AsyncMock(
        return_value={"status": "error", "http_status": 503}
    )
    messages = [
        {
            "sequence": 1,
            "id": "1",
            "text": "你好啊，今天天气怎么样",
            "self": False,
            "replyable": True,
            "sender": "alice",
            "sender_id": "1",
            "created": 1,
        }
    ]
    state = {"scope": {"profile": profile}, "traits": []}
    plan = await service.plan(
        SimpleNamespace(),
        "a" * 24,
        "persona",
        state,
        messages,
        plugin.settings,
        False,
        False,
    )
    assert plan["jev_status"] == "unavailable"
    assert "回落" in plan["reason"]
    assert service.failures == 1


async def test_memory_degraded_skips_context(tmp_path):
    store = Store(tmp_path / "ovenheart.sqlite3")
    await store.call("initialize")
    plugin = SimpleNamespace(
        settings={
            "memory_enabled": True,
            "native_memory_enabled": True,
            "retrieval_timeout": 1,
        },
        store=store,
    )
    memory = RoomMemory(plugin)
    memory.mark_degraded("人格归属冲突")
    assert memory.status() == {
        "available": False,
        "degraded_reason": "人格归属冲突",
    }
    assert await memory.context(SimpleNamespace(), "a" * 24, "persona", "query") == []
    await store.close()
