"""Separate unsent participation reservations from observed native replies."""

import asyncio
import json

from tests.test_participation import decision, requests
from tests.test_runtime import event

pytest_plugins = ["tests.test_participation"]


async def test_cancelled_unsent_request_resumes_without_decision_interval_or_more_inputs(
    participant,
):
    original = event("original")
    assert len(await requests(participant, original)) == 1
    plan = original.get_extra("ovenheart_decision")
    participant.settings["participation_min_messages"] = 3
    latest = event("interrupting")
    assert len(await requests(participant, latest)) == 1
    replacement = latest.get_extra("ovenheart_decision")
    assert replacement["resumed"] is True
    assert replacement["id"] != plan["id"]
    await participant.guard_participation_result(original)
    assert original.is_stopped()
    assert await participant.check_participation(latest)
    assert participant.context.llm_generate.await_count == 2
    decisions = (await participant.store.call("state", plan["scope"]))["decisions"]
    assert next(d for d in decisions if d["id"] == plan["id"])["outcome"] == "cancelled"
    assert (
        next(d for d in decisions if d["id"] == replacement["id"])["outcome"]
        == "admitted"
    )


async def test_cancellation_never_erases_an_observed_send_cooldown(participant):
    incoming = event("direct-observation")
    scope, persona, label = await participant.resolve_scope(incoming)
    room = participant.participation.room_key(incoming)
    await participant.store.call(
        "touch_scope", scope, incoming.unified_msg_origin, persona, label
    )
    await participant.store.call("note_reply", scope, room)
    plan = json.loads(decision().completion_text) | {"room": room, "followup": True}
    admitted = await participant.store.call(
        "decide", scope, plan, "auto", "admitted", participant.settings
    )
    assert admitted["outcome"] == "admitted"
    await participant.store.call("cancel_decision", scope, admitted["id"], "New input")
    await participant.store.call("initialize")
    assert await participant.store.call(
        "participation_gate", scope, participant.settings, room
    )


async def test_reservations_still_serialize_isolated_sessions_in_one_room(participant):
    room = "room:shared-physical-group"
    scopes = ["isolated-first", "isolated-second"]
    for scope in scopes:
        await participant.store.call("touch_scope", scope, scope, "default", scope)
    plan = json.loads(decision().completion_text) | {"room": room}
    admissions = await asyncio.gather(
        *(
            participant.store.call(
                "decide", scope, plan, "auto", "admitted", participant.settings
            )
            for scope in scopes
        )
    )
    assert [result["outcome"] for result in admissions] == ["admitted", "cancelled"]
    await participant.store.call("initialize")
    assert await participant.store.call(
        "participation_gate", scopes[1], participant.settings, room
    )
    # Cancellation must match its actual scope and identifier.
    await participant.store.call(
        "cancel_decision", scopes[1], admissions[0]["id"], "Wrong scope"
    )
    assert await participant.store.call(
        "participation_gate", scopes[1], participant.settings, room
    )
    await participant.store.call(
        "cancel_decision", scopes[0], admissions[0]["id"], "Superseded before send"
    )
    assert not await participant.store.call(
        "participation_gate", scopes[1], participant.settings, room
    )
    retried = await participant.store.call(
        "decide", scopes[1], plan, "auto", "admitted", participant.settings
    )
    assert retried["outcome"] == "admitted"


async def test_interrupted_planner_resumes_on_latest_input_after_full_quiet_window(
    participant,
):
    entered = asyncio.Event()
    calls = []

    async def generate(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            entered.set()
            await asyncio.Event().wait()
        return decision()

    participant.context.llm_generate.side_effect = generate
    first = asyncio.create_task(requests(participant, event("planning")))
    await asyncio.wait_for(entered.wait(), 0.5)
    participant.settings.update(
        participation_debounce=0.08, participation_min_messages=4
    )
    second = asyncio.create_task(requests(participant, event("interruption-one")))
    try:
        await asyncio.sleep(0.04)
        third = asyncio.create_task(requests(participant, event("interruption-last")))
        await asyncio.sleep(0.04)
        assert len(calls) == 1
        one, two, three = await asyncio.wait_for(
            asyncio.gather(first, second, third), 0.5
        )
        assert one == two == [] and len(three) == 1
        assert len(calls) == 2
        payload = json.loads(calls[-1]["prompt"])
        assert payload["recent_messages"][-1]["message_id"] == "interruption-last"
    finally:
        for task in (first, second):
            task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)


async def test_actual_reply_during_quiet_wait_cancels_continuation(participant):
    original = event("old-admission")
    await requests(participant, original)
    participant.settings["participation_debounce"] = 0.08
    latest = event("new-input")
    work = asyncio.create_task(requests(participant, latest))
    try:
        await asyncio.sleep(0.02)
        direct = event("native-answer")
        direct.set_result(direct.plain_result("已经回答"))
        await participant.record_delivery(direct)
        assert await asyncio.wait_for(work, 0.5) == []
        participant.context.llm_generate.assert_awaited_once()
        assert not participant.participation.rooms[
            participant.participation.room_key(latest)
        ].get("work")
    finally:
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)


async def test_ended_observation_does_not_create_an_early_continuation(participant):
    participant.context.llm_generate.return_value = decision("observe")
    assert await requests(participant, event("observed")) == []
    participant.context.llm_generate.return_value = decision()
    assert await requests(participant, event("later-message")) == []
    participant.context.llm_generate.assert_awaited_once()


async def test_continuation_waits_for_planner_capacity_instead_of_another_input(
    participant,
):
    await requests(participant, event("admitted-before-capacity"))
    participant.participation.inflight = 2
    latest = event("latest-awaiting-capacity")
    work = asyncio.create_task(requests(participant, latest))
    try:
        await asyncio.sleep(0.02)
        assert not work.done()
        participant.context.llm_generate.assert_awaited_once()
        participant.participation.inflight = 0
        participant.participation.capacity_changed.set()
        assert len(await asyncio.wait_for(work, 0.5)) == 1
        assert latest.get_extra("ovenheart_decision")["resumed"] is True
    finally:
        participant.participation.inflight = 0
        participant.participation.capacity_changed.set()
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)
