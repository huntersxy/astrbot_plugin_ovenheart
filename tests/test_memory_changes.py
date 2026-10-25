"""Exercise native change tools against the actual SDK journal and stores."""

import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest

from tests.test_conversation_planner import action, send_reply
from tests.test_room_memory import SyntheticEmbedding, material, note
from tests.test_runtime import event

pytest_plugins = ["tests.test_room_memory", "tests.test_conversation_planner"]


async def changed_source(plugin, source, text, **kwargs):
    incoming, scope, persona, messages = await material(plugin, source, text, **kwargs)
    return incoming, scope, persona, messages, [{"msg_id": "1", "quote": text}]


async def test_correct_forget_and_author_undo_use_original_runtime_and_keep_proofs(
    memories,
):
    plugin = memories
    incoming, scope, persona, messages = await material(plugin)
    old = (
        await plugin.room_memory.remember(incoming, scope, persona, note(), messages)
    )["id"]
    incoming, scope, persona, messages, evidence = await changed_source(
        plugin, "correct", "把看小猫的安排改为周日"
    )
    corrected = await plugin.room_memory.change(
        incoming,
        scope,
        persona,
        "correct_memory",
        {
            "memory_id": old,
            "content": "Neko周日去看小猫。",
            "relations": [],
            "evidence": evidence,
        },
        messages,
    )
    assert corrected["success"], corrected
    new_id = corrected["new_ids"][0]
    found = await plugin.room_memory.search(incoming, scope, persona, "小猫")
    assert new_id in {r["id"] for r in found} and old not in {r["id"] for r in found}
    row = plugin.room_memory.rooms[scope].metadata.get_paragraph(new_id)
    assert row["metadata"]["evidence"][0]["source_id"] == "correct"
    assert row["metadata"]["evidence"][0]["quote"] == evidence[0]["quote"]
    assert row["metadata"]["participants"] == ["alice"]
    incoming, scope, persona, messages, evidence = await changed_source(
        plugin, "undo-correction", "撤销刚才的更正"
    )
    undone = await plugin.room_memory.change(
        incoming, scope, persona, "undo_memory_change", {"evidence": evidence}, messages
    )
    assert undone["success"] and undone["restored_ids"] == [old], undone
    found = await plugin.room_memory.search(incoming, scope, persona, "小猫")
    assert old in {r["id"] for r in found} and new_id not in {r["id"] for r in found}
    incoming, scope, persona, messages, evidence = await changed_source(
        plugin, "forget", "忘掉那条看小猫的安排"
    )
    forgotten = await plugin.room_memory.change(
        incoming,
        scope,
        persona,
        "forget_memory",
        {"memory_id": old, "evidence": evidence},
        messages,
    )
    assert forgotten["success"], forgotten
    assert not await plugin.room_memory.search(incoming, scope, persona, "小猫")
    # Closing and reopening must keep the native journal usable without the
    # planner retaining its previous tool results or process-local state.
    from astrbot_plugin_ovenheart.room_memory import RoomMemory

    await plugin.room_memory.close()
    plugin.room_memory = RoomMemory(plugin)
    incoming, scope, persona, messages, evidence = await changed_source(
        plugin, "undo-forget", "恢复刚才忘掉的安排"
    )
    restored = await plugin.room_memory.change(
        incoming, scope, persona, "undo_memory_change", {"evidence": evidence}, messages
    )
    assert (
        restored["metadata_restored"]
        and restored["change_id"] == forgotten["change_id"]
    )
    assert not restored["success"] and restored["pending_derived_indexes"]
    assert any(
        r["id"] == old
        for r in await plugin.room_memory.search(incoming, scope, persona, "小猫")
    )
    # The same original cleanup jobs must complete once a real native embedding
    # channel becomes available; no fake "restored" state is written by us.
    plugin.settings["embedding_provider_id"] = "semantic"
    plugin.context.get_provider_by_id = lambda name: SyntheticEmbedding()
    jobs = plugin.room_memory.rooms[scope].metadata.list_storage_cleanup_jobs(
        operation_id=forgotten["change_id"], statuses=["failed"]
    )
    assert jobs
    delay = max(j["next_attempt_at"] for j in jobs) - time.time()
    assert delay < 5
    await asyncio.sleep(max(0, delay) + 0.05)
    finished = await plugin.room_memory.change(
        incoming,
        scope,
        persona,
        "undo_memory_change",
        {"change_id": forgotten["change_id"], "evidence": evidence},
        messages,
    )
    assert finished["success"] and not finished["pending_derived_indexes"], finished
    assert (
        plugin.room_memory.rooms[scope].metadata.get_delete_operation(
            forgotten["change_id"]
        )["status"]
        == "restored"
    )


