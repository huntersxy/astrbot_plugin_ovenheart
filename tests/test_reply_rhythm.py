"""Original semantic boundaries feed native sends and actual planner receipts."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.core.message.components import At, Plain, Reply
from astrbot.core.message.message_event_result import ResultContentType

from tests.test_conversation_planner import action
from tests.test_runtime import event

pytest_plugins = ["tests.test_conversation_planner"]

FIRST = "先确认这个结果已经保存好了，相关文件也都在原来的目录里。"
SECOND = "接下来可以继续处理后面的步骤，遇到新问题再告诉我。"


@pytest.mark.parametrize("mode", ["complete", "partial", "rewritten"])
async def test_original_splitter_preserves_text_headers_and_actual_sent_segments(
    native_planner, mode
):
    p, provider = native_planner
    p.settings.update(
        reply_rhythm_enabled=True,
        reply_split_mode="llm",
        reply_typing_speed=0,
        reply_split_provider_id="fast-splitter",
        participation_frequency=0,
    )
    split = [FIRST, SECOND] if mode != "rewritten" else ["已被模型偷偷改写的正文"]
    p.context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(
            completion_text=json.dumps(split, ensure_ascii=False)
        )
    )
    provider.text_chat.side_effect = [
        action("reply", msg_id="1", reply_reference="回应当前明确任务。"),
        action("stop"),
    ]
    incoming = event("split-request", text="说明一下这个结果和后面的安排")
    incoming.is_at_or_wake_command = True
    transport = AsyncMock(
        side_effect=[True, False] if mode == "partial" else None, return_value=True
    )
    incoming.send = transport
    replies = p.participate(incoming)
    request = await anext(replies)
    await p.apply_persona(incoming, request)
    await p.apply_participation(incoming, request)
    text = FIRST + SECOND
    incoming.set_result(incoming.plain_result(text))
    incoming.get_result().chain = [
        Reply(id="source-platform-id"),
        At(qq="alice"),
        Plain(text),
    ]
    await incoming.send(incoming.get_result())
    await p.record_delivery(incoming)
    receipt = incoming.get_extra("ovenheart_planner_receipt")
    if mode == "rewritten":
        assert transport.await_count == 2
        assert (
            "".join(call.args[0].get_plain_text() for call in transport.await_args_list)
            == text
        )
    else:
        assert transport.await_count == 2
        assert transport.await_args_list[0].args[0].get_plain_text() == FIRST
        assert transport.await_args_list[1].args[0].get_plain_text() == SECOND
        assert any(
            isinstance(part, Reply)
            for part in transport.await_args_list[0].args[0].chain
        )
        assert not any(
            isinstance(part, (At, Reply))
            for part in transport.await_args_list[1].args[0].chain
        )
    if mode == "partial":
        assert receipt["partial_text"] and receipt["reply_text"] == FIRST
        assert SECOND not in receipt["reply_text"]
    else:
        assert not receipt["partial_text"]
    with pytest.raises(StopAsyncIteration):
        await anext(replies)
    assert p.context.llm_generate.await_count == 1
    assert (
        p.context.llm_generate.await_args.kwargs["chat_provider_id"] == "fast-splitter"
    )
    await p.auxiliary.flush()
    assert (
        next(
            row
            for row in await p.store.call("auxiliary_usage")
            if row["stage"] == "reply_split"
        )["calls"]
        == 1
    )


async def test_short_native_reply_splits_without_a_planner_or_model(participant):
    p = participant
    p.settings["reply_split_mode"] = "rule"
    incoming = event("native-short", text="接下来怎么做")
    await p.capture(incoming)
    text = "先保存好。再继续。"
    incoming.set_result(incoming.plain_result(text))
    incoming.get_result().result_content_type = ResultContentType.LLM_RESULT
    sent = AsyncMock(return_value=True)
    incoming.send = sent
    p.reply_rhythm.pause = AsyncMock()
    p.reply_rhythm.bind(incoming)
    # RespondStage derives a MessageChain; it does not send the result object.
    await incoming.send(incoming.get_result().derive(incoming.get_result().chain))
    assert [call.args[0].get_plain_text() for call in sent.await_args_list] == [
        "先保存好。",
        "再继续。",
    ]
    p.reply_rhythm.pause.assert_awaited_once_with(incoming, "再继续。", False)
    assert incoming.get_extra("ovenheart_actual_text_segments") == [
        "先保存好。",
        "再继续。",
    ]
    p.context.llm_generate.assert_not_awaited()


async def test_rule_splitting_merges_without_losing_punctuation(participant):
    participant.settings.update(reply_split_mode="rule", reply_split_max_messages=2)
    text = "第一步。 第二步！\n第三步？第四步。"
    result = await participant.reply_rhythm.split(event(), text, {})
    assert len(result) == 2
    assert "".join(result) == text


@pytest.mark.parametrize(
    "text",
    [
        "```python\nprint('一。二。')\n```",
        "他说“先保存。再继续。”",
        "1. 保存。\n2. 继续。",
    ],
)
async def test_structured_reply_stays_intact_without_a_model(participant, text):
    participant.settings["reply_split_mode"] = "llm"
    assert await participant.reply_rhythm.split(event(), text, {}) == [text]
    participant.context.llm_generate.assert_not_awaited()


async def test_semantic_timeout_cancels_model_and_uses_rule_result(participant):
    participant.settings.update(reply_split_mode="llm", reply_split_timeout=0.01)
    cancelled = asyncio.Event()

    async def slow(*args, **kwargs):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    participant.auxiliary.generate = AsyncMock(side_effect=slow)
    result = await participant.reply_rhythm.split(event(), "先保存。再继续。", {})
    assert cancelled.is_set()
    assert result == ["先保存。", "再继续。"]


@pytest.mark.parametrize("host_segmented", [True, False])
async def test_host_segmentation_and_command_results_are_not_split(
    participant, host_segmented
):
    incoming = event("host-owner")
    incoming.set_result(incoming.plain_result("第一句。第二句。"))
    if host_segmented:
        incoming.get_result().result_content_type = ResultContentType.LLM_RESULT
        participant.context.get_config = lambda umo: {
            "platform_settings": {"segmented_reply": {"enable": True}}
        }
    sent = AsyncMock(return_value=True)
    incoming.send = sent
    participant.reply_rhythm.pause = AsyncMock()
    participant.reply_rhythm.bind(incoming)
    await incoming.send(incoming.get_result().derive(incoming.get_result().chain))
    assert sent.await_count == 1
    assert sent.await_args.args[0].get_plain_text() == "第一句。第二句。"
    participant.reply_rhythm.pause.assert_not_awaited()
    participant.context.llm_generate.assert_not_awaited()
