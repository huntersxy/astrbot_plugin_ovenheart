"""Reply receipt timeout must not drop a send already in flight."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.auxiliary import AuxiliaryUsage
from core.chat.planner import PlannerSession
from core.settings import DEFAULTS


class Event:
    def __init__(self):
        self.extra = {}
        self.stopped = False
        self._has_send_oper = False
        self.unified_msg_origin = "test:GroupMessage:room"

    def is_private_chat(self):
        return False

    def get_extra(self, key, default=None):
        return self.extra.get(key, default)

    def set_extra(self, key, value):
        self.extra[key] = value

    def stop_event(self):
        self.stopped = True


class Owner:
    def __init__(self):
        self.forgotten = False

    def forget(self, scope, session):
        self.forgotten = True


def session(event, timeout=0.05):
    planner = PlannerSession.__new__(PlannerSession)
    planner.event = event
    planner.settings = {"planner_reply_timeout": timeout}
    planner.pending = asyncio.get_running_loop().create_future()
    planner.action = {"action": "reply", "target_sequence": 3}
    planner.messages = []
    planner.replied = set()
    planner.reacted = set()
    planner.owner = Owner()
    planner.scope = "room"
    return planner


@pytest.mark.parametrize("previous_send", [False, True])
async def test_slow_unsent_reply_is_waited_once_then_cancelled(previous_send):
    event = Event()
    event._has_send_oper = previous_send
    native = asyncio.create_task(asyncio.sleep(30))
    event.set_extra("ovenheart_native_task", lambda: native)
    planner = session(event)
    with pytest.raises(asyncio.TimeoutError):
        await planner.wait_reply_receipt()
    await asyncio.gather(native, return_exceptions=True)
    assert event.stopped is True
    assert event.get_extra("ovenheart_planner_status") == "reply_timeout"
    assert event.get_extra("agent_stop_requested") is True
    assert native.cancelled()
    assert planner.owner.forgotten is True
    assert planner.replied == set()
    assert event.get_extra("ovenheart_planner_receipt") is None


async def test_unsent_reply_uses_the_second_window_when_it_finishes():
    event = Event()
    native = asyncio.create_task(asyncio.sleep(30))
    event.set_extra("ovenheart_native_task", lambda: native)
    # Leave both deadlines clear of Windows' coarse timer ticks.
    planner = session(event, timeout=0.2)

    async def finish():
        await asyncio.sleep(0.3)
        planner.acknowledge({"send_hook": True, "reply_text": "后来发出"})

    task = asyncio.create_task(finish())
    result = await planner.wait_reply_receipt()
    await task
    assert result["reply_text"] == "后来发出"
    assert result["send_hook"] is True
    assert event.stopped is False
    assert native.cancelled() is False
    assert planner.owner.forgotten is False
    native.cancel()
    await asyncio.gather(native, return_exceptions=True)


async def test_slow_receipt_after_send_does_not_cancel_or_resend():
    event = Event()
    event.set_extra("ovenheart_actual_text_segments", ["已经发出"])
    native = asyncio.create_task(asyncio.sleep(30))
    event.set_extra("ovenheart_native_task", lambda: native)
    planner = session(event)
    result = await planner.wait_reply_receipt()
    assert native.cancelled() is False
    native.cancel()
    await asyncio.gather(native, return_exceptions=True)
    assert result["delivery_uncertain"] is True
    assert result["reply_text"] == "已经发出"
    assert result["send_hook"] is True
    assert "不要重发" in result["delivery_status"]
    assert event.stopped is False
    assert event.get_extra("agent_stop_requested") is None
    assert planner.owner.forgotten is False
    assert event.get_extra("ovenheart_planner_status") == "reply_sent_receipt_timeout"


async def test_dispatched_reply_keeps_the_real_receipt_when_it_arrives_late():
    event = Event()
    event._has_send_oper = True
    event.set_extra("ovenheart_planner_receipt", {"send_hook": True})
    native = asyncio.create_task(asyncio.sleep(30))
    event.set_extra("ovenheart_native_task", lambda: native)
    planner = session(event)

    async def finish():
        await asyncio.sleep(0.08)
        planner.acknowledge(
            {"send_hook": True, "reply_text": "回执到了", "delivery_uncertain": False}
        )

    task = asyncio.create_task(finish())
    result = await planner.wait_reply_receipt()
    await task
    assert native.cancelled() is False
    native.cancel()
    await asyncio.gather(native, return_exceptions=True)
    assert result["reply_text"] == "回执到了"
    assert result["delivery_uncertain"] is False
    assert event.stopped is False
    assert planner.owner.forgotten is False


@pytest.mark.parametrize("timeout", [120, 400, 600])
async def test_planner_tool_budget_allows_both_reply_windows(timeout):
    planner = session(Event(), timeout)
    planner.settings = DEFAULTS | {
        "planner_reply_timeout": timeout,
        "planner_provider_id": "test-provider",
    }
    planner.persona = "default"
    planner.expressions = {}
    planner.running_tools = 0
    planner.tool_changed = asyncio.Event()
    planner.actions = asyncio.Queue()
    planner.direct = False
    planner.recall = AsyncMock(return_value={})
    planner.check_current = AsyncMock()
    tool_loop = AsyncMock(return_value=SimpleNamespace(role="assistant"))
    context = SimpleNamespace(
        persona_manager=SimpleNamespace(get_persona_v3_by_id=lambda _: None),
        get_config=lambda _: {},
    )
    auxiliary = AuxiliaryUsage(context, None)
    auxiliary.tool_loop = tool_loop
    planner.plugin = SimpleNamespace(
        context=context,
        emoji_actions=SimpleNamespace(proactive=AsyncMock(return_value={})),
        auxiliary=auxiliary,
    )

    await planner.run({})

    budget = tool_loop.call_args.kwargs["tool_call_timeout"]
    assert budget >= 600
    assert budget > 2 * timeout
