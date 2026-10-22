"""Bridge native streaming completion into bounded self context exactly once."""

import json

import pytest
from astrbot.api.provider import ProviderRequest
from astrbot.core.message.message_event_result import ResultContentType

from tests.test_participation import requests
from tests.test_runtime import event

pytest_plugins = ["tests.test_runtime", "tests.test_participation"]


async def test_direct_stream_completion_enables_followup_and_keeps_learning_context_volatile(
    participant,
):
    participant.settings.update(participation_min_messages=3)
    direct = event("streamed-direct", text="今天还是有点不知道怎么办")
    direct.is_at_or_wake_command = True
    direct.set_extra("enable_streaming", True)
    await participant.capture(direct)
    await participant.apply_persona(
        direct, ProviderRequest(prompt=direct.get_message_str())
    )
    scope = direct.get_extra("ovenheart_reply_scope")
    assert not participant.participation.windows.get(scope)
    finish = direct.plain_result("STREAM_SELF：你想先说说，还是一起理一理？")
    finish.result_content_type = ResultContentType.STREAMING_FINISH
    direct.set_result(finish)
    direct._has_send_oper = True
    await participant.guard_participation_result(direct)
    await participant.guard_participation_result(direct)
    own = [m for m in participant.participation.windows[scope]["messages"] if m["self"]]
    assert len(own) == 1 and own[0]["reply_to"]["message_id"] == "streamed-direct"
    assert len([m for m in participant.learning_context[scope] if m.get("self")]) == 1
    assert direct.get_extra("enable_streaming") is True
    assert (
        direct.get_result() is finish
        and finish.result_content_type == ResultContentType.STREAMING_FINISH
    )
    assert not direct.is_stopped()
    output = await requests(participant, event("after-stream", text="我先说说吧"))
    assert len(output) == 1
    payload = json.loads(participant.context.llm_generate.await_args.kwargs["prompt"])
    assert payload["is_followup"] is True
    assert len([m for m in payload["recent_messages"] if m["self"]]) == 1
    assert "STREAM_SELF" in payload["recent_messages"][0]["text"]
    state = await participant.store.call("state", scope)
    assert "STREAM_SELF" not in json.dumps(state)
    assert not state["applications"]


@pytest.mark.parametrize(
    "boundary",
    ["not_sent", "no_request", "stopped", "persona", "paused", "intermediate"],
)
async def test_nonfinal_unbound_or_disallowed_streams_do_not_record_self_context(
    participant, boundary
):
    direct = event("excluded-stream")
    direct.is_at_or_wake_command = True
    await participant.capture(direct)
    await participant.apply_persona(
        direct, ProviderRequest(prompt=direct.get_message_str())
    )
    scope = direct.get_extra("ovenheart_reply_scope")
    finish = direct.plain_result("EXCLUDED_STREAM_TEXT")
    finish.result_content_type = (
        ResultContentType.STREAMING_RESULT
        if boundary == "intermediate"
        else ResultContentType.STREAMING_FINISH
    )
    direct.set_result(finish)
    direct._has_send_oper = boundary != "not_sent"
    if boundary == "no_request":
        direct.set_extra("ovenheart_reply_scope", None)
    elif boundary == "stopped":
        direct.stop_event()
    elif boundary == "persona":
        participant.context.persona_manager.resolve_selected_persona.return_value = (
            "changed",
            {"prompt": "另一个人格"},
            None,
            False,
        )
    elif boundary == "paused":
        participant.settings["enabled"] = False
    await participant.guard_participation_result(direct)
    assert not participant.participation.windows.get(scope)
    assert not any(m.get("self") for m in participant.learning_context[scope])
    assert "EXCLUDED_STREAM_TEXT" not in json.dumps(
        await participant.store.call("state", scope)
    )
