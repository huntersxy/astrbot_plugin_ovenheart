"""Ordinary bursts accumulate without cancelling requests or resetting silence."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from tests.test_participation import decision, requests
from tests.test_runtime import event

pytest_plugins = ["tests.test_participation"]


async def wait_calls(mock, count):
    async def ready():
        while mock.await_count < count:  # noqa: ASYNC110 - bounded wait for mock call entry
            await asyncio.sleep(0.001)

    await asyncio.wait_for(ready(), 1)


async def test_reply_waits_once_and_reuses_decision_without_new_input(participant):
    participant.settings["participation_silence_seconds"] = 0.08
    incoming = event("single")
    task = asyncio.create_task(requests(participant, incoming))
    try:
        await wait_calls(participant.context.llm_generate, 1)
        await asyncio.sleep(0.02)
        assert not task.done()
        result = await asyncio.wait_for(task, 1)
        assert len(result) == 1
        participant.context.llm_generate.assert_awaited_once()
        plan = incoming.get_extra("ovenheart_decision")
        assert plan["batch_id"] and plan["target"]["message_id"] == "single"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("structured", [False, True])
async def test_burst_during_judge_is_not_cancelled_and_latest_batch_is_seen(
    participant, structured
):
    participant.settings.update(
        participation_silence_seconds=0.08,
        context_mode="structured" if structured else "native",
    )
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    cancelled = False

    async def judge(**kwargs):
        nonlocal cancelled
        calls.append(json.loads(kwargs["prompt"]))
        if len(calls) == 1:
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled = True
                raise
            return decision()
        target = calls[-1]["recent_messages"][-2]["sequence"]
        return decision(target_sequence=target)

    participant.context.llm_generate.side_effect = judge
    owner = event("owner")
    task = asyncio.create_task(requests(participant, owner))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        for i in range(8):
            assert not await requests(participant, event(f"burst-{i}"))
        assert not cancelled and len(calls) == 1 and not task.done()
        release.set()
        await asyncio.sleep(0.02)
        assert len(calls) == 1
        assert len(await asyncio.wait_for(task, 1)) == 1
        assert len(calls) == 2 and not cancelled
        assert [m["message_id"] for m in calls[-1]["recent_messages"]][-8:] == [
            f"burst-{i}" for i in range(8)
        ]
        assert (
            owner.get_extra("ovenheart_decision")["target"]["message_id"] == "burst-6"
        )
        assert await participant.check_participation(owner)
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_repeated_wait_only_rejudges_changed_input_after_fixed_windows(
    participant,
):
    participant.settings["participation_silence_seconds"] = 0.08
    participant.context.llm_generate.side_effect = [
        decision(),
        decision("wait", wait_seconds=1),
        decision(),
    ]
    owner = event("owner")
    task = asyncio.create_task(requests(participant, owner))
    try:
        await wait_calls(participant.context.llm_generate, 1)
        assert not await requests(participant, event("window-one"))
        await wait_calls(participant.context.llm_generate, 2)
        assert not await requests(participant, event("window-two"))
        await asyncio.sleep(0.03)
        assert participant.context.llm_generate.await_count == 2
        assert len(await asyncio.wait_for(task, 2)) == 1
        assert participant.context.llm_generate.await_count == 3
        assert (
            owner.get_extra("ovenheart_decision")["target"]["message_id"]
            == "window-two"
        )
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_wait_without_new_input_stops_polling(participant):
    participant.settings["participation_silence_seconds"] = 0.04
    participant.context.llm_generate.return_value = decision("wait", wait_seconds=1)
    assert not await asyncio.wait_for(requests(participant, event("wait-only")), 2)
    participant.context.llm_generate.assert_awaited_once()
    assert not participant.participation.rooms[
        participant.participation.room_key(event())
    ].get("work")


async def test_arrivals_near_end_do_not_restart_silence_or_rejudge_each_message(
    participant,
):
    participant.settings["participation_silence_seconds"] = 8
    entered, expire = asyncio.Event(), asyncio.Event()

    async def quiet(changed, duration):
        assert duration == 8
        entered.set()
        await expire.wait()
        return False

    participant.participation.quiet_period = AsyncMock(side_effect=quiet)
    task = asyncio.create_task(requests(participant, event("start")))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        for i in range(8):
            assert not await requests(participant, event(f"late-{i}"))
            assert participant.context.llm_generate.await_count == 1
        participant.participation.quiet_period.assert_awaited_once()
        expire.set()
        assert len(await asyncio.wait_for(task, 1)) == 1
        assert participant.context.llm_generate.await_count == 2
        participant.participation.quiet_period.assert_awaited_once()
    finally:
        expire.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_ordinary_input_keeps_admitted_generation_but_direct_preempts(
    participant,
):
    participant.settings["participation_silence_seconds"] = 0.01
    owner = event("generate")
    request = (await requests(participant, owner))[0]
    assert not await requests(participant, event("later-chat"))
    assert await participant.check_participation(owner)
    await participant.apply_participation(owner, request)
    assert not owner.is_stopped()
    participant.context.llm_generate.assert_awaited_once()
    direct = event("at")
    direct.is_at_or_wake_command = True
    await participant.prioritize_direct(direct)
    assert not await participant.check_participation(owner)


async def test_direct_wake_preempts_silence_without_another_judge(participant):
    participant.settings["participation_silence_seconds"] = 0.2
    task = asyncio.create_task(requests(participant, event("quiet")))
    try:
        await wait_calls(participant.context.llm_generate, 1)
        direct = event("direct")
        direct.is_at_or_wake_command = True
        await participant.prioritize_direct(direct)
        with pytest.raises(asyncio.CancelledError):
            await task
        participant.context.llm_generate.assert_awaited_once()
        assert not direct.is_stopped()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_jev_silence_batches_requests_and_never_calls_chat_planner(participant):
    from tests.test_jev_owned import enable

    enable(participant)
    participant.settings["participation_silence_seconds"] = 0.08
    calls = []

    async def judge(cfg, state, questions):
        data = json.loads(state)
        calls.append(data)
        return {
            "status": "ready",
            "answers": {
                "action": {
                    "choice": str(data["eligible_targets"][-1]),
                    "confidence": 0.9,
                },
                "should_reply": {"noul": 0.9},
            },
        }

    participant.jev.client.evaluate = AsyncMock(side_effect=judge)
    incoming = event("jev-owner")
    task = asyncio.create_task(requests(participant, incoming))
    try:
        await wait_calls(participant.jev.client.evaluate, 1)
        for i in range(5):
            assert not await requests(participant, event(f"jev-extra-{i}"))
        assert len(await asyncio.wait_for(task, 1)) == 1
        assert len(calls) == 2
        participant.context.llm_generate.assert_not_awaited()
        assert (
            incoming.get_extra("ovenheart_decision")["target"]["message_id"]
            == "jev-extra-4"
        )
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_epoch_change_or_configuration_pause_cancels_quiet_owner(participant):
    participant.settings["participation_silence_seconds"] = 0.2
    task = asyncio.create_task(requests(participant, event("will-pause")))
    try:
        await wait_calls(participant.context.llm_generate, 1)
        participant.participation.close()
        assert not await asyncio.wait_for(task, 1)
        participant.context.llm_generate.assert_awaited_once()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
