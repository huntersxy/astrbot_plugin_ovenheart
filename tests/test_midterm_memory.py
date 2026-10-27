"""Exercise real context eviction, native persistence and original recall ranking."""

import asyncio
import json
import sqlite3
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from astrbot.core.db.po import PlatformMessageHistory
from astrbot.core.db.sqlite import SQLiteDatabase
from astrbot.core.platform_message_history_mgr import PlatformMessageHistoryManager

from tests.test_room_memory import SyntheticEmbedding, material
from tests.test_structured_conversation import incoming

pytest_plugins = ["tests.test_structured_conversation"]


def test_midterm_cutoff_includes_older_rows_outside_the_result_limit(tmp_path):
    from astrbot_plugin_ovenheart.memory.room_memory import RoomState

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE paragraphs (hash TEXT, source TEXT, metadata TEXT, created_at INT)")
    scope = tmp_path / "scope"
    for key, created, end in (("old", 1, 900), ("middle", 2, 200), ("new", 3, 300)):
        db.execute(
            "INSERT INTO paragraphs VALUES (?, ?, ?, ?)",
            (key, "chat_summary:scope", json.dumps({"midterm": {"cid": "chat", "end": end}}), created),
        )

    class Metadata:
        _conn = db

        def get_paragraphs_by_hashes(self, hashes):
            return {key: {"hash": key, "source": "s", "content": key, "metadata": {}} for key in hashes}

    room = RoomState(scope, asyncio.Lock())
    room.metadata = Metadata()
    room.kernel = SimpleNamespace(
        _get_search_hit_service=lambda: SimpleNamespace(_filter_user_visible_hits=lambda hits: hits)
    )
    try:
        result = room._midterm_records("chat", 2)
        assert result["cutoff"] == 900
        assert {row["hash"] for row in result["records"]} == {"middle", "new"}
        assert room._midterm_records("missing", 2) == {"cutoff": 0, "records": []}
    finally:
        db.close()


async def test_evicted_native_context_compresses_recalls_and_obeys_forgetting(
    structured,
):
    p = structured
    p.settings.update(
        learning_enabled=True,
        person_memory_enabled=False,
        memory_summary_enabled=False,
        native_memory_enabled=True,
        embedding_provider_id="semantic",
    )
    p.context.get_provider_by_id = lambda name: (
        SyntheticEmbedding() if name == "semantic" else None
    )
    p.context.llm_generate.return_value = SimpleNamespace(
        completion_text=json.dumps(
            {
                "summary": "alice和朋友说好周六去书店买绘画书。",
                "recall_cues": ["之前书店之约，想买绘画书时回想这次安排"],
            },
            ensure_ascii=False,
        )
    )
    for index in range(52):
        message = incoming(f"history-{index}", f"周六去书店买绘画书，讨论细节{index}。")
        await p.capture(message)
    await asyncio.gather(*tuple(p.midterm_memory.busy.values()))
    scope, persona, _ = await p.resolve_scope(message)
    status = await p.room_memory.call(scope, "midterm_records", "dialog-a", 10)
    assert len(status["records"]) == 1
    record = status["records"][0]
    assert len(record["metadata"]["evidence"]) == 12
    assert {e["source_id"] for e in record["metadata"]["evidence"]} == {
        f"history-{i}" for i in range(12)
    }
    recalled = await p.midterm_memory.context(
        message, scope, persona, [{"text": "上次说的书店之约"}]
    )
    assert recalled[0]["id"] == record["hash"]
    assert "【聊天回想-内部参考】" in recalled[0]["content"]
    assert not await p.midterm_memory.context(
        message, scope, persona, [{"text": "猫咪"}]
    )
    current, scope, persona, messages = await material(
        p, "forget-trip", "忘掉去书店的安排"
    )
    result = await p.room_memory.change(
        current,
        scope,
        persona,
        "forget_memory",
        {
            "memory_id": record["hash"],
            "evidence": [{"msg_id": "1", "quote": "忘掉去书店"}],
        },
        messages,
    )
    assert result["success"], result
    assert not await p.midterm_memory.context(
        current, scope, persona, [{"text": "书店"}]
    )
    progress = await p.room_memory.call(scope, "midterm_records", "dialog-a", 10)
    assert progress["cutoff"] == status["cutoff"]
    await p.auxiliary.flush()
    assert (
        next(
            r
            for r in await p.store.call("auxiliary_usage")
            if r["stage"] == "midterm_summary"
        )["calls"]
        == 1
    )


async def test_native_host_history_compresses_without_an_owned_chat_ledger(
    participant, tmp_path
):
    p = participant
    p.settings.update(
        context_mode="native",
        native_memory_enabled=True,
        memory_summary_enabled=False,
        person_memory_enabled=False,
    )
    p.context.get_provider_by_id = lambda name: None
    p.context.llm_generate.return_value = SimpleNamespace(
        completion_text=json.dumps(
            {"summary": "群友约好周六去书店。", "recall_cues": ["周六去书店"]},
            ensure_ascii=False,
        )
    )
    message = incoming("native-boundary", "继续聊聊周六的安排")
    p.context.conversation_manager.get_curr_conversation_id.return_value = "native-conv"
    scope, persona, _ = await p.resolve_scope(message)
    await p.store.call(
        "touch_scope", scope, message.unified_msg_origin, persona, "群聊"
    )
    database = SQLiteDatabase(str(tmp_path / "history.db"))
    base = time.time() - 100
    try:
        async with database.get_db() as session:
            session.add_all(
                [
                    PlatformMessageHistory(
                        platform_id=message.get_platform_id(),
                        user_id=message.unified_msg_origin,
                        sender_id="alice",
                        sender_name="Alice",
                        content={
                            "type": "user",
                            "message": [{"type": "plain", "text": f"书店之约细节-{i}"}],
                        },
                        created_at=datetime.fromtimestamp(base + i, timezone.utc),
                    )
                    for i in range(60)
                ]
            )
            await session.commit()
        p.context.message_history_manager = PlatformMessageHistoryManager(database)
        p.midterm_memory.seen[scope] = 11
        await p.midterm_memory.observe_native(message, scope, persona)
        await asyncio.gather(*tuple(p.midterm_memory.busy.values()))
        result = await p.room_memory.call(scope, "midterm_records", "native-conv", 10)
        assert len(result["records"]) == 1
        metadata = result["records"][0]["metadata"]
        assert len(metadata["evidence"]) == 30
        assert all(e["source_id"].startswith("platform:") for e in metadata["evidence"])
        assert not metadata["midterm"]["recall_cues"]
        assert not p.participation.windows
        p.settings["learning_enabled"] = False
        p.midterm_memory.attempted.clear()
        await p.midterm_memory.observe_native(message, scope, persona)
        assert not p.midterm_memory.busy
        assert p.context.llm_generate.await_count == 1
    finally:
        await database.engine.dispose()
