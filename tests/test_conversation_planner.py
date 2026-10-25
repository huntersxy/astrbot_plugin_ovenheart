"""Exercise real SDK tool loops, suspended actions and native reply requests."""

import asyncio
import json
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from astrbot.core.provider.entities import LLMResponse
from astrbot.core.provider.provider import Provider
from astrbot.core.star.context import Context

from tests.test_runtime import event

pytest_plugins = ["tests.test_participation"]


def action(name, **arguments):
    return LLMResponse(
        role="assistant",
        tools_call_name=[name],
        tools_call_args=[arguments],
        tools_call_ids=[f"call-{name}-{len(str(arguments))}"],
    )


@pytest.fixture
def native_planner(participant):
    """Only the provider transport is replaced; SDK runner/executor are real."""
    participant.settings.update(
        planner_mode="maibot",
        participation_rhythm="maibot",
        participation_frequency=1.0,
        planner_timeout=3,
    )
    provider = MagicMock(spec=Provider)
    provider.provider_config = {}
    provider.text_chat = AsyncMock()
    native = Context.__new__(Context)
    native.provider_manager = SimpleNamespace(
        get_provider_by_id=AsyncMock(return_value=provider)
    )
    participant.context.tool_loop_agent = partial(Context.tool_loop_agent, native)
    participant.memory.planning_context = AsyncMock(return_value=([], "没有历史背景"))
    return participant, provider


async def send_reply(plugin, incoming, text):
    incoming.set_result(incoming.plain_result(text))
    await plugin.record_delivery(incoming)


async def test_native_memory_query_reply_receipt_and_stop(native_planner):
    plugin, provider = native_planner
    plugin.memory.recall = AsyncMock(
        return_value=(
            [{"id": "past", "content": "曾聊过毕业设计", "for_evidence_ids": [1]}],
            "获准访问的回忆",
        )
    )
    reference = "先接住对方的失落，再结合此前毕业设计话题追问。" * 15
    provider.text_chat.side_effect = [
        action("search_memory", query="毕业设计上次聊到哪里"),
        action("reply", msg_id="1", reply_reference=reference, reply_style="简短表达"),
        action("stop"),
    ]
    incoming = event("native-planner", text="今天做毕设好累啊")
    replies = plugin.participate(incoming)
    request = await anext(replies)
    assert provider.text_chat.await_count == 2
    assert incoming.get_result() is None
    assert (
        plugin.memory.recall.await_args.kwargs["search_query"] == "毕业设计上次聊到哪里"
    )
    await plugin.apply_participation(incoming, request)
    injected = "\n".join(part.text for part in request.extra_user_content_parts)
    assert reference in injected and "简短表达" in injected
    assert not request.func_tool or request.func_tool.empty()
    await send_reply(plugin, incoming, "歇会儿，你那毕设又卡哪儿了")
    with pytest.raises(StopAsyncIteration):
        await anext(replies)
    assert provider.text_chat.await_count == 3
    contexts = provider.text_chat.await_args.kwargs["contexts"]
    serialized = json.dumps(
        contexts, ensure_ascii=False, default=lambda m: m.model_dump()
    )
    assert "歇会儿，你那毕设又卡哪儿了" in serialized
    assert "native_send_hook" in serialized
    assert not plugin.participation.planner.sessions


async def test_reply_can_continue_to_another_real_target(native_planner):
    plugin, provider = native_planner
    # Keep both actual arrivals pending, then admit a single planner.
    plugin.settings["participation_frequency"] = 0
    older = event("older", text="我在找那个文件", sender="bob")
    assert not [req async for req in plugin.participate(older)]
    plugin.settings["participation_frequency"] = 1
    provider.text_chat.side_effect = [
        action("reply", msg_id="1", reply_reference="回应 bob 的文件问题。"),
        action("reply", msg_id="2", reply_reference="再接住 alice 另一个问题。"),
        action("stop"),
    ]
    incoming = event("newer", text="还有我这个怎么弄？")
    replies = plugin.participate(incoming)
    first = await anext(replies)
    assert incoming.get_extra("ovenheart_decision")["target"]["source"] == "older"
    await plugin.apply_participation(incoming, first)
    await send_reply(plugin, incoming, "bob，你先看下载目录")
    second = await anext(replies)
    assert incoming.get_extra("ovenheart_decision")["target"]["source"] == "newer"
    await plugin.apply_participation(incoming, second)
    await send_reply(plugin, incoming, "alice，你把报错贴一下")
    with pytest.raises(StopAsyncIteration):
        await anext(replies)
    serialized = json.dumps(
        provider.text_chat.await_args.kwargs["contexts"],
        ensure_ascii=False,
        default=lambda m: m.model_dump(),
    )
    assert "bob，你先看下载目录" in serialized
    assert "alice，你把报错贴一下" in serialized


async def test_wait_timeout_resumes_the_same_native_tool_loop(native_planner):
    plugin, provider = native_planner
    provider.text_chat.side_effect = [action("wait", seconds=1), action("stop")]
    incoming = event("waiting", text="让我想想怎么说")
    assert not [req async for req in plugin.participate(incoming)]
    assert provider.text_chat.await_count == 2
    assert "timeout" in json.dumps(
        provider.text_chat.await_args.kwargs["contexts"],
        default=lambda m: m.model_dump(),
    )
    assert incoming.get_result() is None


