"""Copy real SQLite text through the authenticated native memory writer."""

import json
import sqlite3
from contextlib import closing

import pytest

from astrbot_plugin_ovenheart.memory.livingmemory_import import read_source
from astrbot_plugin_ovenheart.memory.memory_panel import MemoryPanel
from tests.test_memory_panel import read
from tests.test_room_memory import SyntheticEmbedding, material
from tests.test_runtime import api_call

pytest_plugins = ["tests.test_room_memory"]


def test_missing_source_does_not_create_files(tmp_path):
    path = tmp_path / "missing" / "livingmemory.db"
    assert not read_source(path)["available"]
    with pytest.raises(ValueError, match="未找到"):
        read_source(path, {})
    assert not path.parent.exists()


async def test_scoped_batched_import_is_readonly_and_idempotent(
    memories, tmp_path, monkeypatch
):
    path = tmp_path / "livingmemory.db"
    monkeypatch.setattr(MemoryPanel, "livingmemory_path", property(lambda self: path))
    with closing(sqlite3.connect(path)) as source:
        source.execute("PRAGMA journal_mode=WAL")
        source.execute(
            "CREATE TABLE documents(id INTEGER PRIMARY KEY,doc_id TEXT,text TEXT,metadata TEXT,created_at TEXT)"
        )
        for index in range(5):
            source.execute(
                "INSERT INTO documents VALUES(?,?,?,?,?)",
                (
                    index + 1,
                    f"note-{index}",
                    f"成员的烤箱食谱记忆 {index}",
                    json.dumps(
                        {"session_id": "source-chat", "persona_id": "source-persona"}
                    ),
                    "2026-10-26 12:00:00",
                ),
            )
        for doc, text, metadata in [
            (
                "other",
                "其他会话的秘密",
                '{"session_id":"another","persona_id":"source-persona"}',
            ),
            ("blank", " ", "{}"),
            ("invalid", "无效来源", "not-json"),
        ]:
            source.execute(
                "INSERT INTO documents(doc_id,text,metadata) VALUES(?,?,?)",
                (doc, text, metadata),
            )
        source.commit()
        before = {
            file.name: file.read_bytes()
            for file in tmp_path.glob("livingmemory.db*")
            if not file.name.endswith("-shm")
        }
        embedding = SyntheticEmbedding()
        memories.settings["embedding_provider_id"] = "import-test"
        memories.context.get_provider_by_id = lambda name: embedding
        _, scope, _, _ = await material(memories)
        panel = memories.page_api.memory_panel
        preview = await api_call(panel.read, query=f"scope={scope}&view=livingmemory")
        assert preview["status"] == "ok", preview
        assert preview["data"]["excluded"] == 2
        group = next(
            g for g in preview["data"]["groups"] if g["session"] == "source-chat"
        )
        assert group["count"] == 5
        for replay in (False, True):
            after = imported = skipped = 0
            while True:
                response = await api_call(
                    panel.action,
                    {
                        "scope": scope,
                        "action": "import_livingmemory",
                        "payload": {
                            key: group[key] for key in ("session", "persona", "until")
                        }
                        | {"after": after},
                    },
                )
                assert response["status"] == "ok", response
                batch = response["data"]
                imported += batch["imported"]
                skipped += batch["skipped"]
                after = batch["after"]
                if batch["done"]:
                    break
            assert (imported, skipped) == ((0, 5) if replay else (5, 0))
        rows = read(memories, scope)["items"]
        assert len(rows) == 5 and all("秘密" not in r["text"] for r in rows)
        detail = read(memories, scope, identity=rows[0]["id"])["items"][0]
        assert detail["metadata"]["import_source"] == "LivingMemory"
        assert detail["metadata"]["source_session"] == "source-chat"
        _, other, _, _ = await material(
            memories, source_id="other", session="different"
        )
        assert not read(memories, other)["items"]
        assert not hasattr(memories, "memory")
        assert before == {
            file.name: file.read_bytes()
            for file in tmp_path.glob("livingmemory.db*")
            if not file.name.endswith("-shm")
        }
