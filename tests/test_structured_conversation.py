"""Exercise the owned transcript with real SDK events and SQLite persistence."""

import asyncio
import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.api.provider import ProviderRequest
from astrbot.builtin_stars.astrbot.main import Main
from astrbot.core.db.po import Conversation
from astrbot.core.message.components import At, Image, Plain, Reply
from astrbot.core.provider.entities import LLMResponse

from astrbot_plugin_ovenheart.chat.participation import (
    ParticipationFormatError,
    parse_plan,
    planner_message,
)
from tests.test_participation import decision, requests
from tests.test_runtime import event

pytest_plugins = ["tests.test_participation"]


@pytest.fixture
async def structured(participant):
    participant.settings.update(
        context_mode="structured", learning_enabled=False, participation_mode="off"
    )
    participant.context.conversation_manager.get_curr_conversation_id.return_value = (
        "dialog-a"
    )
    participant.context.conversation_manager.get_conversation.return_value = (
        Conversation(
            platform_id="test",
            user_id="test",
            cid="dialog-a",
            history="[]",
            persona_id="default",
        )
    )
    return participant


def incoming(key, text, **kwargs):
    message = event(key, text=text, **kwargs)
    message.message_obj.message = [Plain(text)]
    return message


async def test_bare_at_sees_earlier_question_despite_other_members(structured):
    plugin = structured
    first = incoming("question", "汐兮雨是谁", sender="alice", sender_name="阿力")
    other = incoming("other", "妈妈是你的妈妈呀", sender="peer")
    await plugin.capture(first)
    await plugin.capture(other)
    wake = incoming("at", "", sender="alice", sender_name="阿力")
    wake.message_obj.message = [At(qq="bot")]
    wake.is_at_or_wake_command = True
    native = Main.handle_empty_mention(SimpleNamespace(context=plugin.context), wake)
    try:
        request = await anext(native)
    finally:
        await native.aclose()
    request.contexts = [{"role": "assistant", "content": "原生会话保留"}]
    await plugin.coordinate_direct_request(wake, request)
    await plugin.apply_persona(wake, request)
    await plugin.apply_participation(wake, request)
    context = wake.get_extra("ovenheart_direct_context")
    assert [m["message_id"] for m in context["messages"]] == ["question", "other", "at"]
    assert context["messages"][0]["sender_id"] == "alice"
    assert context["reply_message"]["sender_id"] == "alice"
    assert "友好地询问用户想要聊些什么" not in request.prompt
    assert request.contexts == [{"role": "assistant", "content": "原生会话保留"}]
    assert "汐兮雨是谁" in json.dumps(
        await request.assemble_context(), ensure_ascii=False
    )
    assert request.extra_user_content_parts[-1].model_dump_for_context()["_no_save"]


async def test_snapshot_excludes_future_and_is_immutable(structured):
    scope = (await structured.resolve_scope(incoming("lookup", "")))[0]
    current = incoming("current", "QUESTION")
    assert await structured.conversation.prepare(current, scope, ProviderRequest())
    before = json.dumps(current.get_extra("ovenheart_context_snapshot"))
    await structured.conversation.observe(
        incoming("later", "LATER_TOPIC", sender="peer"), scope
    )
    assert await structured.conversation.prepare(current, scope, ProviderRequest())
    assert json.dumps(current.get_extra("ovenheart_context_snapshot")) == before
    assert "LATER_TOPIC" not in before


async def test_snapshot_waits_for_earlier_registered_input(structured):
    old = incoming("old", "EARLIER_INPUT")
    current = incoming("current", "CURRENT_INPUT")
    structured.conversation.reserve(old)
    scope = (await structured.resolve_scope(current))[0]
    task = asyncio.create_task(
        structured.conversation.prepare(current, scope, ProviderRequest())
    )
    await asyncio.sleep(0.01)
    assert not task.done()
    await structured.observe_input(old)
    assert old.get_extra("ovenheart_context_binding")
    assert await task
    assert old.get_extra("ovenheart_context_binding")[0] == scope
    assert old.created_at <= current.created_at, (old.created_at, current.created_at)
    assert [
        m["message_id"]
        for m in current.get_extra("ovenheart_context_snapshot")["messages"]
    ] == ["old", "current"]


