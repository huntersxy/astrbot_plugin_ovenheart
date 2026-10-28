"""Drive original feedback jobs through live native messages and permissions."""

import json
import sqlite3
import time
from datetime import datetime, timezone
from types import MethodType, SimpleNamespace

import pytest

from tests.test_room_memory import material, note
from tests.test_runtime import event

pytest_plugins = ["tests.test_room_memory"]


async def test_background_identity_does_not_create_chat_traces(
    memories, tmp_path, monkeypatch
):
    from astrbot.core.utils import trace
    from astrbot.core.utils.trace_store import TraceStore

    store = TraceStore(tmp_path / "traces.db", {})
    monkeypatch.setitem(trace.astrbot_config, "trace_enable", True)
    monkeypatch.setattr(trace, "get_trace_store", lambda *_: store)
    try:
        incoming, scope, persona, messages = await material(memories)
        incoming.role = "admin"
        await memories.room_memory.remember(incoming, scope, persona, note(), messages)
        for _ in range(20):
            await memories.room_memory.observe(incoming, scope)
        identity = memories.room_memory.rooms[scope].identity
        assert identity.role == "member"
        assert await memories.resolve_scope(identity) == await memories.resolve_scope(
            incoming
        )
        assert identity.get_message_str() == "" and identity.get_messages() == []
        incoming.trace.record("pipeline_end", status="success")
        assert store.flush()
        with sqlite3.connect(store.path) as db:
            assert db.execute(
                "SELECT state,count(*) FROM traces GROUP BY state"
            ).fetchall() == [("completed", 1)]
    finally:
        store.close()


async def read_sdk_window(state, scope, start, end):
    from astrbot_plugin_ovenheart.vendor.maibot.amemorix.sdk_host import message_service

    return [
        m.processed_plain_text
        for m in message_service.get_messages_by_time_in_chat(
            chat_id=scope,
            start_time=start,
            end_time=end,
            limit=2,
            limit_mode="latest",
            filter_mai=True,
            filter_command=True,
        )
    ]


async def test_native_host_history_reads_the_requested_window_beyond_the_latest_page(
    memories, tmp_path
):
    from astrbot.core.db.po import PlatformMessageHistory
    from astrbot.core.db.sqlite import SQLiteDatabase
    from astrbot.core.platform_message_history_mgr import PlatformMessageHistoryManager

    plugin = memories
    incoming, scope, persona, messages = await material(plugin)
    await plugin.room_memory.remember(incoming, scope, persona, note(), messages)
    database = SQLiteDatabase(str(tmp_path / "native-history.db"))
    base = time.time() - 1000
    try:
        async with database.get_db() as session:
            session.add_all(
                [
                    PlatformMessageHistory(
                        platform_id=incoming.get_platform_id(),
                        user_id=incoming.unified_msg_origin,
                        sender_id="alice",
                        sender_name="Alice",
                        content={
                            "type": "user",
                            "message": [{"type": "plain", "text": f"真实宿主记录-{i}"}],
                        },
                        created_at=datetime.fromtimestamp(base + i, timezone.utc),
                    )
                    for i in range(100)
                ]
            )
            await session.commit()
        plugin.context.message_history_manager = PlatformMessageHistoryManager(database)
        state = plugin.room_memory.rooms[scope]
        state.read_sdk_window = MethodType(read_sdk_window, state)
        result = await plugin.room_memory.call(
            scope, "read_sdk_window", scope, base + 1, base + 3.1, event=incoming
        )
        assert result == ["真实宿主记录-2", "真实宿主记录-3"]
    finally:
        await database.engine.dispose()


async def drive_feedback(state, task):
    await state.kernel._feedback_service._process_feedback_task(task)
    return state.metadata.get_feedback_task_by_id(task["id"])


async def read_sdk_messages(state, scope):
    from astrbot_plugin_ovenheart.vendor.maibot.amemorix.sdk_host import (
        chat_manager,
        message_service,
    )

    rows = message_service.get_messages_by_time_in_chat(
        chat_id=scope,
        start_time=0,
        end_time=time.time() + 1,
        limit=120,
        limit_mode="latest",
        filter_mai=True,
        filter_command=True,
    )
    session = chat_manager.get_existing_session_by_session_id(scope)
    return {
        "texts": [r.processed_plain_text for r in rows],
        "session_id": session.session_id,
        "group_id": session.group_id,
    }


