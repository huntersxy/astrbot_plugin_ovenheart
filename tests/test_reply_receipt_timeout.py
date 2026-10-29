"""Reply receipt timeout must not drop a send already in flight."""

from __future__ import annotations

import asyncio

import pytest

from core.chat.planner import PlannerSession


class Event:
    def __init__(self):
        self.extra = {}
        self.stopped = False
        self._has_send_oper = False

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


async def test_slow_unsent_reply_is_waited_once_then_cancelled():
    event = Event()
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


async def test_unsent_reply_uses_the_second_window_when_it_finishes():
    event = Event()
    native = asyncio.create_task(asyncio.sleep(30))
    event.set_extra("ovenheart_native_task", lambda: native)
    planner = session(event)

    async def finish():
        await asyncio.sleep(0.08)
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