async def test_restore_does_not_replay_old_inputs_as_fresh(structured):
    first = incoming("old", "OLD_QUESTION")
    scope = (await structured.resolve_scope(first))[0]
    await structured.conversation.observe(first, scope)
    structured.participation.close()
    window = await structured.conversation.observe(incoming("new", "NEW_INPUT"), scope)
    assert [m["message_id"] for m in window["messages"]] == ["old", "new"]
    assert window["consumed"] == 1 and window["sequence"] == 2


async def test_frozen_request_is_invalidated_before_any_new_dialog_input(structured):
    old = incoming("old", "OLD_INPUT")
    scope = (await structured.resolve_scope(old))[0]
    assert await structured.conversation.prepare(old, scope, ProviderRequest())
    structured.context.conversation_manager.get_curr_conversation_id.return_value = (
        "dialog-b"
    )
    assert not await structured.conversation.prepare(old, scope, ProviderRequest())


@pytest.mark.parametrize("reset", ["cid", "history"])
async def test_new_or_cleared_dialog_isolates_old_history(structured, reset):
    native = structured.context.conversation_manager.get_conversation.return_value
    native.history = '[{"role":"assistant","content":"previous"}]'
    old = incoming("old", "OLD_PRIVATE_TOPIC")
    scope = (await structured.resolve_scope(old))[0]
    await structured.conversation.prepare(old, scope, ProviderRequest())
    epoch = old.get_extra("ovenheart_context_snapshot")["epoch"]
    if reset == "cid":
        structured.context.conversation_manager.get_curr_conversation_id.return_value = "dialog-b"
        native.cid = "dialog-b"
    else:
        native.history = "[]"
    current = incoming("new", "NEW_TOPIC")
    await structured.conversation.prepare(current, scope, ProviderRequest())
    frozen = current.get_extra("ovenheart_context_snapshot")
    assert frozen["epoch"] != epoch and "OLD_PRIVATE_TOPIC" not in json.dumps(frozen)
    assert not await structured.conversation.prepare(old, scope, ProviderRequest())


async def test_pending_media_updates_slot_not_frozen_request(structured):
    ready, release = asyncio.Event(), asyncio.Event()

    async def describe(**kwargs):
        ready.set()
        await release.wait()
        return SimpleNamespace(completion_text="A_BLUE_CAT")

    cfg = {
        "plugin_set": ["*"],
        "provider_ltm_settings": {
            "image_caption": True,
            "image_caption_provider_id": "vision",
        },
    }
    structured.context.get_config = lambda umo: cfg
    structured.context.get_provider_by_id = lambda name: SimpleNamespace(
        text_chat=describe
    )
    image = incoming("image", "[图片]")
    image.message_obj.message = [Image.fromURL("https://example.invalid/image.png")]
    scope = (await structured.resolve_scope(image))[0]
    await structured.conversation.observe(image, scope)
    await asyncio.wait_for(ready.wait(), 1)
    first = incoming("first", "这张图是什么")
    await structured.conversation.prepare(first, scope, ProviderRequest())
    frozen = json.dumps(first.get_extra("ovenheart_context_snapshot"))
    release.set()
    for _ in range(50):
        if not structured.conversation.pending:
            break
        await asyncio.sleep(0.01)
    second = incoming("second", "接着说")
    await structured.conversation.prepare(second, scope, ProviderRequest())
    assert "A_BLUE_CAT" not in frozen
    assert "A_BLUE_CAT" in json.dumps(second.get_extra("ovenheart_context_snapshot"))
    assert json.dumps(first.get_extra("ovenheart_context_snapshot")) == frozen
    assert (
        second.get_extra("ovenheart_context_snapshot")["messages"][0]["message_id"]
        == "image"
    )


