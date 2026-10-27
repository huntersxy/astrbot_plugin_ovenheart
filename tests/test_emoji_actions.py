"""Real SDK candidate-image tool results, text reply, media send and feedback."""

import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_conversation_planner import action, send_reply
from tests.test_image_memory import capture, picture
from tests.test_runtime import api_call, event

pytest_plugins = ["tests.test_conversation_planner"]


async def test_proactive_candidates_attach_without_an_extra_lookup_and_obey_controls(
    native_planner, monkeypatch
):
    p, provider = native_planner
    p.settings.update(
        native_memory_enabled=True,
        image_memory_enabled=True,
        emoji_enabled=True,
        emoji_frequency=1.0,
        emoji_cooldown=30,
        planner_visual_enabled=False,
        participation_frequency=0,
    )
    uploaded = await api_call(
        p.page_api.emoji_panel.action,
        {
            "scope": "library",
            "action": "emoji_upload",
            "payload": {
                "encoded": base64.b64encode(picture()).decode(),
                "text": "开心，庆祝好消息",
            },
        },
    )
    assert uploaded["status"] == "ok", uploaded
    incoming = event("happy-news", text="好消息，终于考过啦！")
    incoming.is_at_or_wake_command = True
    incoming.send = AsyncMock(return_value=True)
    sent = incoming.send
    await p.capture(incoming)
    sessions = []

    async def model(**kwargs):
        if not sessions:
            messages = json.loads(
                json.dumps(kwargs["contexts"], default=lambda value: value.model_dump())
            )
            content = messages[-1]["content"]
            prompt = json.loads(
                content
                if isinstance(content, str)
                else "".join(part.get("text", "") for part in content)
            )
            policy = prompt["emoji_policy"]
            assert policy["proactive_allowed"] and len(policy["candidates"]) == 1
            index = policy["candidates"][0]["index"]
            scope = (await p.resolve_scope(incoming))[0]
            session = p.participation.planner.sessions[scope]
            assert index in session.emojis
            sessions.append(session)
            return action(
                "reply",
                msg_id="1",
                reply_reference="庆祝对方考试通过。",
                attach_emoji=index,
            )
        return action("stop")

    provider.text_chat.side_effect = model
    replies = p.participate(incoming)
    await anext(replies)
    await send_reply(p, incoming, "过啦！恭喜恭喜")
    with pytest.raises(StopAsyncIteration):
        await anext(replies)
    sent.assert_awaited_once()
    assert provider.text_chat.await_count == 2
    session = sessions[0]
    cooling = await p.emoji_actions.proactive(session)
    assert not cooling["proactive_allowed"] and cooling["cooldown_remaining"] > 0
    # Explicit requests still use the real candidate/send path during cooldown.
    assert (await p.emoji_actions.show(session)).content
    index = next(reversed(session.emojis))
    assert (await p.emoji_actions.send(incoming, session, index))["sent"]
    p.settings.update(emoji_frequency=0.0, emoji_cooldown=0)
    assert not (await p.emoji_actions.proactive(session))["candidates"]
    p.settings["emoji_frequency"] = 0.5
    monkeypatch.setattr(
        "astrbot_plugin_ovenheart.memory.emoji_actions.random", lambda: 0.9
    )
    assert not (await p.emoji_actions.proactive(session))["proactive_allowed"]
    monkeypatch.setattr(
        "astrbot_plugin_ovenheart.memory.emoji_actions.random", lambda: 0.1
    )
    assert (await p.emoji_actions.proactive(session))["proactive_allowed"]


def test_emoji_controls_validate_ranges():
    from astrbot_plugin_ovenheart.core.settings import validate_settings

    assert (
        validate_settings({"emoji_frequency": 0.8, "emoji_cooldown": 10})[
            "emoji_frequency"
        ]
        == 0.8
    )
    for patch in (
        {"emoji_frequency": 1.1},
        {"emoji_frequency": True},
        {"emoji_cooldown": -1},
        {"emoji_cooldown": 1.5},
    ):
        with pytest.raises(ValueError):
            validate_settings(patch)


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
    assert all(part.type.value == "Image" for part in sent.await_args.args[0].chain)
    assert incoming.get_extra("ovenheart_planner_receipt")["media"]["sent"]
    p.context.llm_generate.assert_not_awaited()


@pytest.mark.parametrize("selected_index", [1, 999])
async def test_standalone_emoji_validates_visual_selection_before_sending(
    native_planner, selected_index,
):
    p, provider = native_planner
    await seed_emoji_library(p)
    p.context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(
            completion_text=json.dumps({"emoji_index": selected_index, "reason": "回应当前语气"})
        )
    )
    provider.text_chat.side_effect = [action("send_emoji"), action("stop")]
    incoming = event("pure-emoji", text="哈哈哈太棒了")
    incoming.is_at_or_wake_command = True
    sent = AsyncMock(return_value=True)
    incoming.send = sent
    assert not [request async for request in p.participate(incoming)]
    assert sent.await_count == (1 if selected_index == 1 else 0)
    assert p.context.llm_generate.await_count == 1
    selection_call = p.context.llm_generate.await_args.kwargs
    assert selection_call["chat_provider_id"] == "selector-vision"
    assert selection_call["image_urls"][0].startswith("data:image/png;base64,")
    assert "哈哈哈太棒了" in selection_call["prompt"]
    receipt = incoming.get_extra("ovenheart_planner_receipt")
    assert receipt["media_only"] and not receipt["reply_text"]
    assert receipt["media"]["sent"] is (selected_index == 1)
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
