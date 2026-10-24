"""Learning-only group exclusions preserve chat, old data and commit guards."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from astrbot.api.provider import ProviderRequest

from astrbot_plugin_ovenheart.settings import (
    DEFAULTS,
    learning_blocked,
    validate_settings,
)
from tests.test_runtime import api_call, completion, event, sources
from tests.test_structured_conversation import incoming

pytest_plugins = ["tests.test_structured_conversation"]


def test_group_id_and_full_session_matching_do_not_match_private_chats():
    settings = DEFAULTS | {"learning_blocked_groups": ["123", "bot:GroupMessage:456"]}
    assert learning_blocked("qq:GroupMessage:123", settings)
    assert learning_blocked("bot:GroupMessage:456", settings)
    assert not learning_blocked("other:GroupMessage:456", settings)
    assert not learning_blocked("qq:FriendMessage:123", settings)
    assert validate_settings({"learning_blocked_groups": [" 123 ", "123"]})[
        "learning_blocked_groups"
    ] == ["123"]


async def test_capture_and_manual_learning_stop_but_approved_guidance_remains(plugin):
    scope = await sources(plugin)
    assert (await plugin.learn(scope))["status"] == "success"
    await plugin.store.call("mutate", scope, "profile", {"identity": "保留的底稿"})
    await plugin.capture(event("pending-before-block"))
    before = await plugin.store.call("state", scope)
    await api_call(
        plugin.page_api.action,
        {"action": "settings", "payload": {"learning_blocked_groups": ["123"]}},
    )
    plugin.context.llm_generate.reset_mock()
    await plugin.capture(event("must-not-be-learned", "不应进入学习库"))
    result = await plugin.learn(scope)
    assert result["status"] == "error" and "学习黑名单" in result["message"]
    plugin.context.llm_generate.assert_not_awaited()
    after = await plugin.store.call("state", scope)
    assert before["scope"]["pending"] == after["scope"]["pending"]
    assert len(after["traits"]) == len(before["traits"])
    request = ProviderRequest(prompt="继续聊天", system_prompt="宿主人格")
    assert await plugin.permitted(event("reply"))
    await plugin.apply_persona(event("reply"), request)
    assert "保留的底稿" in request.system_prompt


async def test_store_guard_prevents_late_capture_and_atomic_commit(plugin):
    scope = await sources(plugin)
    batch = await plugin.store.call("batch", scope, 30)
    run = await plugin.store.call("start_run", scope)
    await plugin.store.call("settings", {"learning_blocked_groups": ["123"]})
    assert not await plugin.store.call(
        "capture",
        scope,
        "qq:GroupMessage:123",
        "default",
        "群聊",
        "late",
        "u",
        "不保存",
        DEFAULTS,
    )
    with pytest.raises(ValueError, match="学习黑名单"):
        await plugin.store.call("finish_run", run, scope, batch, [], DEFAULTS, "")
    assert len(await plugin.store.call("batch", scope, 30)) == len(batch)


async def test_inflight_learning_cannot_commit_after_group_is_blocked(plugin):
    scope = await sources(plugin)
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(**kwargs):
        entered.set()
        await release.wait()
        return await completion(**kwargs)

    plugin.context.llm_generate = AsyncMock(side_effect=delayed)
    task = asyncio.create_task(plugin.learn(scope))
    await entered.wait()
    await api_call(
        plugin.page_api.action,
        {"action": "settings", "payload": {"learning_blocked_groups": ["123"]}},
    )
    release.set()
    assert (await task)["status"] == "error"
    state = await plugin.store.call("state", scope)
    assert not state["traits"] and state["scope"]["pending"] == 2


async def test_learning_block_does_not_hide_group_history_from_replies(structured):
    structured.settings.update(learning_enabled=True, learning_blocked_groups=["123"])
    first = incoming("blocked-history", "阿力刚才提出的问题", sender="alice")
    await structured.capture(first)
    wake = incoming("blocked-wake", "请继续", sender="alice")
    wake.is_at_or_wake_command = True
    request = ProviderRequest(prompt="请继续")
    await structured.apply_persona(wake, request)
    await structured.apply_participation(wake, request)
    assert "阿力刚才提出的问题" in str(await request.assemble_context())
    scope = (await structured.resolve_scope(first))[0]
    assert not await structured.store.call("batch", scope, 30)


async def test_scheduler_skips_blocked_learning_without_skipping_other_groups(
    plugin, monkeypatch
):
    await sources(plugin)
    for i in range(4):
        await plugin.capture(event(f"other-{i}", session="456"))
    other = (await plugin.resolve_scope(event("other-scope", session="456")))[0]
    plugin.settings.update(learning_blocked_groups=["123"], min_messages=2)
    plugin.learn = AsyncMock()
    original_sleep = asyncio.sleep
    ticks = 0

    async def tick(seconds):
        nonlocal ticks
        if seconds == 15:
            ticks += 1
            if ticks > 1:
                raise asyncio.CancelledError
        await original_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", tick)
    with pytest.raises(asyncio.CancelledError):
        await plugin.poll()
    plugin.learn.assert_awaited_once_with(other)
