"""Explicit wakes preempt owned work before native follow-up capture and locks."""

import asyncio
from types import SimpleNamespace

import pytest
from astrbot.core.pipeline.process_stage import follow_up
from astrbot.core.utils.session_lock import session_lock_manager

from astrbot_plugin_ovenheart.main import DirectWakeFilter
from tests.test_participation import requests
from tests.test_runtime import event

pytest_plugins = ["tests.test_participation"]


async def test_direct_wake_preempts_active_autonomous_generation_and_frees_native_lock(
    participant,
):
    participant.settings["participation_cooldown"] = 0
    previous = event("autonomous", sender="alice")
    locked, entered = asyncio.Event(), asyncio.Event()
    runner = SimpleNamespace(
        run_context=SimpleNamespace(context=SimpleNamespace(event=previous)),
        request_stop=lambda: None,
        follow_up=lambda **kwargs: pytest.fail(
            "Direct input must not join the obsolete autonomous run"
        ),
    )

    async def automatic():
        assert len(await requests(participant, previous)) == 1
        async with session_lock_manager.acquire_lock(previous.unified_msg_origin):
            follow_up.register_active_runner(previous.unified_msg_origin, runner)
            try:
                locked.set()
                await asyncio.Event().wait()
            finally:
                follow_up.unregister_active_runner(previous.unified_msg_origin, runner)

    old = asyncio.create_task(automatic())
    await asyncio.wait_for(locked.wait(), 1)
    direct = event("explicit-at", sender="alice")
    direct.is_at_or_wake_command = True

    async def prioritized():
        await participant.prioritize_direct(direct)
        assert follow_up.try_capture_follow_up(direct) is None
        async with session_lock_manager.acquire_lock(direct.unified_msg_origin):
            entered.set()

    new = asyncio.create_task(prioritized())
    try:
        await asyncio.wait_for(entered.wait(), 0.5)
        assert previous.is_stopped() and not direct.is_stopped()
        await new
        with pytest.raises(asyncio.CancelledError):
            await old
    finally:
        for task in [old, new]:
            if not task.done():
                task.cancel()
        await asyncio.gather(old, new, return_exceptions=True)


async def test_name_mention_uses_direct_path_without_decision_frequency_or_planner(
    participant,
):
    participant.settings.update(
        mentioned_bot_reply=True, mention_names=["烤箱"], planner_interval_seconds=300
    )
    incoming = event("name", text="烤箱，这句话是什么意思")
    assert DirectWakeFilter().filter(
        incoming, participant.context.get_config(incoming.unified_msg_origin)
    )
    await participant.prioritize_direct(incoming)
    assert incoming.is_at_or_wake_command
    assert not await requests(participant, incoming)
    participant.context.llm_generate.assert_not_awaited()


async def test_direct_reservation_blocks_new_autonomous_work_until_pipeline_completes(
    participant,
):
    entered, release = asyncio.Event(), asyncio.Event()
    direct = event("direct")
    direct.is_at_or_wake_command = True

    async def pipeline():
        await participant.prioritize_direct(direct)
        entered.set()
        await release.wait()

    task = asyncio.create_task(pipeline())
    await entered.wait()
    try:
        assert not await requests(participant, event("ordinary"))
        participant.context.llm_generate.assert_not_awaited()
        release.set()
        await task
        await asyncio.sleep(0)
        assert len(await requests(participant, event("later"))) == 1
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_preemption_keeps_other_groups_and_existing_direct_requests(participant):
    ready = asyncio.Event()
    release = asyncio.Event()
    other = event("other-group", session="456")
    same_direct = event("already-direct")
    tasks = []

    async def owned(owner, direct):
        participant.direct_priority.watch(owner, direct=direct)
        ready.set()
        await release.wait()

    for owner, direct in [(other, False), (same_direct, True)]:
        ready.clear()
        tasks.append(asyncio.create_task(owned(owner, direct)))
        await ready.wait()
    incoming = event("priority")
    incoming.is_at_or_wake_command = True
    try:
        await participant.prioritize_direct(incoming)
        assert not other.is_stopped() and not same_direct.is_stopped()
        assert all(not task.cancelling() for task in tasks)
    finally:
        release.set()
        await asyncio.gather(*tasks)


async def test_disabled_name_and_ordinary_chatter_do_not_gain_priority(participant):
    participant.settings.update(mentioned_bot_reply=False, mention_names=["烤箱"])
    incoming = event("not-explicit", text="烤箱怎么样")
    assert not DirectWakeFilter().filter(
        incoming, participant.context.get_config(incoming.unified_msg_origin)
    )
    await participant.prioritize_direct(incoming)
    assert not participant.direct_priority.entries
