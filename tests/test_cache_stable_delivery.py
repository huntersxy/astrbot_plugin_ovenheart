"""Keep request prefixes cache-stable across ordinary turns."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from astrbot_plugin_ovenheart.core.integrations.reply_delivery import (  # noqa: E402
    SEND_MESSAGE_DESCRIPTION,
    SEND_MESSAGE_PARAMETERS,
    TEXT_DELIVERY_RULE,
    bind_text_delivery,
)
from astrbot_plugin_ovenheart.core.prompts import AVATAR_TOOL_HINT  # noqa: E402
from astrbot.core.agent.tool import FunctionTool, ToolSet  # noqa: E402

from astrbot_plugin_ovenheart.main import (  # noqa: E402
    ParticipationToolSet,
    narrow_toolset,
)


def test_narrow_toolset_keeps_host_schema_and_filters_lookup():
    tools = ToolSet(
        [
            FunctionTool(name="alpha", parameters={"type": "object", "properties": {}}, description="a"),
            FunctionTool(name="beta", parameters={"type": "object", "properties": {}}, description="b"),
            FunctionTool(
                name="gamma",
                parameters={"type": "object", "properties": {}},
                description="c",
                active=False,
            ),
        ]
    )
    narrowed = narrow_toolset(tools, {"alpha"}, skills_like=False)
    assert isinstance(narrowed, ParticipationToolSet)
    assert [tool.name for tool in narrowed.tools] == ["alpha", "beta"]
    assert narrowed.get_tool("alpha") is not None
    assert narrowed.get_tool("beta") is None


@pytest.mark.asyncio
async def test_bind_text_delivery_rewrites_stable_tool_schema_not_system(monkeypatch):
    class DummySend:
        def __init__(self):
            self.name = "send_message_to_user"
            self.description = "native description"
            self.parameters = {
                "type": "object",
                "properties": {
                    "messages": {
                        "description": "native",
                        "items": {"properties": {"type": {}, "text": {}}},
                    },
                    "session": {"description": "native"},
                },
            }
            self.active = True
            self.call = AsyncMock(return_value="sent")

    monkeypatch.setattr(
        "astrbot.core.tools.message_tools.SendMessageToUserTool", DummySend
    )

    class Bag:
        def __init__(self, tools):
            self.tools = list(tools)

        def get_tool(self, name):
            return next((item for item in self.tools if item.name == name), None)

    tool = DummySend()
    request = SimpleNamespace(
        system_prompt="host system",
        func_tool=Bag([tool]),
        extra_user_content_parts=[],
    )
    event = SimpleNamespace()
    bind_text_delivery(event, request)
    assert request.system_prompt == "host system"
    bound = request.func_tool.get_tool("send_message_to_user")
    assert bound.description == SEND_MESSAGE_DESCRIPTION
    assert (
        bound.parameters["properties"]["messages"]["description"]
        == SEND_MESSAGE_PARAMETERS["messages"]
    )
    assert TEXT_DELIVERY_RULE.strip() in request.extra_user_content_parts[0].text
    # Identical rewrite on a second bind keeps the cache hash stable.
    second = SimpleNamespace(
        system_prompt="host system",
        func_tool=Bag([DummySend()]),
        extra_user_content_parts=[],
    )
    bind_text_delivery(event, second)
    again = second.func_tool.get_tool("send_message_to_user")
    assert again.description == bound.description
    assert (
        again.parameters["properties"]["session"]["description"]
        == bound.parameters["properties"]["session"]["description"]
    )


@pytest.mark.asyncio
async def test_bind_text_delivery_rejects_current_chat_plain(monkeypatch):
    class DummySend:
        def __init__(self):
            self.name = "send_message_to_user"
            self.description = "native"
            self.parameters = {
                "type": "object",
                "properties": {
                    "messages": {
                        "description": "native",
                        "items": {"properties": {"type": {}, "text": {}}},
                    },
                    "session": {"description": "native"},
                },
            }
            self.active = True
            self.call = AsyncMock(return_value="sent")

    monkeypatch.setattr(
        "astrbot.core.tools.message_tools.SendMessageToUserTool", DummySend
    )
    monkeypatch.setattr(
        "astrbot.core.cron.events.CronMessageEvent", type("CronMessageEvent", (), {})
    )

    class Bag:
        def __init__(self, tools):
            self.tools = list(tools)

        def get_tool(self, name):
            return next((item for item in self.tools if item.name == name), None)

    tool = DummySend()
    request = SimpleNamespace(
        system_prompt="host system",
        func_tool=Bag([tool]),
        extra_user_content_parts=[],
    )
    bind_text_delivery(SimpleNamespace(), request)
    bound = request.func_tool.get_tool("send_message_to_user")
    event = SimpleNamespace(
        unified_msg_origin="qq:GroupMessage:1",
        get_session_id=lambda: "qq:GroupMessage:1",
    )
    context = SimpleNamespace(context=SimpleNamespace(event=event))
    result = await bound.call(
        context,
        messages=[{"type": "plain", "text": "hello"}],
        session="",
    )
    assert "立刻把要说的话作为本轮回复正文" in result
    tool.call.assert_not_awaited()


def test_avatar_hint_constant_is_stable():
    assert "avatar_references" in AVATAR_TOOL_HINT
