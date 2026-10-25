"""Actual legacy DDL, SQLite snapshots, native SDK writes and source retirement."""

import ast
import asyncio
import hashlib
import importlib.util
import json
import sqlite3
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_ovenheart.legacy_import import LivingMemoryImport
from tests.test_room_memory import material
from tests.test_runtime import POLICY_PATH

pytest_plugins = ["tests.test_room_memory"]


@pytest.fixture
async def legacy(memories, tmp_path, monkeypatch):
    root = POLICY_PATH.parent.parent
    spec = importlib.util.spec_from_file_location(
        "legacy_fixture.core.memory_scope", POLICY_PATH
    )
    policy = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, policy)
    spec.loader.exec_module(policy)
    source = tmp_path / "legacy.db"
    incoming, scope, persona, _ = await material(
        memories, "import-owner", "准备迁入旧记忆"
    )
    with sqlite3.connect(source) as db:
        for filename, table in (
            (root / "core/managers/memory_engine.py", "documents"),
            (root / "core/managers/memory_engine.py", "memory_sources"),
            (root / "storage/atom_store.py", "memory_atoms"),
        ):
            sql = next(
                node.value
                for node in ast.walk(ast.parse(filename.read_text(encoding="utf-8")))
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and f"CREATE TABLE IF NOT EXISTS {table} (" in node.value
            )
            db.execute(sql)
        for identifier, text, origin, person in [
            (1, "周六的旧安排和新安排混在正文", "livingmemory:global", persona),
            (2, "群友更喜欢简洁的回答。", incoming.unified_msg_origin, persona),
            (3, "其他人格专属内容", "livingmemory:global", "other"),
            (4, "其他会话专属内容", "other:GroupMessage:456", persona),
        ]:
            metadata = {
                "session_id": origin,
                "persona_id": person,
                "status": "active",
                "create_time": time.time() - 1000,
                **({"atom_types": ["factual"]} if identifier == 1 else {}),
            }
            db.execute(
                "INSERT INTO documents(id,text,metadata) VALUES(?,?,?)",
                (identifier, text, json.dumps(metadata)),
            )
        sources = [
            {
                "id": 10,
                "sender_id": "alice",
                "role": "user",
                "content": "我周日去书店",
                "session_id": incoming.unified_msg_origin,
                "timestamp": time.time() - 1000,
            }
        ]
        db.execute(
            "INSERT INTO memory_sources VALUES (?,?,?,?)",
            (1, json.dumps(sources), time.time(), time.time()),
        )
        for identifier, text, status, expiry in [
            (1, "alice周日去书店。", "active", time.time() + 86400),
            (2, "alice周六去书店。", "forgotten", time.time() + 86400),
            (3, "上周临时状态。", "expired", time.time() - 1),
        ]:
            db.execute(
                "INSERT INTO memory_atoms(id,parent_memory_id,content,created_at,last_accessed_at,status,expires_at) VALUES(?,1,?,?,?,?,?)",
                (identifier, text, time.time() - 1000, time.time(), status, expiry),
            )
    owner = type("LegacyOwner", (), {"__module__": "legacy_fixture.main"})()
    owner.config_manager = {"filtering_settings": {"memory_scope_mode": "global"}}
    owner.initializer = SimpleNamespace(
        is_initialized=True, memory_engine=SimpleNamespace(db_path=str(source))
    )
    memories.context.get_registered_star = lambda name: (
        SimpleNamespace(activated=True, star_cls=owner)
        if name == "astrbot_plugin_livingmemory"
        else None
    )
    return memories, incoming, scope, persona, source, owner


async def run_import(p, scope):
    await p.legacy_import.start(scope)
    await asyncio.gather(*tuple(p.legacy_import.busy.values()))
    assert p.legacy_import.status[scope]["state"] == "completed", (
        p.legacy_import.status[scope]
    )
    return p.legacy_import.status[scope]


