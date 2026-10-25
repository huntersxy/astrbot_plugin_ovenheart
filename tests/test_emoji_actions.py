"""Real SDK candidate-image tool results, text reply, media send and feedback."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_conversation_planner import action, send_reply
from tests.test_image_memory import capture, picture
from tests.test_runtime import event

pytest_plugins = ["tests.test_conversation_planner"]


@pytest.mark.parametrize("failed", [False, True])
async def test_original_candidate_grid_selected_attachment_and_real_send_result(
    native_planner, failed
):
    p, provider = native_planner
    p.settings.update(
        native_memory_enabled=True,
        image_memory_enabled=True,
        emoji_enabled=True,
        planner_visual_enabled=True,
        participation_frequency=0,
    )
    vision = SimpleNamespace(
        text_chat=AsyncMock(
            return_value=SimpleNamespace(
                completion_text='{"description":"绿色猫猫开心表情包","is_emoji":true}'
            )
        )
    )
    p.context.get_provider_by_id = lambda _: vision
    p.context.get_config = lambda _: {
        "plugin_set": ["*"],
        "platform_settings": {},
        "wake_prefix": [],
        "provider_ltm_settings": {
            "image_caption": True,
            "image_caption_provider_id": "vision",
        },
    }
    _, scope, _, _ = await capture(p, "incoming-emoji", picture(), "开心猫猫")
    incoming = event("attach-request", text="这个结果真让人开心")
    incoming.is_at_or_wake_command = True
    incoming.send = AsyncMock(
        side_effect=RuntimeError("adapter failed") if failed else None,
        return_value=True,
    )
    original_send = incoming.send
    calls = 0

    async def model(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return action("show_emoji_list")
        if calls == 2:
            session = p.participation.planner.sessions[scope]
            index = next(iter(session.emojis))
            encoded = json.dumps(
                kwargs["contexts"],
                default=lambda value: value.model_dump(),
                ensure_ascii=False,
            )
            assert "data:image/png;base64" in encoded
            return action(
                "reply",
                msg_id="1",
                reply_reference="和对方分享开心，配上刚查看的开心猫猫。",
                attach_emoji=index,
            )
        return action("stop")

    provider.text_chat.side_effect = model
    replies = p.participate(incoming)
    request = await anext(replies)
    await p.apply_persona(incoming, request)
    await p.apply_participation(incoming, request)
    await send_reply(p, incoming, "这下终于搞定啦")
    with pytest.raises(StopAsyncIteration):
        await anext(replies)
    assert original_send.await_count == 1
    assert original_send.await_args.args[0].chain[0].type.value == "Image"
    assert provider.text_chat.await_count == 3
    receipt = incoming.get_extra("ovenheart_planner_receipt")
    assert receipt["media"]["sent"] is not failed
    own_images = [
        row
        for row in p.participation.windows[scope]["messages"]
        if row.get("self") and row.get("media")
    ]
    assert bool(own_images) is not failed
    if own_images:
        assert own_images[-1]["media"][0]["text"] == "绿色猫猫开心表情包"
        await asyncio.gather(
            *(
                task
                for task in tuple(p.tasks)
                if task.get_coro().__qualname__.endswith("ImageMemory.sent")
            )
        )
        candidates = await p.room_memory.call(scope, "image_candidates", event=incoming)
        assert any(
            occurrence["source_kind"] == "bot"
            for item in candidates
            for occurrence in item["occurrences"]
        )
    assert "这下终于搞定啦" in str(provider.text_chat.await_args.kwargs["contexts"])