async def test_change_rejects_foreign_scope_forged_quote_and_other_author_undo(
    memories,
):
    plugin = memories
    incoming, scope, persona, messages = await material(plugin)
    old = (
        await plugin.room_memory.remember(incoming, scope, persona, note(), messages)
    )["id"]
    (
        foreign,
        other_scope,
        other_persona,
        other_messages,
        evidence,
    ) = await changed_source(plugin, "other-room", "忘掉那条安排", session="other")
    denied = await plugin.room_memory.change(
        foreign,
        other_scope,
        other_persona,
        "forget_memory",
        {"memory_id": old, "evidence": evidence},
        other_messages,
    )
    assert not denied["success"]
    with pytest.raises(ValueError, match="原文"):
        await plugin.room_memory.change(
            incoming,
            scope,
            persona,
            "forget_memory",
            {
                "memory_id": old,
                "evidence": [{"msg_id": "1", "quote": "不存在的遗忘请求"}],
            },
            messages,
        )
    incoming, scope, persona, messages, evidence = await changed_source(
        plugin, "authorized", "忘掉那条安排"
    )
    forgotten = await plugin.room_memory.change(
        incoming,
        scope,
        persona,
        "forget_memory",
        {"memory_id": old, "evidence": evidence},
        messages,
    )
    bob, scope, persona, bob_messages, evidence = await changed_source(
        plugin, "other-author", "撤销那次遗忘", sender="bob"
    )
    denied = await plugin.room_memory.change(
        bob,
        scope,
        persona,
        "undo_memory_change",
        {"change_id": forgotten["change_id"], "evidence": evidence},
        bob_messages,
    )
    assert not denied["success"]
    assert not await plugin.room_memory.search(bob, scope, persona, "小猫")


async def test_actual_planner_queries_corrects_and_replies_from_updated_memory(
    native_planner,
):
    plugin, provider = native_planner
    plugin.settings.update(native_memory_enabled=True, memory_summary_enabled=False)
    plugin.context.get_provider_by_id = lambda name: None
    plugin.memory.recall = AsyncMock(return_value=([], "没有旧插件回忆"))
    incoming, scope, persona, messages = await material(plugin)
    old = (
        await plugin.room_memory.remember(incoming, scope, persona, note(), messages)
    )["id"]
    provider.text_chat.side_effect = [
        action("search_memory", query="小猫"),
        action(
            "correct_memory",
            memory_id=old,
            content="Neko周日去看小猫。",
            relations=[],
            evidence=[{"msg_id": "1", "quote": "把看小猫的安排改为周日"}],
        ),
        action("search_memory", query="小猫"),
        action("reply", msg_id="1", reply_reference="根据实际修改结果确认已改为周日。"),
        action("stop"),
    ]
    incoming = event("planner-correct", text="把看小猫的安排改为周日")
    incoming.is_at_or_wake_command = True
    replies = plugin.participate(incoming)
    request = await anext(replies)
    assert provider.text_chat.await_count == 4
    serialized = json.dumps(
        provider.text_chat.await_args.kwargs["contexts"],
        ensure_ascii=False,
        default=lambda value: value.model_dump(),
    )
    tool_results = [
        json.loads(m["content"]) for m in json.loads(serialized) if m["role"] == "tool"
    ]
    assert any(m.get("success") for m in tool_results)
    assert tool_results[-1]["memories"][0]["content"] == "Neko周日去看小猫。"
    await plugin.apply_persona(incoming, request)
    await plugin.apply_participation(incoming, request)
    assert "Neko周日去看小猫。" in "\n".join(
        p.text for p in request.extra_user_content_parts
    )
    await send_reply(plugin, incoming, "记好了，改成周日。")
    with pytest.raises(StopAsyncIteration):
        await anext(replies)
    assert provider.text_chat.await_count == 5
    assert not plugin.participation.planner.sessions
