"""Coordinate native direct turns without losing input, media or permissions."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.message.components import At, Image, Plain, Reply

from astrbot_plugin_ovenheart.main import ObservationFilter
from tests.test_participation import requests
from tests.test_runtime import event

pytest_plugins = ["tests.test_participation"]


async def test_pending_name_request_absorbs_only_same_member_empty_at(participant):
    participant.settings.update(
        mentioned_bot_reply=True, mention_names=["插座"], learning_enabled=False
    )
    first = event("named", text="插座，帮我看一下这段内容", sender="alice")
    first.message_obj.message = [
        Plain(first.message_str),
        Image.fromURL("https://example.invalid/source.png"),
    ]
    first.set_extra("ovenheart_mention", "插座")
    tools = ToolSet(
        [FunctionTool(name="original_tool", description="Native", parameters={})]
    )
    req = ProviderRequest(
        prompt=first.message_str,
        image_urls=["https://example.invalid/source.png"],
        func_tool=tools,
    )
    before = deepcopy((req.prompt, req.image_urls))
    entered, release = asyncio.Event(), asyncio.Event()

    async def original_turn():
        assert await requests(participant, first) == []
        assert first.is_at_or_wake_command
        await participant.coordinate_direct_request(first, req)
        entered.set()
        await release.wait()

    task = asyncio.create_task(original_turn())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        incoming = event("empty-at", text="", sender="alice")
        incoming.message_obj.message = [At(qq="bot"), Plain("  ")]
        incoming.is_at_or_wake_command = True
        await participant.coordinate_direct_request(
            incoming, ProviderRequest(prompt="")
        )
        assert (
            incoming.is_stopped()
            and incoming.get_extra("ovenheart_merged_direct")["source"] == "named"
        )
        assert not first.is_stopped() and not task.done()
        assert (req.prompt, req.image_urls) == before and req.func_tool is tools
        participant.context.llm_generate.assert_not_awaited()
    finally:
        release.set()
        await task


@pytest.mark.parametrize(
    "case",
    [
        "text",
        "quote",
        "image",
        "other_member",
        "other_scope",
        "expired",
        "stopped",
        "disabled",
        "done",
        "sent",
        "other_conversation",
    ],
)
async def test_empty_at_merge_does_not_capture_independent_or_finished_requests(
    participant, case
):
    original = event("original", text="原来的问题", sender="alice")
    original.is_at_or_wake_command = True
    scope, _, _ = await participant.resolve_scope(original)
    entered, release = asyncio.Event(), asyncio.Event()

    async def original_turn():
        await participant.coordinate_direct_request(
            original,
            ProviderRequest(
                prompt=original.message_str,
                conversation=SimpleNamespace(
                    cid="original-conversation", persona_id="default"
                )
                if case == "other_conversation"
                else None,
            ),
        )
        entered.set()
        await release.wait()

    work = asyncio.create_task(original_turn())
    await asyncio.wait_for(entered.wait(), 1)
    incoming = event(
        "later",
        text="",
        sender="bob" if case == "other_member" else "alice",
        session="other" if case == "other_scope" else "123",
    )
    incoming.is_at_or_wake_command = True
    incoming.message_obj.message = [At(qq="bot")]
    if case == "text":
        incoming.message_obj.message.append(Plain("换一个问题"))
        incoming.message_str = "换一个问题"
    elif case == "quote":
        incoming.message_obj.message.insert(
            0, Reply(id="explicit", sender_id="alice", message_str="以此引用为准")
        )
    elif case == "image":
        incoming.message_obj.message.append(
            Image.fromURL("https://example.invalid/new.png")
        )
    elif case == "expired":
        participant.direct_pending[scope, "alice"]["created"] -= 121
    elif case == "stopped":
        original.stop_event()
    elif case == "disabled":
        participant.settings["apply_enabled"] = False
    elif case == "done":
        release.set()
        await work
    elif case == "sent":
        original.set_extra("ovenheart_reply_scope", scope)
        original.set_result(original.plain_result("已经回答"))
        await participant.record_delivery(original)
    try:
        await participant.coordinate_direct_request(
            incoming,
            ProviderRequest(
                prompt="native",
                conversation=SimpleNamespace(
                    cid="different-conversation", persona_id="default"
                )
                if case == "other_conversation"
                else None,
            ),
        )
        assert not incoming.is_stopped() and not incoming.get_extra(
            "ovenheart_merged_direct"
        )
    finally:
        release.set()
        await work


async def test_native_group_context_is_reused_without_copying_group_messages(
    participant,
):
    participant.settings.update(learning_enabled=False)
    participant.context.get_config = lambda umo: {
        "plugin_set": ["*"],
        "provider_ltm_settings": {"group_icl_enable": True},
    }
    older = event("earlier-question", text="插座，请看看我前面的问题", sender="alice")
    await requests(participant, older)
    current = event("wake", text="", sender="alice")
    current.message_obj.message = [At(qq="bot")]
    current.is_at_or_wake_command = True
    scope, _, _ = await participant.resolve_scope(current)
    context = participant.direct_reply_context(current, scope)
    assert context["host_group_context"] is True
    assert context["messages"] == context["reply_context"] == []
    req = ProviderRequest(
        prompt="native", contexts=[{"role": "user", "content": "宿主群聊上下文"}]
    )
    before = deepcopy(req.contexts)
    await participant.apply_participation(current, req)
    assert req.contexts == before
    assert not hasattr(participant, "direct_inputs")


async def test_autonomous_reply_reuses_host_group_context_without_a_second_transcript(
    participant,
):
    participant.context.get_config = lambda umo: {
        "plugin_set": ["*"],
        "provider_ltm_settings": {"group_icl_enable": True},
    }
    incoming = event("autonomous")
    req = (await requests(participant, incoming))[0]
    req.contexts = [{"role": "user", "content": "原生历史保持不动"}]
    before = deepcopy(req.contexts)
    await participant.apply_participation(incoming, req)
    assert incoming.get_extra("ovenheart_context_messages") == []
    assert incoming.get_extra("ovenheart_host_group_context") is True
    import json

    payload = req.extra_user_content_parts[-1].text
    decoder = json.JSONDecoder()
    # The appended structured block still carries its true target and goal.
    block = payload[payload.index('{"reply_goal"') :]
    data, _ = decoder.raw_decode(block)
    assert data["messages"] == data["reply_context"] == []
    assert data["reply_message"]["sender_id"] == incoming.get_sender_id()
    assert req.contexts == before


async def test_pending_request_is_registered_before_native_image_captioning(
    participant,
):
    participant.settings.update(
        mentioned_bot_reply=True, mention_names=["插座"], learning_enabled=False
    )
    participant.context.conversation_manager.get_curr_conversation_id.return_value = (
        "native-existing"
    )
    first = event("before-caption", text="插座，请理解这张图", sender="alice")
    first.message_obj.message = [
        Plain(first.message_str),
        Image.fromURL("https://example.invalid/image.png"),
    ]
    first.set_extra("ovenheart_mention", "插座")
    entered, release = asyncio.Event(), asyncio.Event()

    async def native_pipeline_before_agent():
        ObservationFilter().filter(first, {"plugin_set": ["*"]})
        await participant.capture(first)
        entered.set()
        await release.wait()  # Native captioning has not reached on_llm_request.

    task = asyncio.create_task(native_pipeline_before_agent())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        latest = event("at-during-caption", text="", sender="alice")
        latest.message_obj.message = [At(qq="bot")]
        latest.is_at_or_wake_command = True
        req = ProviderRequest(
            prompt="",
            conversation=SimpleNamespace(cid="native-existing", persona_id="default"),
        )
        await participant.coordinate_direct_request(latest, req)
        assert latest.is_stopped()
        assert not task.done() and not first.is_stopped()
        participant.context.llm_generate.assert_not_awaited()
    finally:
        release.set()
        await task