async def test_new_input_resumes_an_actual_wait_without_restarting(native_planner):
    plugin, provider = native_planner
    provider.text_chat.side_effect = [
        action("wait", seconds=30),
        action("reply", msg_id="2", reply_reference="现在补充完整，回应后来的信息。"),
        action("stop"),
    ]
    older = event("wait-for-more", text="我想说的是……")
    first = asyncio.create_task(anext(plugin.participate(older), None))
    for _ in range(100):
        if any(
            s.action and s.action["action"] == "wait"
            for s in plugin.participation.planner.sessions.values()
        ):
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("native wait was never reached")
    session = next(iter(plugin.participation.planner.sessions.values()))
    incoming = event("wait-completed", text="我明天要答辩，有点紧张")
    replies = plugin.participate(incoming)
    request = await anext(replies)
    assert await first is None
    assert next(iter(plugin.participation.planner.sessions.values())) is session
    serialized = json.dumps(
        provider.text_chat.await_args.kwargs["contexts"],
        ensure_ascii=False,
        default=lambda m: m.model_dump(),
    )
    assert "new_messages" in serialized and "我明天要答辩，有点紧张" in serialized
    await plugin.apply_participation(incoming, request)
    await send_reply(plugin, incoming, "紧张正常，先把开头顺一遍")
    with pytest.raises(StopAsyncIteration):
        await anext(replies)


async def test_no_send_hook_does_not_report_success_or_continue(native_planner):
    plugin, provider = native_planner
    provider.text_chat.side_effect = [
        action("reply", msg_id="1", reply_reference="简短接住。"),
        action("stop"),
    ]
    replies = plugin.participate(event("not-sent"))
    await anext(replies)
    with pytest.raises(StopAsyncIteration):
        await anext(replies)
    assert provider.text_chat.await_count == 1
    assert not plugin.participation.planner.sessions


async def test_new_direct_wake_revokes_the_planner_reply(native_planner):
    plugin, provider = native_planner
    provider.text_chat.side_effect = [
        action("reply", msg_id="1", reply_reference="接住旧话题。")
    ]
    incoming = event("stale")
    replies = plugin.participate(incoming)
    await anext(replies)
    direct = event("direct", text="先帮我看新的问题")
    direct.is_at_or_wake_command = True
    await plugin.prioritize_direct(direct)
    assert not await plugin.check_participation(incoming)
    with pytest.raises(StopAsyncIteration):
        await anext(replies)
    assert provider.text_chat.await_count == 1


async def test_repeated_reply_is_rejected_inside_the_native_loop(native_planner):
    plugin, provider = native_planner
    provider.text_chat.side_effect = [
        action("reply", msg_id="1", reply_reference="接住这一句。"),
        action("reply", msg_id="1", reply_reference="又回答同一句。"),
        action("stop"),
    ]
    incoming = event("once")
    replies = plugin.participate(incoming)
    await anext(replies)
    await send_reply(plugin, incoming, "知道啦")
    with pytest.raises(StopAsyncIteration):
        await anext(replies)
    assert provider.text_chat.await_count == 3
    last = str(provider.text_chat.await_args.kwargs["contexts"])
    assert "已回复" in last


async def test_continuation_still_reserves_the_hourly_quota(native_planner):
    plugin, provider = native_planner
    plugin.settings.update(participation_hourly_limit=1, participation_frequency=0)
    assert not [
        req async for req in plugin.participate(event("quota-old", sender="bob"))
    ]
    plugin.settings["participation_frequency"] = 1
    provider.text_chat.side_effect = [
        action("reply", msg_id="1", reply_reference="回应第一人。"),
        action("reply", msg_id="2", reply_reference="回应第二人。"),
    ]
    incoming = event("quota-new")
    replies = plugin.participate(incoming)
    await anext(replies)
    await send_reply(plugin, incoming, "第一个问题是这样")
    with pytest.raises(StopAsyncIteration):
        await anext(replies)
    state = await plugin.store.call("state", (await plugin.resolve_scope(incoming))[0])
    assert state["decisions"][0]["outcome"] == "cancelled"
    assert "上限" in state["decisions"][0]["reason"]


async def test_observation_mode_never_dispatches_the_reply(native_planner):
    plugin, provider = native_planner
    plugin.settings["participation_mode"] = "observe"
    provider.text_chat.return_value = action(
        "reply", msg_id="1", reply_reference="只记录计划。"
    )
    incoming = event("observe")
    assert not [req async for req in plugin.participate(incoming)]
    assert incoming.get_result() is None
    assert provider.text_chat.await_count == 1
    assert not plugin.participation.planner.sessions


async def test_profile_revoked_during_memory_query_stops_before_reply(native_planner):
    plugin, provider = native_planner

    async def recall(*args, **kwargs):
        plugin.settings_revision = "changed-during-query"
        return [], "已改变设置"

    plugin.memory.recall = recall
    provider.text_chat.side_effect = [action("search_memory", query="过去的话题")]
    with pytest.raises(asyncio.CancelledError):
        assert not [req async for req in plugin.participate(event("revoked-memory"))]
    assert provider.text_chat.await_count == 1
    assert not plugin.participation.planner.sessions


async def test_revocation_after_send_keeps_the_consumed_quota(native_planner):
    plugin, provider = native_planner
    provider.text_chat.return_value = action(
        "reply", msg_id="1", reply_reference="先回复这一句。"
    )
    incoming = event("sent-before-revocation")
    replies = plugin.participate(incoming)
    await anext(replies)
    await send_reply(plugin, incoming, "这条已经发送")
    plugin.settings["apply_enabled"] = False
    try:
        await anext(replies)
    except (StopAsyncIteration, asyncio.CancelledError):
        pass
    else:
        pytest.fail("revoked planner dispatched another reply")
    state = await plugin.store.call("state", (await plugin.resolve_scope(incoming))[0])
    assert state["decisions"][0]["outcome"] == "admitted"
    assert provider.text_chat.await_count == 1
