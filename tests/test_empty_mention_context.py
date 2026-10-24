"""Adapt the native bare-At greeting without copying or replacing host history."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from astrbot.api.provider import ProviderRequest
from astrbot.builtin_stars.astrbot.group_chat_context import GroupChatContext
from astrbot.builtin_stars.astrbot.main import Main
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.message.components import At, Image, Plain, Reply

from astrbot_plugin_ovenheart import prompts
from astrbot_plugin_ovenheart.main import NATIVE_EMPTY_MENTION_PROMPT
from tests.test_runtime import event

pytest_plugins = ["tests.test_participation"]


async def test_native_bare_at_continues_the_existing_group_context(participant):
    participant.settings.update(learning_enabled=False, participation_mode="off")
    participant.context.get_config = lambda umo: {
        "plugin_set": ["*"],
        "platform_settings": {
            "empty_mention_waiting": True,
            "empty_mention_waiting_need_reply": True,
        },
        "provider_settings": {"image_caption_prompt": ""},
        "provider_ltm_settings": {
            "group_icl_enable": True,
            "group_message_max_cnt": 30,
            "image_caption": False,
            "active_reply": {
                "enable": False,
                "method": "possibility_reply",
                "possibility_reply": 0,
            },
        },
    }
    participant.context.conversation_manager.get_curr_conversation_id.return_value = (
        "native-conv"
    )
    group = GroupChatContext(None, participant.context)
    question = event("question", text="汐兮雨是谁", sender="alice", sender_name="阿力")
    question.message_obj.message = [Plain(question.message_str)]
    peer = event("peer", text="妈妈是你的妈妈呀", sender="peer")
    peer.message_obj.message = [Plain(peer.message_str)]
    incoming = event("bare-at", text="", sender="alice", sender_name="阿力")
    incoming.message_obj.message = [At(qq="bot")]
    incoming.is_at_or_wake_command = True
    for message in (question, peer, incoming):
        await group.handle_message(message)

    # Get the actual host-generated prompt, rather than a copy in the fixture.
    native = Main.handle_empty_mention(
        SimpleNamespace(context=participant.context), incoming
    )
    try:
        request = await anext(native)
    finally:
        await native.aclose()
    assert request.prompt == NATIVE_EMPTY_MENTION_PROMPT
    history = [{"role": "assistant", "content": "我是汐兮雨做的烤箱插座呀"}]
    request.contexts = deepcopy(history)
    request.system_prompt = "宿主人格"
    tools = ToolSet([FunctionTool(name="native_tool", description="", parameters={})])
    request.func_tool = tools
    conversation = request.conversation
    components = list(incoming.get_messages())

    await participant.coordinate_direct_request(incoming, request)
    await participant.apply_persona(incoming, request)
    await group.on_req_llm(incoming, request)
    await participant.apply_participation(incoming, request)
    # Repeated hooks must not accumulate another prompt or transcript.
    await participant.coordinate_direct_request(incoming, request)
    await participant.apply_participation(incoming, request)

    assert request.prompt == prompts.EMPTY_MENTION_CONTINUATION
    assert "友好地询问用户想要聊些什么" not in request.prompt
    assert request.contexts == history
    assert request.conversation is conversation and request.func_tool is tools
    assert request.system_prompt == "宿主人格"
    assert incoming.get_messages() == components and incoming.message_str == ""
    assert not incoming.is_stopped() and not incoming.call_llm
    blocks = [p.text for p in request.extra_user_content_parts]
    assert sum("BEGIN CONTEXT" in block for block in blocks) == 1
    native_block = next(block for block in blocks if "BEGIN CONTEXT" in block)
    assert "[阿力/" in native_block and "汐兮雨是谁" in native_block
    assert "妈妈是你的妈妈呀" in native_block
    target_block = next(b for b in blocks if b.startswith("[OvenHeart reply target]"))
    direct = json.JSONDecoder().raw_decode(
        target_block[target_block.index('{"scope"') :]
    )[0]
    assert direct["reply_message"]["sender_id"] == "alice"
    assert direct["reply_message"]["text"] == ""
    assert direct["messages"] == direct["reply_context"] == []
    assert prompts.EMPTY_MENTION_CONTINUATION in direct["reply_goal"]
    assert len([b for b in blocks if b.startswith("[OvenHeart reply target]")]) == 1
    participant.context.llm_generate.assert_not_awaited()


@pytest.mark.parametrize(
    "case",
    [
        "custom",
        "text",
        "quote",
        "image",
        "other_at",
        "private",
        "disabled",
        "scope_disabled",
        "not_awake",
        "autonomous",
    ],
)
async def test_only_the_allowed_native_bare_at_prompt_is_adapted(participant, case):
    incoming = event("independent", text="")
    incoming.message_obj.message = [At(qq="bot")]
    incoming.is_at_or_wake_command = True
    request = ProviderRequest(prompt=NATIVE_EMPTY_MENTION_PROMPT)
    if case == "custom":
        request.prompt += " 自定义提示，请保留。"
    elif case == "text":
        incoming.message_obj.message.append(Plain("这次回答新问题"))
    elif case == "quote":
        incoming.message_obj.message.insert(0, Reply(id="quote", message_str="引用"))
    elif case == "image":
        incoming.message_obj.message.append(
            Image.fromURL("https://example.invalid/a.png")
        )
    elif case == "other_at":
        incoming.message_obj.message = [At(qq="peer")]
    elif case == "private":
        from astrbot.api.platform import MessageType

        incoming.message_obj.type = MessageType.FRIEND_MESSAGE
    elif case == "disabled":
        participant.settings["apply_enabled"] = False
    elif case == "scope_disabled":
        scope, persona, label = await participant.resolve_scope(incoming, request)
        await participant.store.call(
            "touch_scope", scope, incoming.unified_msg_origin, persona, label
        )
        await participant.store.call(
            "mutate", scope, "profile", {"apply_enabled": False}
        )
    elif case == "not_awake":
        incoming.is_at_or_wake_command = False
    elif case == "autonomous":
        incoming.set_extra("ovenheart_decision", {"scope": "autonomous"})
    before = request.prompt
    await participant.coordinate_direct_request(incoming, request)
    assert request.prompt == before and not incoming.is_stopped()
