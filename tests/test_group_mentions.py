"""Explicit third-party mentions remain real At segments on native delivery."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

PARENT = Path(__file__).resolve().parents[2]
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from astrbot.core.agent.tool import FunctionTool, ToolSet  # noqa: E402
from astrbot.core.message.components import At, Plain  # noqa: E402
from astrbot.core.message.message_event_result import MessageChain, MessageEventResult  # noqa: E402
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (  # noqa: E402
    AiocqhttpMessageEvent,
)

from astrbot_plugin_ovenheart.core.chat import group_mentions  # noqa: E402
from astrbot_plugin_ovenheart.core.chat.reply_rhythm import ReplyRhythm  # noqa: E402
from astrbot_plugin_ovenheart.core.settings import DEFAULTS  # noqa: E402
from astrbot_plugin_ovenheart.main import PersonaPlugin  # noqa: E402


class Event:
    def __init__(self):
        self.extra = {
            "ovenheart_reply_target": {"sender_id": "111", "sender": "提问者"}
        }
        self.message_obj = SimpleNamespace(message=[])
        self.result = None
        self.unified_msg_origin = "qq:GroupMessage:777"
        self.bot = SimpleNamespace(
            call_action=AsyncMock(
                return_value=[
                    {"user_id": 111, "nickname": "提问者", "card": ""},
                    {"user_id": 222, "nickname": "另一个昵称", "card": "幽幽子"},
                    {"user_id": 333, "nickname": "群友", "card": ""},
                ]
            )
        )
        self.sent = []
        self.sent_messages = []

    def get_extra(self, key, default=None):
        return self.extra.get(key, default)

    def set_extra(self, key, value):
        self.extra[key] = value

    def get_group_id(self):
        return "777"

    def get_platform_name(self):
        return "aiocqhttp"

    def get_self_id(self):
        return "999"

    def get_sender_id(self):
        return "111"

    def get_sender_name(self):
        return "提问者"

    def get_result(self):
        return self.result

    def is_stopped(self):
        return False

    async def send(self, message):
        self.sent_messages.append(message)
        self.sent.extend(message.chain)

    async def send_streaming(self, stream, use_fallback=False):
        async for chunk in stream:
            if chunk is not None:
                self.sent.extend(chunk.chain)


def plugin():
    owner = PersonaPlugin.__new__(PersonaPlugin)
    owner.ready = True
    owner.settings = {"apply_enabled": True}
    owner.permitted = AsyncMock(return_value=True)
    owner.debug = lambda *_, **__: None
    return owner


async def test_member_card_lookup_and_buffered_command_keep_the_requested_bot():
    event, owner = Event(), plugin()
    group_mentions.bind_mentions(event)
    result = json.loads(await owner.find_group_members(event, "幽幽子", 0))
    assert result["members"] == [
        {"user_id": "222", "nickname": "另一个昵称", "card": "幽幽子"}
    ]
    event.bot.call_action.assert_awaited_once_with(
        "get_group_member_list", group_id=777, self_id="999"
    )
    result = json.loads(await owner.mention_group_members(event, ["222"]))
    assert result["status"] == "planned_not_sent"
    assert event.sent == []
    event.result = MessageEventResult(chain=[Plain("@幽幽子 /今日猪猪")])

    owner.realize_result_mentions(event, None)

    wire = [
        await AiocqhttpMessageEvent._from_segment_to_dict(part)
        for part in event.result.chain
    ]
    assert [part["data"]["qq"] for part in wire if part["type"] == "at"] == ["222"]
    assert event.result.get_plain_text().strip() == "/今日猪猪"
    assert len([part for part in event.result.chain if isinstance(part, Plain)]) == 1


@pytest.mark.parametrize("text", ["/今日猪猪", "@幽幽子 /今日猪猪"])
async def test_streamed_command_mentions_once_even_with_a_split_address(text):
    event, owner = Event(), plugin()
    group_mentions.bind_mentions(event)
    await owner.mention_group_members(event, ["222", "333"])

    async def chunks():
        for character in text:
            yield MessageChain(chain=[Plain(character)])

    await event.send_streaming(owner.direct_reply_stream(event, chunks()))

    assert [str(part.qq) for part in event.sent if isinstance(part, At)] == [
        "222",
        "333",
    ]
    assert (
        "".join(part.text for part in event.sent if isinstance(part, Plain)).strip()
        == "/今日猪猪"
    )
    bound = event.send_streaming
    group_mentions.bind_mentions(event)
    assert event.send_streaming is bound
    assert event.get_extra("ovenheart_reply_mentions") == []


@pytest.mark.parametrize(
    "chunks",
    [
        ["放心", "啦", "，没事的。"],
        ["谢谢鱼", "鱼。"],
        ["认领", "的这一套。"],
        ["第一段\n", "\n第二段", "。"],
    ],
)
async def test_native_onebot_collects_stream_chunks_without_inserting_newlines(
    monkeypatch, chunks
):
    class NativeEvent(Event, AiocqhttpMessageEvent):
        send_streaming = AiocqhttpMessageEvent.send_streaming

    event, owner = NativeEvent(), plugin()
    event.platform_meta = SimpleNamespace(name="aiocqhttp")
    monkeypatch.setattr(
        "astrbot.core.platform.astr_message_event.Metric.upload", AsyncMock()
    )
    group_mentions.bind_mentions(event)

    async def source():
        for text in chunks:
            yield MessageChain(chain=[Plain(text)])

    await event.send_streaming(
        owner.direct_reply_stream(event, source()), use_fallback=False
    )

    assert len(event.sent_messages) == 1
    wire = await AiocqhttpMessageEvent._parse_onebot_json(event.sent_messages[0])
    text = "".join(item["data"]["text"] for item in wire if item["type"] == "text")
    assert text == "".join(chunks)


async def test_ambiguous_members_and_unknown_ids_do_not_choose_or_send_anyone():
    event = Event()
    event.bot.call_action.return_value.append(
        {"user_id": 444, "nickname": "幽幽子", "card": ""}
    )
    found = await group_mentions.find_members(event, "幽幽子")
    assert {row["user_id"] for row in found["members"]} == {"222", "444"}
    assert not event.get_extra("ovenheart_reply_mentions")
    await group_mentions.plan_mentions(event, ["222"])
    result = await group_mentions.plan_mentions(event, ["not-in-group"])
    assert result["success"] is False
    assert event.get_extra("ovenheart_reply_mentions") == []
    assert event.sent == []


async def test_segmented_reply_keeps_mentions_only_on_the_first_message():
    event = Event()
    event.set_extra("ovenheart_decision", {"direct": True})
    await group_mentions.plan_mentions(event, ["222"])
    rhythm = ReplyRhythm(
        SimpleNamespace(
            settings=DEFAULTS | {"reply_typing_speed": 0},
            context=SimpleNamespace(
                get_config=lambda _: {
                    "platform_settings": {"segmented_reply": {"split_words": ["。"]}}
                }
            ),
            check_participation=AsyncMock(return_value=True),
        )
    )
    rhythm.bind(event)

    await event.send(
        MessageChain(
            chain=group_mentions.with_mentions(event, [Plain("第一句。第二句。")])
        )
    )

    assert len(event.sent_messages) == 2
    assert [str(part.qq) for part in event.sent if isinstance(part, At)] == ["222"]
    assert not any(isinstance(part, At) for part in event.sent_messages[1].chain)


async def test_unavailable_member_api_and_permission_errors_do_not_claim_success():
    event, owner = Event(), plugin()
    event.bot.call_action.side_effect = RuntimeError("offline")
    assert (await group_mentions.find_members(event, "幽幽子"))["success"] is False
    assert (await group_mentions.plan_mentions(event, ["222"]))["success"] is False
    assert event.get_extra("ovenheart_reply_mentions") == []
    event.bot.call_action.reset_mock()
    owner.permitted.return_value = False
    assert (
        json.loads(await owner.find_group_members(event, "幽幽子", 0))["success"]
        is False
    )
    assert (
        json.loads(await owner.mention_group_members(event, ["222"]))["success"]
        is False
    )
    event.bot.call_action.assert_not_awaited()


def test_mention_capability_follows_the_actual_enabled_toolset():
    event = Event()
    tool = FunctionTool(
        name=group_mentions.MENTION_TOOL,
        description="mention",
        parameters={"type": "object", "properties": {}},
    )
    request = SimpleNamespace(func_tool=ToolSet([tool]))
    assert group_mentions.capabilities(event, request)["available"] is True
    tool.active = False
    assert group_mentions.capabilities(event, request)["available"] is False
