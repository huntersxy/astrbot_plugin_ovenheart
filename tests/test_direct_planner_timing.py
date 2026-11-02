"""Direct wakes plan with a first-turn budget and fall back without double-send."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

PARENT = Path(__file__).resolve().parents[2]
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from astrbot.api.platform import MessageType  # noqa: E402
from astrbot.api.provider import ProviderRequest  # noqa: E402

from astrbot_plugin_ovenheart.core.settings import DEFAULTS  # noqa: E402
from astrbot_plugin_ovenheart.main import PersonaPlugin  # noqa: E402


class Event:
    def __init__(self, *, wake=True, private=False):
        self.is_at_or_wake_command = wake
        self.private = private
        self.call_llm = False
        self._has_send_oper = False
        self.unified_msg_origin = "aiocqhttp:GroupMessage:1"
        self.extra = {}
        self.message_obj = SimpleNamespace(
            message_id="m1",
            sender=SimpleNamespace(user_id="111", nickname="member"),
            message=[],
        )
        self.activated_handlers = []
        self.created_at = 0

    def get_message_type(self):
        return MessageType.FRIEND_MESSAGE if self.private else MessageType.GROUP_MESSAGE

    def get_extra(self, key, default=None):
        if key == "activated_handlers":
            return self.activated_handlers
        return self.extra.get(key, default)

    def set_extra(self, key, value):
        self.extra[key] = value

    def get_result(self):
        return None

    def get_message_str(self):
        return "你好"

    def get_sender_id(self):
        return "111"

    def get_sender_name(self):
        return "member"

    def get_self_id(self):
        return "999"

    def get_platform_name(self):
        return "aiocqhttp"

    def is_private_chat(self):
        return self.private

    def is_stopped(self):
        return False


def _plugin(**overrides):
    owner = PersonaPlugin.__new__(PersonaPlugin)
    owner.settings = DEFAULTS | {
        "apply_enabled": True,
        "planner_mode": "maibot",
        "planner_direct_first_timeout": 12,
        "retrieval_timeout": 30,
        **overrides,
    }
    owner.closing = False
    owner.debug = lambda *_, **__: None
    owner.permitted = AsyncMock(return_value=True)
    owner.check_mention = AsyncMock(return_value=True)
    owner.prioritize_direct = AsyncMock()
    owner.resolve_scope = AsyncMock(return_value=("scope", "persona", "label"))
    owner.register_direct_request = lambda *_: None
    owner.direct_priority = SimpleNamespace(watch=lambda *_args, **_kwargs: None)
    owner.participation = SimpleNamespace(
        consider=AsyncMock(return_value=None),
        after_reply=AsyncMock(return_value=None),
        planner=SimpleNamespace(finish_event=lambda *_: None),
    )
    owner.collect_planner_request = AsyncMock(
        return_value=ProviderRequest(prompt="collected")
    )
    owner.participation_request = AsyncMock()
    return owner


async def _drain(plugin, event):
    return [item async for item in plugin.participate(event)]


@pytest.mark.asyncio
async def test_direct_fallback_yields_collected_request_when_planner_misses():
    plugin = _plugin()
    event = Event()
    yielded = await _drain(plugin, event)
    assert len(yielded) == 1
    assert yielded[0].prompt == "collected"
    assert event.call_llm is True
    plugin.participation.consider.assert_awaited_once()
    assert plugin.participation.consider.await_args.kwargs.get("direct") is True


@pytest.mark.asyncio
async def test_direct_reply_plan_uses_participation_request():
    plugin = _plugin()
    plan = {"action": "reply", "direct": True, "scope": "scope", "id": 1}
    plugin.participation.consider = AsyncMock(return_value=plan)

    async def requests(_event, _plan):
        yield ProviderRequest(prompt="planned")

    plugin.participation_request = requests
    plugin.participation.after_reply = AsyncMock(return_value=None)
    event = Event()
    yielded = await _drain(plugin, event)
    assert [item.prompt for item in yielded] == ["planned"]
    assert event.get_extra("ovenheart_planner_initial_request").prompt == "collected"


@pytest.mark.asyncio
async def test_direct_wall_clock_timeout_falls_back():
    plugin = _plugin(planner_direct_first_timeout=3, retrieval_timeout=8)

    async def slow_consider(*_args, **_kwargs):
        await asyncio.sleep(60)
        return {"action": "reply"}

    plugin.participation.consider = AsyncMock(side_effect=slow_consider)
    finished = []
    plugin.participation.planner = SimpleNamespace(
        finish_event=lambda event: finished.append(event)
    )
    event = Event()
    started = asyncio.get_event_loop().time()
    yielded = await asyncio.wait_for(_drain(plugin, event), timeout=20)
    assert asyncio.get_event_loop().time() - started < 20
    assert len(yielded) == 1
    assert yielded[0].prompt == "collected"
    assert finished


@pytest.mark.asyncio
async def test_legacy_direct_still_returns_to_host_path():
    plugin = _plugin(planner_mode="legacy")
    event = Event()
    yielded = await _drain(plugin, event)
    assert yielded == []
    plugin.participation.consider.assert_not_awaited()
