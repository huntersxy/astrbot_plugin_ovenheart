"""Exercise physical-group coordination with native per-user session isolation."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("astrbot.api")

from astrbot.core.event_bus import EventBus  # noqa: E402
from astrbot.core.pipeline.context_utils import call_handler  # noqa: E402
from astrbot.core.pipeline.process_stage.stage import ProcessStage  # noqa: E402
from astrbot.core.pipeline.scheduler import PipelineScheduler  # noqa: E402

from tests.test_participation import decision, requests  # noqa: E402
from tests.test_runtime import event  # noqa: E402

pytest_plugins = ["tests.test_participation"]


def isolated(identifier, sender):
    """Apply the actual host isolation builder to one group event.

    Args:
        identifier: Unique synthetic platform message identifier.
        sender: Fictional group member identifier.

    Returns:
        SDK event whose UMO was rewritten by AstrBot's EventBus.
    """
    incoming = event(identifier, sender=sender)
    EventBus(asyncio.Queue(), {}, None)._apply_session_isolation(
        incoming, {"platform_settings": {"unique_session": True}}
    )
    return incoming


async def test_two_isolated_members_do_not_both_admit_in_one_physical_group(
    participant,
):
    first, second = isolated("first", "alice"), isolated("second", "bob")
    assert first.unified_msg_origin != second.unified_msg_origin
    assert first.get_group_id() == second.get_group_id()
    result = await asyncio.gather(
        requests(participant, first), requests(participant, second)
    )
    assert sum(bool(output) for output in result) == 1


async def test_direct_wake_interrupts_old_planning_even_when_learning_is_off(
    participant,
):
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def slow(**kwargs):
        entered.set()
        try:
            await asyncio.sleep(30)
        finally:
            cancelled.set()
        return decision()

    participant.context.llm_generate.side_effect = slow
    work = asyncio.create_task(requests(participant, isolated("old", "alice")))
    await entered.wait()
    participant.settings["learning_enabled"] = False
    direct = isolated("direct", "bob")
    direct.is_at_or_wake_command = True
    await participant.capture(direct)
    try:
        result = await asyncio.wait_for(asyncio.shield(work), timeout=0.2)
        assert result == [] and cancelled.is_set()
    finally:
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)


async def test_native_event_bus_and_scheduler_prioritize_direct_reply(participant):
    participant.settings["learning_enabled"] = False
    entered, cancelled, sent = asyncio.Event(), asyncio.Event(), asyncio.Event()
    deliveries = []

    async def slow(**kwargs):
        entered.set()
        try:
            await asyncio.sleep(30)
        finally:
            cancelled.set()

    participant.context.llm_generate.side_effect = slow
    config = {
        "provider_settings": {"enable": True},
        "platform_settings": {"unique_session": True},
    }

    class CaptureStage:
        async def process(self, event):
            await participant.capture(event)
            event.set_extra(
                "activated_handlers",
                []
                if event.is_at_or_wake_command
                else [SimpleNamespace(event_filters=[])],
            )

    class StarStage:
        async def process(self, event):
            async for request in call_handler(event, participant.participate):
                yield request

    class AgentStage:
        async def process(self, event):
            event.set_result(event.plain_result("direct reply"))
            yield

    class SendStage:
        async def process(self, event):
            if event.get_result():
                await participant.guard_participation_result(event)
                if not event.is_stopped():
                    deliveries.append(event.message_obj.message_id)
                    await participant.record_delivery(event)
                    event.clear_result()
                    sent.set()

    process = ProcessStage()
    process.ctx = SimpleNamespace(astrbot_config=config)
    process.star_request_sub_stage = StarStage()
    process.agent_sub_stage = AgentStage()
    scheduler = PipelineScheduler(process.ctx)
    scheduler.stages = [CaptureStage(), process, SendStage()]
    queue = asyncio.Queue()
    bus = EventBus(
        queue,
        {"synthetic": scheduler},
        SimpleNamespace(get_conf_info=lambda umo: {"id": "synthetic"}),
    )
    dispatcher = asyncio.create_task(bus.dispatch())
    old = event("old-flow", sender="alice")
    direct = event("direct-flow", sender="bob")
    direct.is_at_or_wake_command = True
    try:
        await queue.put(old)
        await asyncio.wait_for(entered.wait(), 1)
        await queue.put(direct)
        await asyncio.wait_for(sent.wait(), 1)
        await asyncio.wait_for(cancelled.wait(), 1)
        await asyncio.gather(*bus._pending_tasks)
        assert old.unified_msg_origin != direct.unified_msg_origin
        assert deliveries == ["direct-flow"]
        assert old.get_extra("ovenheart_decision") is None
    finally:
        dispatcher.cancel()
        for task in tuple(bus._pending_tasks):
            task.cancel()
        await asyncio.gather(dispatcher, *bus._pending_tasks, return_exceptions=True)


async def test_shared_group_quota_survives_reload_without_sharing_learning(
    participant, monkeypatch
):
    from astrbot_plugin_ovenheart.core import store as store_module

    now = [1000000.0]
    monkeypatch.setattr(store_module.time, "time", lambda: now[0])
    first, second = isolated("a", "alice"), isolated("b", "bob")
    first_scope, persona, label = await participant.resolve_scope(first)
    second_scope, _, _ = await participant.resolve_scope(second)
    for scope, event_ in [(first_scope, first), (second_scope, second)]:
        await participant.store.call(
            "touch_scope", scope, event_.unified_msg_origin, persona, label
        )
    settings = participant.settings | {
        "participation_hourly_limit": 1,
        "participation_cooldown": 5,
    }
    plan = {
        "action": "reply",
        "confidence": 0.9,
        "reason": "参与",
        "intent": "简短回应",
        "room": participant.participation.room_key(first),
    }
    one = await participant.store.call(
        "decide", first_scope, plan, "auto", "admitted", settings
    )
    assert one["outcome"] == "admitted"
    now[0] += 6
    await participant.store.call("initialize")
    two = await participant.store.call(
        "decide", second_scope, plan, "auto", "admitted", settings
    )
    assert two["outcome"] == "cancelled"
    second_state = await participant.store.call("state", second_scope)
    assert "本小时" in second_state["decisions"][0]["reason"]
    assert not second_state["traits"] and second_state["scope"]["pending"] == 0


async def test_new_direct_input_cancels_a_queued_isolated_autonomous_reply(participant):
    first = isolated("admitted", "alice")
    request = (await requests(participant, first))[0]
    direct = isolated("direct", "bob")
    direct.is_at_or_wake_command = True
    await participant.capture(direct)
    await participant.apply_participation(first, request)
    assert first.is_stopped()


async def test_other_groups_and_bots_do_not_interrupt_one_another(participant):
    first = isolated("here", "alice")
    request = (await requests(participant, first))[0]
    other = event("other", session="another-group")
    await participant.capture(other)
    second_bot = isolated("other-bot", "bob")
    second_bot.message_obj.self_id = "another-bot"
    await participant.capture(second_bot)
    await participant.apply_participation(first, request)
    assert not first.is_stopped()


async def test_older_delayed_capture_and_same_event_do_not_cancel_latest(participant):
    old = isolated("older", "alice")
    latest = isolated("latest", "bob")
    request = (await requests(participant, latest))[0]
    await participant.capture(old)
    await participant.capture(latest)
    await participant.apply_participation(latest, request)
    assert not latest.is_stopped()




async def test_denied_input_does_not_interrupt_an_allowed_group_reply(
    participant, monkeypatch
):
    from tests.test_runtime import SessionServiceManager

    first = isolated("permitted", "alice")
    request = (await requests(participant, first))[0]
    denied = isolated("denied", "bob")
    monkeypatch.setattr(
        SessionServiceManager,
        "is_session_enabled",
        AsyncMock(side_effect=lambda umo: umo != denied.unified_msg_origin),
    )
    await participant.capture(denied)
    await participant.apply_participation(first, request)
    assert not first.is_stopped()