@pytest.mark.parametrize("isolation", [True, False])
async def test_real_query_queues_once_and_original_feedback_sees_later_native_input(
    memories, isolation,
):
    plugin = memories
    plugin.settings["memory_isolation"] = isolation
    incoming, scope, persona, messages = await material(plugin)
    old = (
        await plugin.room_memory.remember(incoming, scope, persona, note(), messages)
    )["id"]
    assert await plugin.room_memory.search(incoming, scope, persona, "小猫")
    assert await plugin.room_memory.search(incoming, scope, persona, "小猫")
    storage = await plugin.room_memory.storage_scope(scope)
    state = plugin.room_memory.rooms[storage]
    tasks = state.metadata.list_feedback_tasks(limit=10)
    assert len(tasks) == 1
    calls = []

    async def classify(**options):
        calls.append(options["prompt"])
        return SimpleNamespace(
            completion_text=json.dumps(
                {
                    "decision": "reject",
                    "confidence": 1.0,
                    "target_hashes": [old],
                    "corrected_relations": [],
                    "reason": "用户明确否认此安排",
                }
            )
        )

    plugin.context.llm_generate.side_effect = classify
    feedback = event("later-feedback", text="你记错了，不是去看小猫，我并没有那个安排")
    await plugin.capture(feedback)
    other, other_scope, other_persona, _ = await material(plugin, "unrelated", "隔壁群说我觉得那只小猫不错", session="456")
    await plugin.room_memory.search(other, other_scope, other_persona, "小猫")
    # Use the real persisted query and original job, with a short controlled
    # due window instead of waiting the production default twelve hours.
    original = tasks[0]
    task = state.metadata.enqueue_feedback_task(
        query_tool_id=original["query_tool_id"],
        session_id=storage,
        query_timestamp=time.time() - 1,
        due_at=time.time() + 1,
        query_snapshot=original["query_snapshot"],
    )
    state.drive_feedback = MethodType(drive_feedback, state)
    finished = await plugin.room_memory.call(
        scope, "drive_feedback", task, event=other if not isolation else feedback
    )
    assert finished["status"] not in {"failed", "skipped"}, finished
    assert len(calls) == 1 and "你记错了" in calls[0]
    assert "隔壁群说" not in calls[0]
    assert all(
        r["id"] != old
        for r in await plugin.room_memory.search(feedback, scope, persona, "小猫")
    )
    await plugin.auxiliary.flush()
    assert any(
        row["stage"] == "memory_feedback"
        for row in await plugin.store.call("auxiliary_usage")
    )


async def test_live_sdk_reader_uses_current_scope_and_learning_permissions(memories):
    plugin = memories
    plugin.settings["context_mode"] = "structured"
    incoming, scope, persona, messages = await material(plugin)
    await plugin.room_memory.remember(incoming, scope, persona, note(), messages)
    state = plugin.room_memory.rooms[scope]
    state.read_sdk_messages = MethodType(read_sdk_messages, state)
    later = event("current-text", text="这是后来的真实输入")
    command = event("native-command", text="/sid")
    await plugin.capture(later)
    await plugin.capture(command)
    result = await plugin.room_memory.call(
        scope, "read_sdk_messages", scope, event=later
    )
    assert "这是后来的真实输入" in result["texts"]
    assert "/sid" not in result["texts"]
    assert result["session_id"] == scope and result["group_id"] == later.get_group_id()
    assert (
        state.identity.get_message_str() == "" and state.identity.get_messages() == []
    )
    with pytest.raises(ValueError, match="其他会话"):
        await plugin.room_memory.call(
            scope, "read_sdk_messages", "other-scope", event=later
        )
    plugin.settings["learning_enabled"] = False
    with pytest.raises(ValueError, match="权限"):
        await plugin.room_memory.call(scope, "read_sdk_messages", scope, event=later)
