"""Guards that keep explicit @ replies off unbounded prep waits."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from core.chat.conversation import ConversationContext
from core.chat.direct_priority import DirectPriority
from core.chat.preparation import context_reads


class Event:
    def __init__(self, sender):
        self.sender = sender
        self.unified_msg_origin = "umo"

    def get_sender_id(self):
        return self.sender

    def is_stopped(self):
        return False

    def get_extra(self, *_args, **_kwargs):
        return None

    def set_extra(self, *_args, **_kwargs):
        return None

    def stop_event(self):
        return None

    def clear_result(self):
        return None


@pytest.mark.asyncio
async def test_direct_priority_wait_does_not_block_on_previous_direct():
    plugin = SimpleNamespace(
        closing=False,
        participation=SimpleNamespace(room_key=lambda _event: "room"),
    )
    priority = DirectPriority(plugin)
    first = Event("a")
    second = Event("b")

    async def hold_first():
        priority.watch(first, direct=True)
        await asyncio.sleep(60)

    task = asyncio.create_task(hold_first())
    await asyncio.sleep(0)
    started = time.monotonic()
    queued = await asyncio.wait_for(priority.wait(second), timeout=1)
    assert queued is True
    assert time.monotonic() - started < 1
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_context_reads_cancel_when_budget_is_spent():
    started = time.monotonic()
    timings = {}

    async def slow():
        await asyncio.sleep(5)
        return ["memory"]

    async with context_reads(timings, memory=slow()) as pending:
        await asyncio.sleep(0.01)
        for task in pending.values():
            task.cancel()
        results = await asyncio.gather(*pending.values(), return_exceptions=True)
    assert isinstance(results[0], asyncio.CancelledError)
    assert time.monotonic() - started < 1


@pytest.mark.asyncio
async def test_model_messages_skip_remote_image_redownload(monkeypatch):
    plugin = SimpleNamespace(settings={"planner_visual_enabled": True})
    context = ConversationContext(plugin)
    captured = {}

    class FakeRequest:
        def __init__(self, prompt, image_urls):
            captured["prompt"] = prompt
            captured["image_urls"] = image_urls

        async def assemble_context(self):
            return {"role": "user", "content": captured["prompt"]}

    monkeypatch.setattr("core.chat.conversation.ProviderRequest", FakeRequest)
    monkeypatch.setattr(
        "core.chat.conversation.Message.model_validate",
        lambda value: SimpleNamespace(**value, _no_save=False),
    )
    rows = [
        {
            "sequence": 1,
            "sender": "u",
            "sender_id": "1",
            "text": "看图",
            "self": False,
            "media": [
                {
                    "type": "image",
                    "url": "https://multimedia.nt.qq.com.cn/download",
                    "status": "ready",
                    "text": "蓝毛端盆",
                }
            ],
        }
    ]
    messages = await context.model_messages(rows)
    assert captured["image_urls"] == []
    assert "蓝毛端盆" in captured["prompt"]
    assert len(messages) == 1
