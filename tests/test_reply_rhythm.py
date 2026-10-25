"""Original semantic boundaries feed native sends and actual planner receipts."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.core.message.components import At, Plain, Reply

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
        assert transport.await_count == 1
        assert transport.await_args.args[0].get_plain_text() == text
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
