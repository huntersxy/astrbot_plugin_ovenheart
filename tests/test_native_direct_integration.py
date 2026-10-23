"""Preserve native direct-agent capabilities while supplying plugin context."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.message.components import At, Image, Plain, Reply
from astrbot.core.message.message_event_result import MessageChain, ResultContentType
from astrbot.core.pipeline.process_stage.stage import ProcessStage
from astrbot.core.pipeline.respond.stage import RespondStage
from astrbot.core.pipeline.waking_check import stage as waking

from astrbot_plugin_ovenheart.main import ParticipationFilter
from tests.test_participation import requests, result_decorate_stage
from tests.test_runtime import event

pytest_plugins = ["tests.test_participation"]


@pytest.mark.parametrize("route", ["at", "quote"])
async def test_native_wake_runs_one_unchanged_agent_with_plugin_context(
    participant, monkeypatch, route
):
    participant.settings.update(
        participation_mode="off", learning_enabled=False, guidance_delivery="cached"
    )
    incoming = event("native-direct", text="看看这张图", sender="current")
    image = Image.fromURL("https://example.invalid/input.png")
    quoted = Reply(
        id="bot-before",
        sender_id="bot",
        sender_nickname="机器人",
        chain=[Plain("这是我刚才解释的那个结果"), image],
    )
    incoming.message_obj.message = (
        [At(qq="bot"), Plain(incoming.message_str), image]
        if route == "at"
        else [quoted, Plain(incoming.message_str), image]
    )
    components = list(incoming.get_messages())
    config = {"admins_id": [], "wake_prefix": [], "plugin_set": ["*"]}
    wake = waking.WakingCheckStage()
    wake.ctx = SimpleNamespace(astrbot_config=config)
    wake.ignore_bot_self_message = wake.disable_builtin_commands = False
    wake.ignore_at_all = True
    wake.friend_message_needs_wake_prefix = False
    wake._umo_auto_name_recorder = SimpleNamespace(schedule=Mock())
    monkeypatch.setattr(
        waking.star_handlers_registry, "get_handlers_by_event_type", lambda *a, **k: []
    )
    await wake.process(incoming)
    assert incoming.is_at_or_wake_command and not incoming.is_stopped()
    assert not ParticipationFilter().filter(incoming, config)
    tools = ToolSet(
        [FunctionTool(name="native_tool", description="Native tool", parameters={})]
    )
    request = ProviderRequest(
        prompt="宿主处理好的图文请求",
        system_prompt="稳定宿主人格",
        contexts=[{"role": "assistant", "content": "原生历史"}],
        image_urls=["https://example.invalid/input.png"],
        func_tool=tools,
    )
    original = deepcopy(
        {
            key: getattr(request, key)
            for key in (
                "prompt",
                "system_prompt",
                "contexts",
                "image_urls",
                "conversation",
            )
        }
    )
    invocations = []

    class AgentStage:
        async def process(self, current):
            invocations.append(current)
            await participant.apply_persona(current, request)
            await participant.apply_participation(current, request)
            yield None

    process = ProcessStage()
    process.ctx = SimpleNamespace(
        astrbot_config={"provider_settings": {"enable": True}}
    )
    process.agent_sub_stage = AgentStage()
    async for _ in process.process(incoming):
        pass
    assert invocations == [incoming] and not incoming.call_llm
    assert request.func_tool is tools and request.func_tool.get_tool("native_tool")
    assert incoming.get_messages() == components
    assert incoming.get_extra("enable_streaming") is None
    for key in ("prompt", "system_prompt", "contexts", "image_urls", "conversation"):
        assert getattr(request, key) == original[key]
    direct = incoming.get_extra("ovenheart_direct_context")
    assert direct["route"] == route
    assert direct["reply_message"]["sender_id"] == "current"
    assert direct["reply_message"]["message_id"] == "native-direct"
    if route == "quote":
        assert direct["reply_message"]["reply_to"]["text"] == "这是我刚才解释的那个结果"
    assert request.extra_user_content_parts[-1].model_dump_for_context()["_no_save"]
    assert "reply_message" in request.extra_user_content_parts[-1].text
    assert not incoming.get_extra("ovenheart_decision")
    participant.context.llm_generate.assert_not_awaited()


async def test_direct_stream_corrects_split_prefix_without_buffering_the_answer(
    participant,
):
    incoming = event("stream", sender="current", sender_name="当前成员")
    incoming.is_at_or_wake_command = True
    incoming.set_extra("enable_streaming", True)
    reasoning = MessageChain(type="reasoning").message("内部推理")
    tool = MessageChain(type="tool_call").message("工具状态")
    boundary = MessageChain(type="break")
    media = MessageChain([Image.fromURL("https://example.invalid/result.png")])
    first = MessageChain().message("@旧")
    first.use_markdown_ = True
    chunks = [
        reasoning,
        first,
        tool,
        MessageChain().message("成员 正文第一句。"),
        MessageChain().message("正文第二句。"),
        boundary,
        media,
    ]
    consumed, closed = [], []

    async def source():
        try:
            assert incoming.send_streaming is native_send
            for chunk in chunks:
                consumed.append(chunk)
                yield chunk
        finally:
            closed.append(True)

    async def native_send(generator, use_fallback=False):
        assert use_fallback is True
        assert await anext(generator) is reasoning
        assert await anext(generator) is tool
        corrected = await anext(generator)
        assert len(consumed) == 4  # The rest has not been generated.
        assert corrected.use_markdown_ is True
        assert [str(p.qq) for p in corrected.chain if isinstance(p, At)] == ["current"]
        assert corrected.get_plain_text().strip() == "正文第一句。"
        assert [chunk async for chunk in generator] == chunks[4:]

    incoming.send_streaming = native_send
    request = ProviderRequest(prompt="问题")
    await participant.apply_participation(incoming, request)
    wrapped_adapter = incoming.send_streaming
    await participant.apply_participation(incoming, request)
    assert incoming.send_streaming is wrapped_adapter
    assert len(request.extra_user_content_parts) == 1
    result = incoming.plain_result("")
    result.result_content_type = ResultContentType.STREAMING_RESULT
    result.async_stream = source()
    incoming.set_result(result)
    decorate = result_decorate_stage()
    async for _ in decorate.process(incoming):
        pass
    assert not consumed
    respond = RespondStage()
    respond.config = {}
    await respond.process(incoming)
    assert closed == [True] and incoming.send_streaming is native_send
    assert incoming.get_extra("enable_streaming") is True


async def test_native_stream_close_releases_the_underlying_iterator(participant):
    incoming = event("cancel", sender="current")
    closed = []

    async def source():
        try:
            yield MessageChain().message("正常开头")
            yield MessageChain().message("尚未读取")
        finally:
            closed.append(True)

    async def native_send(generator, use_fallback=False):
        assert (await anext(generator)).get_plain_text() == "正常开头"
        raise asyncio.CancelledError

    incoming.send_streaming = native_send
    await participant.apply_participation(incoming, ProviderRequest(prompt="问题"))
    result = incoming.plain_result("")
    result.result_content_type = ResultContentType.STREAMING_RESULT
    result.async_stream = source()
    incoming.set_result(result)
    respond = RespondStage()
    respond.config = {}
    with pytest.raises(asyncio.CancelledError):
        await respond.process(incoming)
    assert closed == [True] and incoming.send_streaming is native_send


async def test_direct_quote_only_and_disabled_plugin_leave_native_delivery_available(
    participant,
):
    incoming = event("quote-only", text="")
    incoming.message_obj.message = [
        Reply(id="old-bot", sender_id="bot", message_str="引用的机器人原文")
    ]
    incoming.is_at_or_wake_command = True
    assert await requests(participant, incoming) == []
    request = ProviderRequest(prompt="宿主引用处理结果", system_prompt="原生系统")
    await participant.apply_persona(incoming, request)
    await participant.apply_participation(incoming, request)
    assert (
        incoming.get_extra("ovenheart_direct_context")["reply_message"]["reply_to"][
            "text"
        ]
        == "引用的机器人原文"
    )
    assert not incoming.is_stopped() and not incoming.call_llm
    participant.settings["enabled"] = False
    untouched = ProviderRequest(prompt="原请求", system_prompt="原生系统")
    await participant.apply_persona(incoming, untouched)
    await participant.apply_participation(incoming, untouched)
    assert not untouched.extra_user_content_parts and untouched.prompt == "原请求"
    assert not incoming.is_stopped() and incoming.is_at_or_wake_command
    participant.context.llm_generate.assert_not_awaited()