async def test_planner_and_reply_share_snapshot_and_real_earlier_target(structured):
    structured.settings.update(
        participation_mode="auto",
        participation_min_messages=1,
        participation_cooldown=0,
    )
    await structured.capture(incoming("earlier", "EARLIER_QUESTION", sender="alice"))
    structured.context.llm_generate.return_value = decision(target_sequence=1)
    trigger = incoming("trigger", "OTHER_TOPIC", sender="bob")
    yielded = await requests(structured, trigger)
    assert len(yielded) == 1
    plan = trigger.get_extra("ovenheart_decision")
    planner = json.loads(structured.context.llm_generate.await_args.kwargs["prompt"])[
        "recent_messages"
    ]
    assert [planner_message(m) for m in plan["messages"]] == planner
    await structured.apply_persona(trigger, yielded[0])
    await structured.apply_participation(trigger, yielded[0])
    assert trigger.get_extra("ovenheart_context_messages") == plan["messages"]
    assert (
        plan["target"]["message_id"] == "earlier" and trigger.get_sender_id() == "bob"
    )


async def test_bootstrap_platform_history_retains_unreplyable_identity(structured):
    row = SimpleNamespace(
        id=123,
        created_at=datetime.fromtimestamp(time.time() - 10, timezone.utc),
        sender_id="peer",
        sender_name="群友",
        content={
            "type": "user",
            "message": [{"type": "plain", "text": "BOOTSTRAP_QUESTION"}],
        },
    )
    structured.context.message_history_manager = SimpleNamespace(
        get=AsyncMock(return_value=[row])
    )
    wake = incoming("current", "WAKE")
    scope = (await structured.resolve_scope(wake))[0]
    await structured.conversation.prepare(wake, scope, ProviderRequest())
    view = wake.get_extra("ovenheart_context_snapshot")["messages"]
    assert view[0]["text"] == "BOOTSTRAP_QUESTION" and not view[0]["replyable"]
    with pytest.raises(ParticipationFormatError):
        parse_plan(
            decision(target_sequence=1).completion_text,
            structured.settings,
            messages=view,
        )


async def test_host_context_enabled_prevents_duplicate_owned_injection(structured):
    structured.context.get_config = lambda umo: {
        "provider_ltm_settings": {"group_icl_enable": True}
    }
    assert not structured.conversation.active(incoming("native", "text"))


async def test_explicit_native_reset_clears_even_without_prior_native_reply(structured):
    previous = incoming("previous", "CLEAR_ME")
    await structured.capture(previous)
    reset = incoming("reset", "/reset")
    reset.set_extra("_clean_group_context_session", True)
    await structured.record_delivery(reset)
    current = incoming("current", "NEW_CONTEXT")
    scope = (await structured.resolve_scope(current))[0]
    await structured.conversation.prepare(current, scope, ProviderRequest())
    assert "CLEAR_ME" not in json.dumps(current.get_extra("ovenheart_context_snapshot"))


async def test_bot_reply_is_retained_when_autonomous_participation_is_off(structured):
    current = incoming("current", "QUESTION")
    await structured.capture(current)
    current.set_result(current.plain_result("ANSWER"))
    await structured.record_delivery(current)
    next_turn = incoming("next", "FOLLOWUP")
    scope = (await structured.resolve_scope(next_turn))[0]
    await structured.conversation.prepare(next_turn, scope, ProviderRequest())
    history = next_turn.get_extra("ovenheart_context_snapshot")["messages"]
    assert any(
        m["self"] and m["text"] == "ANSWER" and m["reply_to"]["message_id"] == "current"
        for m in history
    )


async def test_quote_and_budget_preserve_identity(structured):
    first = incoming("first", "LONG" * 1000)
    first.message_obj.message.append(
        Reply(id="quoted", sender_id="peer", message_str="QUOTE")
    )
    scope = (await structured.resolve_scope(first))[0]
    await structured.conversation.observe(first, scope)
    for i in range(50):
        await structured.conversation.observe(
            incoming(str(i), str(i) + "x" * 1000), scope
        )
    current = incoming("final", "END")
    await structured.conversation.prepare(current, scope, ProviderRequest())
    frozen = current.get_extra("ovenheart_context_snapshot")["messages"]
    assert len(frozen) <= 40 and len(json.dumps(frozen, ensure_ascii=False)) <= 24000
    assert frozen[-1]["message_id"] == "final"
    stored = await structured.store.call("chat_context", scope, "dialog-a")
    assert stored["messages"][0]["reply_to"]["message_id"] == "quoted"


