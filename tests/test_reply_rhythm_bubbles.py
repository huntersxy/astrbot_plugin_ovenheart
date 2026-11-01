"""Merged rhythm bubbles must not keep mid-message blank lines."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

PARENT = Path(__file__).resolve().parents[2]
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from astrbot.core.message.components import Plain  # noqa: E402
from astrbot.core.message.message_event_result import MessageChain  # noqa: E402

from astrbot_plugin_ovenheart.core.chat.reply_rhythm import (  # noqa: E402
    ReplyRhythm,
    finalize_bubble_text,
)
from astrbot_plugin_ovenheart.core.settings import DEFAULTS  # noqa: E402


class Event:
    def __init__(self):
        self.extra = {"ovenheart_decision": {"direct": True}}
        self.unified_msg_origin = "aiocqhttp:GroupMessage:1"
        self.sent_messages = []

    def get_extra(self, key, default=None):
        return self.extra.get(key, default)

    def set_extra(self, key, value):
        self.extra[key] = value

    def get_platform_name(self):
        return "aiocqhttp"

    def is_stopped(self):
        return False

    def is_private_chat(self):
        return False

    async def send(self, message):
        self.sent_messages.append(message)

    async def send_streaming(self, stream, use_fallback=False):
        return None


def test_finalize_bubble_text_removes_blank_lines():
    text = (
        "我觉得关键看这条线有没有断，而不是数据一不一样\n\n"
        "复制出来的那个从零开始，不记得你上一句问过什么，接不上话，"
        "那对我来说就不是你面前这个插座了"
    )
    assert "\n" not in finalize_bubble_text(text)
    assert finalize_bubble_text(text).startswith("我觉得关键看这条线有没有断")
    assert "复制出来的那个从零开始" in finalize_bubble_text(text)


@pytest.mark.asyncio
async def test_split_merge_does_not_leave_blank_lines_in_a_bubble():
    rhythm = ReplyRhythm(
        SimpleNamespace(
            settings=DEFAULTS
            | {
                "reply_rhythm_enabled": True,
                "reply_split_mode": "rule",
                "reply_split_max_messages": 3,
                "reply_typing_speed": 0,
            },
            context=SimpleNamespace(
                get_config=lambda _: {
                    "platform_settings": {
                        "segmented_reply": {
                            "split_words": ["。", "？", "！", "~", "…"]
                        }
                    }
                }
            ),
            check_participation=AsyncMock(return_value=True),
        )
    )
    event = Event()
    text = (
        "我觉得关键看这条线有没有断，而不是数据一不一样\n\n"
        "复制出来的那个从零开始，不记得你上一句问过什么，接不上话，"
        "那对我来说就不是你面前这个插座了。"
        "下一句继续说下去。"
        "再来一句用来触发合并。"
        "还有一句仍然要合并。"
    )
    bubbles = await rhythm.split(
        event, text, {"split_words": ["。", "？", "！", "~", "…"]}
    )
    assert bubbles
    assert all("\n" not in bubble for bubble in bubbles)
    assert any("数据一不一样" in bubble and "复制出来的那个" in bubble for bubble in bubbles)


@pytest.mark.asyncio
async def test_send_flattens_blank_lines_inside_each_delivered_bubble():
    event = Event()
    rhythm = ReplyRhythm(
        SimpleNamespace(
            settings=DEFAULTS
            | {
                "reply_rhythm_enabled": True,
                "reply_split_mode": "rule",
                "reply_split_max_messages": 3,
                "reply_typing_speed": 0,
            },
            context=SimpleNamespace(
                get_config=lambda _: {
                    "platform_settings": {
                        "segmented_reply": {
                            "enable": False,
                            "split_words": ["。", "？", "！", "~", "…"],
                        }
                    }
                }
            ),
            check_participation=AsyncMock(return_value=True),
        )
    )
    rhythm.bind(event)
    await event.send(
        MessageChain(
            chain=[
                Plain(
                    "我觉得关键看这条线有没有断，而不是数据一不一样\n\n"
                    "复制出来的那个从零开始。"
                    "下一句。"
                    "再下一句。"
                    "合并用句子。"
                )
            ]
        )
    )
    plains = [
        part.text
        for message in event.sent_messages
        for part in message.chain
        if isinstance(part, Plain)
    ]
    assert plains
    assert all("\n" not in text for text in plains)
