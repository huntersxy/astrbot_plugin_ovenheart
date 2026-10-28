"""Run review, effective-version filtering and rollback through the native SDK."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from tests.test_room_memory import material, note
from tests.test_runtime import api_call

pytest_plugins = ["tests.test_room_memory"]


@pytest.mark.parametrize("confidence", [0.99, 0.5])
async def test_review_uses_new_evidence_preserves_journal_and_can_rollback(
    memories, confidence
):
    old, scope, persona, rows = await material(memories, "old-home", "Neko住在东街。")
    first = await memories.room_memory.remember(
        old,
        scope,
        persona,
        note(quote="Neko住在东街", content="Neko住在东街。", relations=[]),
        rows,
    )
    new, _, _, rows = await material(
        memories, "new-home", "Neko已经搬去西街了，不再住东街。"
    )
    await memories.room_memory.remember(
        new,
        scope,
        persona,
        note(
            quote="Neko已经搬去西街了，不再住东街",
            content="Neko已经搬去西街了，不再住东街。",
            relations=[],
        ),
        rows,
    )
    memories.context.llm_generate.side_effect = None
    memories.context.llm_generate.return_value = SimpleNamespace(
        completion_text=json.dumps(
            {
                "confidence": confidence,
                "reason": "新原文明确说明搬家，保留旧址的历史含义。",
                "operations": [
                    {"action": "mark_superseded", "hash": first["id"]},
                    {
                        "action": "ingest_text",
                        "text": "Neko现在的住址是西街，东街是搬家前的旧地址。",
                        "relations": [],
                    },
                ],
            },
            ensure_ascii=False,
        )
    )
    result = await memories.room_memory.call(scope, "review", lambda: True, event=new)
    assert result["reviewed"] == 1 and result["plan_id"]
    if confidence < 0.9:
        assert result["status"] == "needs_confirmation"
        found = await memories.room_memory.search(new, scope, persona, "Neko")
        assert any(row["id"] == first["id"] for row in found)
        approved = await api_call(
            memories.page_api.memory_panel.action,
            {
                "scope": scope,
                "action": "review_execute",
                "payload": {"id": result["plan_id"]},
            },
        )
        assert approved["status"] == "ok", approved
    else:
        assert result["status"] == "corrected" and result["changed"] == 1
    found = await memories.room_memory.search(new, scope, persona, "Neko")
    assert all(row["id"] != first["id"] for row in found)
    replacement = next(row for row in found if "旧地址" in row["content"])
    assert {p["source_id"] for p in replacement["metadata"]["evidence"]} == {
        "old-home",
        "new-home",
    }
    restored = await memories.room_memory.call(
        scope, "admin", "rollback", {"id": result["plan_id"]}
    )
    assert restored["success"], restored
    found = await memories.room_memory.search(new, scope, persona, "Neko")
    assert any(row["id"] == first["id"] for row in found)


async def test_background_review_obeys_global_interval_and_disabled_switch(memories):
    first, scope, persona, rows = await material(memories, "review-a", "Neko喜欢猫咪。")
    await memories.room_memory.remember(
        first, scope, persona, note(content="Neko喜欢猫咪。"), rows
    )
    newest, _, _, rows = await material(memories, "review-b", "Neko周末在书店读书。")
    await memories.room_memory.remember(
        newest,
        scope,
        persona,
        note(quote="Neko周末在书店读书", content="Neko周末在书店读书。", relations=[]),
        rows,
    )
    memories.context.llm_generate.side_effect = None
    memories.context.llm_generate.return_value = SimpleNamespace(
        completion_text=json.dumps(
            {
                "confidence": 1,
                "reason": "两条记录无矛盾，不需改动。",
                "operations": [],
            }
        )
    )
    await memories.memory_review.poll()
    memories.context.llm_generate.assert_not_awaited()
    memories.settings["memory_review_enabled"] = True
    before = set(memories.tasks)
    await memories.memory_review.poll()
    await asyncio.wait_for(asyncio.gather(*(memories.tasks - before)), 10)
    progress = await memories.store.read("memory_review_status")
    assert progress["latest"] and progress["rooms"][scope]["status"] == "kept"
    assert memories.context.llm_generate.await_count == 1
    await memories.memory_review.poll()
    assert memories.context.llm_generate.await_count == 1