async def test_own_speech_and_same_sender_survive_busy_room_window(structured):
    plugin = structured
    first = incoming("first", "先前的问题", sender="alice")
    scope = (await plugin.resolve_scope(first))[0]
    await plugin.capture(first)
    await plugin.conversation.delivered(
        first, scope, None, "我这条插座出厂就是温柔款的", True
    )
    for i in range(45):
        await plugin.capture(
            incoming(
                f"peer-{i}", "别人的闲聊", sender="peer", sender_name="机器人（自己）"
            )
        )
    wake = incoming("followup", "你怎么是条了", sender="alice")
    wake.is_at_or_wake_command = True
    request = ProviderRequest(prompt=wake.message_str)
    await plugin.coordinate_direct_request(wake, request)
    await plugin.apply_persona(wake, request)
    await plugin.apply_participation(wake, request)
    focus = wake.get_extra("ovenheart_reply_focus")
    assert focus["recent_self"][-1]["text"] == "我这条插座出厂就是温柔款的"
    assert focus["same_sender_before"][-1]["message_id"] == "first"
    assert all(m["sender_id"] != "peer" for m in focus["recent_self"])
    assert "first" not in [
        m["message_id"]
        for m in wake.get_extra("ovenheart_context_snapshot")["messages"]
    ]
    assert "我这条插座出厂就是温柔款的" in json.dumps(
        await request.assemble_context(), ensure_ascii=False
    )
    assert request.extra_user_content_parts[-1].model_dump_for_context()["_no_save"]
    before = json.dumps(focus)
    await plugin.capture(incoming("future", "未来的话题"))
    assert plugin.direct_reply_context(wake, scope)["reply_focus"] is focus
    assert json.dumps(focus) == before


@pytest.mark.parametrize("has_message_id", [True, False])
async def test_already_answered_message_is_not_an_autonomous_target(structured, has_message_id):
    original = incoming("original", "QUESTION")
    if not has_message_id:
        original.message_obj.message_id = ""
    source = structured.conversation.record(original)["source"]
    scope = (await structured.resolve_scope(original))[0]
    await structured.capture(original)
    await structured.conversation.delivered(
        original, scope, None, "", False, media=[{"type": "image", "status": "ready"}]
    )
    after_image = structured.conversation.snapshot(
        original, scope, through_sequence=structured.participation.windows[scope]["sequence"]
    )
    assert next(m for m in after_image if m["source"] == source)["replyable"]
    await structured.conversation.delivered(original, scope, None, "ANSWER", True)
    next_turn = incoming("next", "NEW_TOPIC", sender="peer")
    if not has_message_id:
        next_turn.message_obj.message_id = ""
    await structured.conversation.prepare(next_turn, scope, ProviderRequest())
    messages = next_turn.get_extra("ovenheart_context_snapshot")["messages"]
    answered = next(m for m in messages if m["source"] == source)
    assert not answered["replyable"]
    assert next(m for m in messages if m["source"] == structured.conversation.record(next_turn)["source"])["replyable"]
    with pytest.raises(ParticipationFormatError):
        parse_plan(
            decision(target_sequence=answered["sequence"]).completion_text,
            structured.settings,
            messages=messages,
        )


async def test_autonomous_planning_waits_for_live_direct_task(structured):
    plugin = structured
    plugin.settings.update(participation_mode="auto", participation_cooldown=0)
    direct = incoming("direct", "主持辩论，每轮等评判再继续")
    direct.is_at_or_wake_command = True
    entered, release = asyncio.Event(), asyncio.Event()

    async def working():
        await plugin.coordinate_direct_request(
            direct, ProviderRequest(prompt=direct.message_str)
        )
        entered.set()
        await release.wait()

    task = asyncio.create_task(working())
    await asyncio.wait_for(entered.wait(), 1)
    try:
        assert (
            await requests(plugin, incoming("chatter", "烤箱快来定规则", sender="peer"))
            == []
        )
        plugin.context.llm_generate.assert_not_awaited()
    finally:
        release.set()
        await task
    # A finished task cannot keep the room blocked.
    assert len(await requests(plugin, incoming("fresh", "新问题", sender="peer"))) == 1


