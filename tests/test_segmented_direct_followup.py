"""Keep one direct answer's segments together without consuming newer input."""

import json

from tests.test_participation import requests
from tests.test_runtime import event

pytest_plugins = ["tests.test_runtime", "tests.test_participation"]


async def test_direct_answer_segments_keep_the_single_followup_opportunity(participant):
    participant.settings["participation_min_messages"] = 3
    direct = event("segmented-direct", text="可以说说具体做法吗？")
    direct.is_at_or_wake_command = True
    await participant.capture(direct)
    scope, _, _ = await participant.resolve_scope(direct)
    for text in ("先把这一步完成。", "然后确认结果。", "拿不准就告诉我。"):
        direct.set_result(direct.plain_result(text))
        await participant.record_delivery(direct)
    window = participant.participation.windows[scope]
    assert window["consumed"] == window["sequence"]
    assert len(window["messages"]) == 3
    assert (
        len(
            await requests(
                participant, event("after-segments", text="第一步做好了，接下来呢？")
            )
        )
        == 1
    )
    payload = json.loads(participant.context.llm_generate.await_args.kwargs["prompt"])
    assert payload["is_followup"] is True
    assert [m["text"] for m in payload["recent_messages"] if m["self"]] == [
        "先把这一步完成。",
        "然后确认结果。",
        "拿不准就告诉我。",
    ]
    assert participant.context.llm_generate.await_count == 1


async def test_intervening_input_stays_pending_through_all_late_direct_segments(
    participant,
):
    participant.settings["participation_min_messages"] = 3
    direct = event("old-direct", text="先讲讲第一步吧")
    direct.is_at_or_wake_command = True
    await participant.capture(direct)
    direct.set_result(direct.plain_result("这是第一段。"))
    await participant.record_delivery(direct)
    scope, _, _ = await participant.resolve_scope(direct)
    consumed = participant.participation.windows[scope]["consumed"]
    assert (
        await requests(
            participant, event("new-between", text="我还有另一个问题", sender="bob")
        )
        == []
    )
    for text in ("这是旧回答的第二段。", "这是旧回答的第三段。"):
        direct.set_result(direct.plain_result(text))
        await participant.record_delivery(direct)
    window = participant.participation.windows[scope]
    assert window["consumed"] == consumed
    assert [
        m["message_id"]
        for m in window["messages"]
        if not m["self"] and m["sequence"] > consumed
    ] == ["new-between"]
    assert await requests(participant, event("next-alice", text="那接下来呢？")) == []
    participant.context.llm_generate.assert_not_awaited()
