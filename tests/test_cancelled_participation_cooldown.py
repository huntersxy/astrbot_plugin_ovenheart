"""Separate unsent participation reservations from observed native replies."""

import asyncio
import json

from tests.test_participation import decision, requests
from tests.test_runtime import event

pytest_plugins = ["tests.test_participation"]


async def test_cancelled_unsent_request_does_not_spend_full_reply_cooldown(participant):
    original = event("original")
    assert len(await requests(participant, original)) == 1
    plan = original.get_extra("ovenheart_decision")
    assert await participant.store.call(
        "participation_gate", plan["scope"], participant.settings, plan["room"]
    )
    assert await requests(participant, event("interrupting")) == []
    await participant.guard_participation_result(original)
    assert original.is_stopped()
    assert not await participant.store.call(
        "participation_gate", plan["scope"], participant.settings, plan["room"]
    )
    # The existing separate decision-check interval still applies. Advance only
    # that interval; a 90-second send cooldown would continue to reject this.
    participant.participation.windows[plan["scope"]]["last_check"] -= 31
    assert len(await requests(participant, event("new-opportunity"))) == 1
    assert participant.context.llm_generate.await_count == 2


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