async def test_optional_selection_has_hard_deadline_even_if_cancellation_is_slow(
    structured, monkeypatch
):
    release, cancelled = asyncio.Event(), asyncio.Event()
    workers = []

    async def slow(*args, **kwargs):
        workers.append(asyncio.current_task())
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        return {"traits": [], "mode": "late", "message": "late"}

    monkeypatch.setattr(structured.retriever, "select", slow)
    structured.settings.update(retrieval_timeout=0.01, selector_timeout=0.01)
    wake = incoming("wake", "真正要处理的任务")
    request = ProviderRequest(prompt=wake.message_str)
    try:
        await asyncio.wait_for(structured.apply_persona(wake, request), 1)
        await asyncio.wait_for(cancelled.wait(), 1)
        assert not wake.is_stopped() and request.prompt == "真正要处理的任务"
        assert wake.get_extra("ovenheart_trace_metrics")["retrieval_mode"] == "deadline"
    finally:
        release.set()
        await asyncio.gather(*workers, return_exceptions=True)


async def test_empty_generation_is_visible_in_audit_without_repeating_tools(structured):
    wake = incoming("direct", "帮我主持辩论")
    wake.is_at_or_wake_command = True
    request = ProviderRequest(prompt=wake.message_str)
    await structured.apply_persona(wake, request)
    await structured.apply_participation(wake, request)
    scope = (await structured.resolve_scope(wake))[0]
    run = SimpleNamespace(
        messages=[SimpleNamespace(role="user"), SimpleNamespace(role="assistant")]
    )
    await structured.record_generation_outcome(
        wake, run, LLMResponse(role="assistant", reasoning_content="private reasoning")
    )
    trace = (await structured.store.call("state", scope))["turn_traces"][0]
    assert trace["status"] == "empty_response"
    assert "private reasoning" not in json.dumps(trace)
    structured.context.llm_generate.assert_not_awaited()
    # A send-stage observation may supersede an empty diagnostic, not a cancellation.
    wake.set_result(wake.plain_result("实际回复"))
    await structured.record_delivery(wake)
    assert (await structured.store.call("state", scope))["turn_traces"][0][
        "status"
    ] == "send_hook"


@pytest.mark.parametrize("autonomous", [False, True])
@pytest.mark.parametrize("limit", [0, 3])
async def test_reply_window_obeys_settings_without_trimming_planner_or_native_history(
    structured, autonomous, limit
):
    from astrbot_plugin_ovenheart.chat.reply_payload import dumps

    plugin = structured
    plugin.settings.update(reply_context_messages=limit, reply_context_budget=1000)
    for index in range(18):
        await plugin.capture(incoming(str(index), f"背景消息{index}", sender="peer"))
    wake = incoming("current", "真实任务", sender="alice")
    if autonomous:
        plugin.settings.update(participation_mode="auto", participation_min_messages=1)
        request = (await requests(plugin, wake))[0]
        assert len(wake.get_extra("ovenheart_decision")["messages"]) > 3
    else:
        wake.is_at_or_wake_command = True
        request = ProviderRequest(prompt=wake.message_str)
        await plugin.coordinate_direct_request(wake, request)
    request.contexts = [{"role": "assistant", "content": "完整原生历史"}]
    original_tools = request.func_tool
    await plugin.apply_persona(wake, request)
    await plugin.apply_participation(wake, request)
    temporary = request.extra_user_content_parts[-1]
    marker = '{"reply_goal"' if autonomous else '{"scope"'
    payload = json.JSONDecoder().raw_decode(
        temporary.text[temporary.text.index(marker) :]
    )[0]
    assert len(payload["messages"]) == limit and len(dumps(payload["messages"])) <= 1000
    assert payload["reply_message"]["sender_id"] == "alice"
    assert payload["reply_message"]["message_id"] == "current"
    assert all("arrival_order" not in row for row in payload["messages"])
    assert request.contexts == [{"role": "assistant", "content": "完整原生历史"}]
    if not autonomous:
        assert request.func_tool is original_tools
    assert temporary.model_dump_for_context()["_no_save"]
    if not autonomous:
        assert len(wake.get_extra("ovenheart_context_snapshot")["messages"]) > 3
    else:
        assert len(wake.get_extra("ovenheart_decision")["messages"]) > 3
    trace = (await plugin.store.call("state", (await plugin.resolve_scope(wake))[0]))[
        "turn_traces"
    ][0]
    assert trace["details"]["context_limit"] == limit
    assert trace["details"]["context_budget"] == 1000
