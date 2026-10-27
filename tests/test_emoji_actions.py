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
    reply_context = "\n".join(part.text for part in request.extra_user_content_parts)
    assert '"planned_emoji"' in reply_context
    assert "绿色猫猫开心表情包" in reply_context
    assert '"pending"' in reply_context
    assert '"reply_capabilities"' in reply_context
    assert "正文工具列表只覆盖当前文字生成阶段" in reply_context
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


async def seed_emoji_library(p):
    p.settings.update(
        native_memory_enabled=True,
        image_memory_enabled=True,
        emoji_enabled=True,
        emoji_provider_id="selector-vision",
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
    await capture(p, "emoji-source", picture(), "开心猫猫")


async def test_direct_selected_emoji_needs_no_text_or_visual_subagent(native_planner):
    p, provider = native_planner
    await seed_emoji_library(p)
    p.context.llm_generate = AsyncMock(
        side_effect=AssertionError("unexpected extra model")
    )
    calls = 0

    async def model(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return action("show_emoji_list")
        if calls == 2:
            session = next(iter(p.participation.planner.sessions.values()))
            return action("send_emoji", index=next(iter(session.emojis)))
        return action("stop")

    provider.text_chat.side_effect = model
    incoming = event("image-only-request", text="发个表情给我")
    incoming.is_at_or_wake_command = True
    sent = AsyncMock(return_value=True)
    incoming.send = sent
    assert not [request async for request in p.participate(incoming)]
    sent.assert_awaited_once()
    assert all(
        part.type.value == "Image" for part in sent.await_args.args[0].chain
    )
    assert incoming.get_extra("ovenheart_planner_receipt")["media"]["sent"]
    p.context.llm_generate.assert_not_awaited()


async def test_standalone_emoji_uses_original_visual_selector_and_returns_media_only_receipt(
    native_planner,
):
    p, provider = native_planner
    await seed_emoji_library(p)
    p.context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(
            completion_text='{"emoji_index":999,"reason":"回应开心的语气"}'
        )
    )
    provider.text_chat.side_effect = [action("send_emoji"), action("stop")]
    incoming = event("pure-emoji", text="哈哈哈太棒了")
    incoming.is_at_or_wake_command = True
    sent = AsyncMock(return_value=True)
    incoming.send = sent
    assert not [request async for request in p.participate(incoming)]
    assert sent.await_count == 1
    assert p.context.llm_generate.await_count == 1
    selection_call = p.context.llm_generate.await_args.kwargs
    assert selection_call["chat_provider_id"] == "selector-vision"
    assert selection_call["image_urls"][0].startswith("data:image/png;base64,")
    assert "哈哈哈太棒了" in selection_call["prompt"]
    receipt = incoming.get_extra("ovenheart_planner_receipt")
    assert receipt["send_hook"] and receipt["media_only"] and not receipt["reply_text"]
    assert receipt["media"]["selection"]["fallback_to_first"]
    assert "media_only" in str(provider.text_chat.await_args.kwargs["contexts"])
    await p.auxiliary.flush()
    assert (
        next(
            row
            for row in await p.store.call("auxiliary_usage")
            if row["stage"] == "emoji_select"
        )["calls"]
        == 1
    )


async def test_failed_standalone_selection_returns_to_text_request_despite_group_zero_frequency(
    native_planner,
):
    p, provider = native_planner
    await seed_emoji_library(p)
    p.context.llm_generate = AsyncMock(side_effect=RuntimeError("selector unavailable"))
    provider.text_chat.side_effect = [
        action("send_emoji"),
        action(
            "reply",
            msg_id="1",
            reply_reference="表情失败不影响帮用户完成当前任务，继续文字说明。",
        ),
        action("stop"),
    ]
    incoming = event("pure-emoji-failure", text="先给个表情，然后解释一下这个结果")
    incoming.is_at_or_wake_command = True
    sent = AsyncMock(return_value=True)
    incoming.send = sent
    replies = p.participate(incoming)
    request = await anext(replies)
    assert incoming.get_extra("ovenheart_decision")["direct"]
    assert not incoming.get_extra("ovenheart_decision")["media_only"]
    await p.apply_persona(incoming, request)
    await p.apply_participation(incoming, request)
    await send_reply(p, incoming, "这个结果说明任务已经完成。")
    with pytest.raises(StopAsyncIteration):
        await anext(replies)
    assert sent.await_count == 0
    assert provider.text_chat.await_count == 3
    assert "表情选择模型请求失败" in str(
        provider.text_chat.await_args.kwargs["contexts"]
    )