async def test_snapshot_import_uses_live_atoms_preserves_source_and_native_namespace(
    legacy,
):
    p, incoming, scope, persona, source, _ = legacy
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    preview = await p.legacy_import.preview(scope)
    assert (preview["documents"], preview["units"], preview["excluded_atoms"]) == (
        2,
        2,
        2,
    )
    assert preview["native"]["ready"] == 2
    assert not p.legacy_import.directory.exists()
    p.context.llm_generate.assert_not_awaited()
    status = await run_import(p, scope)
    assert status["stored"] == 2
    found = await p.room_memory.search(incoming, scope, persona, "书店")
    assert any("周日" in row["content"] for row in found)
    assert all("周六" not in row["content"] for row in found)
    imported = next(row for row in found if "周日" in row["content"])
    assert imported["metadata"]["evidence"] == []
    assert imported["metadata"]["legacy_sources"][0]["source_id"].startswith(
        "livingmemory:"
    )
    assert imported["metadata"]["legacy_sources"][0]["quote"] == "我周日去书店"
    assert await p.legacy_import.retired(incoming)
    assert await LivingMemoryImport(p).retired(incoming)
    callback = AsyncMock()
    await p.memory._dispatch(object(), callback, incoming)
    callback.assert_not_awaited()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    snapshot = p.legacy_import.directory / status["snapshot"]
    with sqlite3.connect(snapshot) as db:
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 4
        assert db.execute("SELECT COUNT(*) FROM memory_atoms").fetchone()[0] == 3
    p.context.llm_generate.assert_not_awaited()


async def test_waiting_import_survives_reload_can_cancel_and_uses_next_real_source(
    legacy,
):
    from astrbot_plugin_ovenheart.legacy_import import LivingMemoryImport

    p, _, scope, _, _, _ = legacy
    p.events.clear()
    result = await p.legacy_import.start(scope)
    assert result["state"] == "waiting_source"
    assert not p.legacy_import.directory.exists()
    assert await p.store.call("legacy_import_queue") == [scope]
    p.legacy_import = LivingMemoryImport(p)
    await p.legacy_import.restore()
    assert scope in p.legacy_import.pending
    await p.legacy_import.cancel(scope)
    assert not await p.store.call("legacy_import_queue")
    await p.legacy_import.start(scope)
    p.legacy_import = LivingMemoryImport(p)
    await p.legacy_import.restore()
    await material(p, "next-real-source")
    await asyncio.gather(*tuple(p.legacy_import.launching.values()))
    await asyncio.gather(*tuple(p.legacy_import.busy.values()))
    assert p.legacy_import.status[scope]["state"] == "completed"
    assert p.legacy_import.status[scope]["stored"] == 2
    assert not await p.store.call("legacy_import_queue")


async def test_reimport_does_not_revive_native_forgotten_records_or_widen_old_access(
    legacy,
):
    p, incoming, scope, persona, _, owner = legacy
    await run_import(p, scope)
    target = next(
        row
        for row in await p.room_memory.search(incoming, scope, persona, "书店")
        if "周日" in row["content"]
    )
    current, scope, persona, messages = await material(
        p, "forget-import", "忘掉去书店的那条旧记忆"
    )
    forgotten = await p.room_memory.change(
        current,
        scope,
        persona,
        "forget_memory",
        {
            "memory_id": target["id"],
            "evidence": [{"msg_id": "1", "quote": "忘掉去书店的那条旧记忆"}],
        },
        messages,
    )
    assert forgotten["success"]
    repeated = await run_import(p, scope)
    assert repeated["stored"] == 0 and repeated["skipped"] == 2
    assert not any(
        "周日" in row["content"]
        for row in await p.room_memory.search(current, scope, persona, "书店")
    )
    assert await p.legacy_import.retired(current)
    owner.config_manager["access_control"] = {
        "whitelist_enabled": True,
        "allowed_ids": "alice",
    }
    with pytest.raises(ValueError, match="部分成员"):
        await p.legacy_import.preview(scope)
