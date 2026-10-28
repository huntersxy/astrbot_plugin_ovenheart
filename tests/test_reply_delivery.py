"""Exercise the native sender and runner with request-local interactive routing."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolSet
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.cron.events import CronMessageEvent
from astrbot.core.pipeline.respond.stage import RespondStage
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.provider.entities import LLMResponse
from astrbot.core.star.context import Context
from astrbot.core.tools.message_tools import SendMessageToUserTool

from astrbot_plugin_ovenheart.integrations.reply_delivery import bind_text_delivery
from astrbot_plugin_ovenheart.main import ParticipationToolSet
from tests.test_conversation_planner import action
from tests.test_runtime import event

pytest_plugins = ["tests.test_conversation_planner"]


async def test_native_tool_text_returns_to_one_normal_reply_instead_of_ack(
    native_planner,
):
    p, provider = native_planner
    incoming = event("text-delivery", text="捏捏耳朵")
    incoming.is_at_or_wake_command = True
    wire = AsyncMock()
    incoming.send = wire
    p.context.send_message = AsyncMock(return_value=True)
    tool = SendMessageToUserTool()
    tools = ToolSet([tool])
    req = ProviderRequest(prompt=incoming.message_str, func_tool=tools)
    await p.apply_persona(incoming, req)
    await p.apply_participation(incoming, req)
    assert req.func_tool is not tools and tools.get_tool(tool.name) is tool
    body = "呜，耳朵都被捏软啦。真是拿你没办法。"
    provider.text_chat.side_effect = [
        action("send_message_to_user", messages=[{"type": "plain", "text": body}]),
        LLMResponse(role="assistant", completion_text=body),
    ]
    response = await p.context.tool_loop_agent(
        event=incoming,
        chat_provider_id="test-provider",
        prompt=req.prompt,
        system_prompt=req.system_prompt,
        tools=req.func_tool,
        max_steps=4,
    )
    p.context.send_message.assert_not_awaited()
    returned = provider.text_chat.await_args.kwargs["contexts"]
    assert any(
        m.role == "tool" and "未发送任何消息" in str(m.content) for m in returned
    )
    incoming.set_result(incoming.plain_result(response.completion_text))
    respond = RespondStage()
    respond.config = {}
    respond.platform_settings = {}
    respond.enable_seg = False
    await respond.process(incoming)
    assert wire.await_count == 1
    assert wire.await_args.args[0].get_plain_text() == body
    assert not incoming.get_extra("_send_message_to_user_current_session_plain_texts")


@pytest.mark.parametrize(
    "session,parts,blocked",
    [
        (None, [{"type": "image", "url": "https://example.invalid/image.png"}], False),
        ("qq:GroupMessage:other", [{"type": "plain", "text": "通知内容"}], False),
        (
            None,
            [
                {"type": "plain", "text": "配文"},
                {"type": "image", "url": "https://example.invalid/image.png"},
            ],
            True,
        ),
        ("123", [{"type": "plain", "text": "当前聊天正文"}], True),
    ],
)
async def test_media_and_other_sessions_keep_native_delivery_and_policy(
    session, parts, blocked
):
    incoming = event()
    incoming.role = "admin"
    context = Context.__new__(Context)
    context.send_message = AsyncMock(return_value=True)
    context.get_config = lambda **_: {"provider_settings": {}}
    run = ContextWrapper(context=AstrAgentContext(context=context, event=incoming))
    native = SendMessageToUserTool()
    policy = {native.name}
    tools = ParticipationToolSet([native], lambda name: name in policy)
    request = ProviderRequest(func_tool=tools)
    bind_text_delivery(incoming, request)
    routed = request.func_tool.get_tool(native.name)
    result = await routed.call(
        run, messages=parts, **({"session": session} if session else {})
    )
    assert context.send_message.await_count == (0 if blocked else 1)
    assert ("未发送任何消息" in result) == blocked
    if session == "qq:GroupMessage:other":
        incoming.role = "member"
        denied = await routed.call(run, messages=parts, session=session)
        assert "Permission denied" in denied and context.send_message.await_count == 1
    assert tools.get_tool(native.name) is native
    policy.clear()
    assert request.func_tool.get_tool(native.name) is None


def test_cron_retains_native_text_sender():
    source = CronMessageEvent(
        context=SimpleNamespace(),
        session=MessageSession.from_str("qq:FriendMessage:123"),
        message="定时提醒",
    )
    tools = ToolSet([SendMessageToUserTool()])
    request = ProviderRequest(func_tool=tools)
    bind_text_delivery(source, request)
    assert request.func_tool is tools
