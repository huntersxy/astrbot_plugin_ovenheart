"""QQ Official platform capability matching."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.core.agent.tool import FunctionTool, ToolSet

from core.chat import group_mentions
from core.platform_capabilities import (
    can_quote_target,
    claim_reply_slot,
    describe,
    is_qq_official_at,
    reply_segment_limit,
)


class Event:
    def __init__(self, platform="qq_official", *, private=False, message_id="m-1"):
        self.platform = platform
        self.private = private
        self.message_obj = SimpleNamespace(message_id=message_id)
        self.bot = SimpleNamespace(call_action=AsyncMock())
        self.extra = {}

    def get_platform_name(self):
        return self.platform

    def get_group_id(self):
        return "group-openid"

    def is_private_chat(self):
        return self.private

    def get_extra(self, key, default=None):
        return self.extra.get(key, default)

    def set_extra(self, key, value):
        self.extra[key] = value


@pytest.mark.parametrize("platform", ["qq_official", "qq_official_webhook"])
def test_qq_official_capabilities_use_openid_and_passive_limits(platform):
    event = Event(platform)
    caps = describe(event)
    assert caps["user_id_kind"] == "OpenID"
    assert caps["group_member_lookup"] is False
    assert caps["targeted_quote"] == "current_message_only"
    assert caps["passive_reply_window_seconds"] == 300
    assert caps["passive_reply_limit"] == 5
    assert reply_segment_limit(event, 10) == 3


def test_non_qq_platform_keeps_configured_reply_segments_and_quotes():
    event = Event("aiocqhttp")
    assert reply_segment_limit(event, 10) == 10
    assert can_quote_target(event, "old-message") is True
    assert describe(event) == {"platform": "aiocqhttp"}


def test_qq_official_only_quotes_the_current_inbound_message():
    event = Event(message_id="current")
    assert can_quote_target(event, "current") is True
    assert can_quote_target(event, "old-message") is False
    assert (
        can_quote_target(Event(private=True, message_id="current"), "old-message")
        is False
    )


def test_qq_official_reply_slots_follow_the_scene_limit():
    group = Event()
    assert [claim_reply_slot(group) for _ in range(6)] == [True] * 5 + [False]
    private = Event(private=True)
    assert [claim_reply_slot(private) for _ in range(5)] == [True] * 4 + [False]


def test_qq_official_channel_at_is_detected_from_raw_mentions():
    event = Event()
    event.message_obj.raw_message = SimpleNamespace(
        channel_id="channel-openid",
        mentions=[SimpleNamespace(id="bot-openid")],
        content="<@bot-openid> hello",
    )
    assert is_qq_official_at(event) is True


async def test_qq_official_member_tools_are_not_advertised_or_called():
    event = Event()
    tool = FunctionTool(
        name=group_mentions.MENTION_TOOL,
        description="mention",
        parameters={"type": "object", "properties": {}},
    )
    request = SimpleNamespace(func_tool=ToolSet([tool]))
    caps = group_mentions.capabilities(event, request)
    assert caps["available"] is False
    assert caps["mention_tool"] is None
    group_mentions.hide_unsupported_tools(event, request)
    assert request.func_tool.tools == []
    result = await group_mentions.find_members(event, "someone")
    assert result["success"] is False
    event.bot.call_action.assert_not_awaited()
