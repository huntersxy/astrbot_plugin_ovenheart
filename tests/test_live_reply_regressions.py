"""Production regressions: stopped native spans, replanning and context echoes."""

import asyncio
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.api.platform import MessageType
from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.utils import trace as native_trace

from core.chat.participation import ParticipationController, event_source
from core.chat.reply_boundary import clean_reply
from core.integrations.native_pipeline import NativePipelineBridge
from core.settings import DEFAULTS


class Event:
    def __init__(self, source="first", *, direct=False):
        self.extra = {"ovenheart_native_task": object()}
        self.stopped = False
        self.created_at = time.time()
        self.message_obj = SimpleNamespace(message_id=source)
        self.is_at_or_wake_command = direct
        self.call_llm = False
        self.unified_msg_origin = "test:GroupMessage:room"

    def get_extra(self, key, default=None):
        return self.extra.get(key, default)

    def set_extra(self, key, value):
        self.extra[key] = value

    def is_stopped(self):
        return self.stopped

    def get_message_type(self):
        return MessageType.GROUP_MESSAGE

    def get_platform_name(self):
        return "test"

    def get_platform_id(self):
        return "test"

    def get_self_id(self):
        return "bot"

    def get_sender_id(self):
        return "member"

    def get_group_id(self):
        return "room"

    def is_private_chat(self):
        return False


@pytest.fixture
def trace_frames(monkeypatch):
    frames = []

    def append(payload, **_):
        frames.append(copy.deepcopy(payload))
        return True

    monkeypatch.setitem(native_trace.astrbot_config, "trace_enable", True)
    monkeypatch.setattr(
        native_trace, "get_trace_store", lambda _: SimpleNamespace(append=append)
    )
    monkeypatch.setattr(native_trace, "_get_log_broker", lambda: None)
    return frames


@pytest.mark.parametrize("stop_after_yield", [False, True])
async def test_stopped_native_spans_finish_before_pipeline_end(
    trace_frames, stop_after_yield
):
    event = Event()
    event.trace = native_trace.TraceSpan("test")
    completed = []

    async def process(event):
        event.trace.record(
            "handler_start", plugin="ovenheart", handler="participate", status="running"
        )
        event.trace.record("agent_start", status="running")
        if not stop_after_yield:
            event.stopped = True
        yield
        completed.append("agent")
        event.trace.record("agent_end", status="success")
        completed.append("handler")
        event.trace.record(
            "handler_end", plugin="ovenheart", handler="participate", status="success"
        )

    async def respond(event):
        event.stopped = True

    scheduler = PipelineScheduler.__new__(PipelineScheduler)
    scheduler.stages = [
        SimpleNamespace(process=process),
        SimpleNamespace(process=respond),
    ]
    bridge = NativePipelineBridge()
    original = PipelineScheduler._process_stages
    bridge.install()
    try:
        with native_trace.trace_execution(event.trace):
            await scheduler._process_stages(event)
    finally:
        bridge.disconnect()
    assert PipelineScheduler._process_stages is original
    assert completed == ["agent", "handler"]
    assert trace_frames[-1]["trace"]["has_errors"] is False


async def test_real_pipeline_error_is_preserved(trace_frames):
    event = Event()
    event.trace = native_trace.TraceSpan("test")

    async def fail(event):
        raise RuntimeError("provider failed")
        yield

    scheduler = PipelineScheduler.__new__(PipelineScheduler)
    scheduler.stages = [SimpleNamespace(process=fail)]
    bridge = NativePipelineBridge()
    bridge.install()
    try:
        with pytest.raises(RuntimeError, match="provider failed"):
            with native_trace.trace_execution(event.trace):
                await scheduler._process_stages(event)
    finally:
        bridge.disconnect()
    assert trace_frames[-1]["trace"]["has_errors"] is True


async def test_planned_reply_survives_chatter_but_direct_wake_revokes_it():
    plugin = SimpleNamespace(
        settings=DEFAULTS | {"planner_mode": "maibot"},
        direct_priority=SimpleNamespace(predecessor=lambda _: None),
    )
    controller = ParticipationController(plugin)
    first = Event()
    room = controller.notice(first)
    key = controller.room_key(first)
    room["work"] = {
        "id": 1,
        "source": event_source(first),
        "created": time.time(),
        "planner_session": "planned",
    }
    controller.windows["scope"] = {"sequence": 1}
    plan = {
        "id": 1,
        "scope": "scope",
        "room": key,
        "room_source": event_source(first),
        "sequence": 1,
        "planner_session": "planned",
    }
    controller.notice(Event("ordinary"))
    controller.windows["scope"]["sequence"] = 2
    assert controller.current(plan)
    assert not room["changed"].is_set()
    controller.notice(Event("direct", direct=True))
    assert room["changed"].is_set()
    assert not controller.current(plan)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "笑得这么开心，也不分我一份\n\n哈哈哈\n"
            "<system_reminder>User ID: member, Nickname: name\n"
            "Current datetime: now</system_reminder>\n"
            "[OvenHeart person profiles]\n内部人物资料\n</p>",
            "笑得这么开心，也不分我一份\n\n哈哈哈",
        ),
        ("正常回复\n[OvenHeart person profiles]\n内部资料", "正常回复"),
        ("[OvenHeart person profiles]\n内部资料", ""),
        ("普通聊天与换行\n都保留", "普通聊天与换行\n都保留"),
        (
            "示例：\n```text\n[OvenHeart person profiles]\n示例\n```",
            "示例：\n```text\n[OvenHeart person profiles]\n示例\n```",
        ),
    ],
)
def test_internal_echo_is_removed_without_rewriting_normal_content(text, expected):
    assert clean_reply(text) == expected


async def test_bridge_does_not_replace_a_later_integration():
    bridge = NativePipelineBridge()
    original = PipelineScheduler._process_stages
    try:
        bridge.install()
        replacement = AsyncMock()
        PipelineScheduler._process_stages = replacement
        bridge.disconnect()
        assert PipelineScheduler._process_stages is replacement
    finally:
        PipelineScheduler._process_stages = original


async def test_external_cancellation_still_propagates(trace_frames):
    event = Event()
    event.trace = native_trace.TraceSpan("test")

    async def cancelled(event):
        raise asyncio.CancelledError
        yield

    scheduler = PipelineScheduler.__new__(PipelineScheduler)
    scheduler.stages = [SimpleNamespace(process=cancelled)]
    bridge = NativePipelineBridge()
    bridge.install()
    try:
        with pytest.raises(asyncio.CancelledError):
            with native_trace.trace_execution(event.trace):
                await scheduler._process_stages(event)
    finally:
        bridge.disconnect()
    assert trace_frames[-1]["trace"]["has_errors"] is True
