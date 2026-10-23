"""Carry bounded same-member additions beside an older actual reply target."""

import json
import time
from unittest.mock import AsyncMock

from tests.test_participation import decision, requests
from tests.test_runtime import event

pytest_plugins = ["tests.test_participation"]


async def test_older_target_keeps_nearby_additions_in_selector_and_temporary_reply(
    participant,
):
    participant.settings.update(
        participation_min_messages=30, guidance_delivery="cached"
    )
    base = time.time() - 100
    incoming = []
    for i in range(1, 20):
        message = event(
            f"source-{i}",
            text=f"SUPPLEMENT_{i}：" + "补充说明。" * 100 + f"TAIL_CONSTRAINT_{i}"
            if i <= 9 and i != 5
            else "TARGET：我想再说清楚一点。"
            if i == 5
            else f"其他人的话题 {i}",
            sender="alice" if i <= 9 else "peer",
        )
        message.created_at = base + i
        incoming.append(message)
        assert await requests(participant, message) == []
    participant.context.llm_generate.assert_not_awaited()
    participant.settings["participation_min_messages"] = 1
    participant.context.llm_generate.return_value = decision(target_sequence=5)
    trigger = event("source-20", text="最新的无关话题", sender="whale")
    trigger.created_at = base + 20
    request = (await requests(participant, trigger))[0]
    plan = trigger.get_extra("ovenheart_decision")
    assert [m["sequence"] for m in plan["reply_context"]] == [3, 4, 6, 7]
    assert all(m["sender_id"] == "alice" for m in plan["reply_context"])
    assert all(len(m["text"]) == 300 for m in plan["reply_context"])
    assert all(m["sequence"] >= 9 for m in plan["messages"])
    assert request.prompt == incoming[4].get_message_str()
    assert trigger.message_obj.message_id == "source-20"
    participant.retriever.select = AsyncMock(wraps=participant.retriever.select)
    await participant.apply_persona(trigger, request)
    recall = participant.retriever.select.await_args
    assert len(recall.args[2]) <= 2400
    assert "SUPPLEMENT_4" in recall.args[2] and "TAIL_CONSTRAINT_4" in recall.args[2]
    assert "SUPPLEMENT" not in recall.kwargs["literal_query"]
    await participant.apply_participation(trigger, request)
    temporary = request.extra_user_content_parts[-1]
    payload = json.JSONDecoder().raw_decode(
        temporary.text[temporary.text.index('{"reply_goal"') :]
    )[0]
    assert payload["reply_context"] == plan["reply_context"]
    assert payload["reply_message"]["sequence"] == 5
    assert temporary.model_dump_for_context()["_no_save"]
    participant.context.llm_generate.assert_awaited_once()


async def test_neighbor_context_stops_at_time_gaps_and_another_member(participant):
    participant.settings["participation_min_messages"] = 30
    base = time.time() - 100
    for i, (sender, offset) in enumerate(
        (("alice", -31), ("alice", 0), ("peer", 1), ("alice", 2)), 1
    ):
        message = event(f"bounded-{i}", text=f"发言 {i}", sender=sender)
        message.created_at = base + offset
        assert await requests(participant, message) == []
    participant.settings["participation_min_messages"] = 1
    participant.context.llm_generate.return_value = decision(target_sequence=2)
    trigger = event("bounded-5", text="触发检查", sender="peer")
    trigger.created_at = base + 3
    assert len(await requests(participant, trigger)) == 1
    assert trigger.get_extra("ovenheart_decision")["reply_context"] == []
    participant.context.llm_generate.assert_awaited_once()
