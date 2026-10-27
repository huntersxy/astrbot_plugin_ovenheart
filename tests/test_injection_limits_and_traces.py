"""Verify operator budgets and request/send metadata without provider calls."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from astrbot.api.provider import ProviderRequest

from astrbot_plugin_ovenheart.chat.participation import bound_reply_messages
from astrbot_plugin_ovenheart.core.retrieval import HabitRetriever
from astrbot_plugin_ovenheart.core.settings import DEFAULTS
from tests.test_participation import decision, requests
from tests.test_runtime import completion, event, sources

pytest_plugins = ["tests.test_participation"]


@pytest.mark.parametrize("kind", ["expression", "jargon"])
async def test_contextual_limits_above_five_include_literal_definitions(kind):
    traits = [
        {
            "id": i,
            "kind": kind,
            "status": "active",
            "confidence": 0.95,
            "updated": 1,
            "situation": "讨论本群词时",
            "content": f"TERM{i}：本群第{i}个词义",
        }
        for i in range(1, 9)
    ]
    context = SimpleNamespace(
        llm_generate=AsyncMock(
            return_value=SimpleNamespace(
                completion_text=json.dumps(
                    {"selected_ids": list(range(1, 9)) if kind == "expression" else []}
                )
            )
        )
    )
    query = " ".join(f"TERM{i}" for i in range(1, 9))
    result = await HabitRetriever(context, None).select(
        "scope",
        traits,
        query,
        DEFAULTS | {"max_traits": 8},
        provider_id="simulated",
        literal_query=query,
    )
    assert len(result["traits"]) == 8
    assert (
        json.loads(context.llm_generate.await_args.kwargs["prompt"])["max_selected"]
        == 8
    )


def test_context_window_keeps_a_complete_recent_suffix_within_both_budgets():
    messages = [{"sequence": i, "text": "原文" * i} for i in range(1, 6)]
    original = json.dumps(messages, ensure_ascii=False)
    budget = len(json.dumps(messages[-2:], ensure_ascii=False))
    selected = bound_reply_messages(
        messages,
        DEFAULTS
        | {
            "reply_context_messages": 4,
            "reply_context_budget": budget,
        },
    )
    assert selected == messages[-2:]
    assert len(json.dumps(selected, ensure_ascii=False)) == budget
    assert json.dumps(messages, ensure_ascii=False) == original
    assert not bound_reply_messages(messages, DEFAULTS | {"reply_context_messages": 0})
    assert not bound_reply_messages(messages, DEFAULTS | {"reply_context_budget": 0})


async def test_disabling_extra_context_keeps_the_validated_older_target(participant):
    participant.settings.update(participation_mode="observe", participation_cooldown=0)
    await requests(participant, event("older", sender="older-user"))
    participant.settings.update(participation_mode="auto", reply_context_messages=0)
    participant.context.llm_generate.return_value = decision(target_sequence=1)
    incoming = event("newer", sender="newer-user")
    request = (await requests(participant, incoming))[0]
    plan = incoming.get_extra("ovenheart_decision")
    assert not plan["messages"] and plan["target"]["sender_id"] == "older-user"
    await participant.apply_persona(incoming, request)
    await participant.apply_participation(incoming, request)
    trace = (await participant.store.call("state", plan["scope"]))["turn_traces"][0]
    assert trace["details"]["context_count"] == 0
    assert trace["details"]["target_sender_id"] == "older-user"
    await requests(participant, event("interrupt"))
    await participant.check_participation(incoming)
    assert (await participant.store.call("state", plan["scope"]))["turn_traces"][-1][
        "status"
    ] == "cancelled"


async def test_trace_distinguishes_selected_included_and_send_stage_without_exposure(
    plugin,
    monkeypatch,
):
    scope = await sources(plugin)
    plugin.context.llm_generate.side_effect = completion
    await plugin.learn(scope)
    state = await plugin.store.call("state", scope)
    for trait in state["traits"]:
        await plugin.store.call(
            "mutate", scope, "trait", {"id": trait["id"], "status": "active"}
        )
    await plugin.store.call("mutate", scope, "profile", {"identity": "身份底稿。" * 60})
    plugin.settings.update(prompt_budget=500, guidance_delivery="cached")
    incoming = event("trace", "亲切 简短反问 先回应对方的情绪 好家伙")
    incoming.is_at_or_wake_command = True
    request = ProviderRequest(prompt=incoming.message_str, system_prompt="稳定前缀")
    await plugin.apply_persona(incoming, request)
    await plugin.apply_participation(incoming, request)
    prepared = (await plugin.store.call("state", scope))["turn_traces"]
    assert len(prepared) == 1 and prepared[0]["status"] == "prepared"
    details = prepared[0]["details"]
    assert details["selected_ids"] and not details["included_ids"]
    assert details["temporary_chars"] == sum(
        len(p.text) for p in request.extra_user_content_parts
    )
    assert request.system_prompt.startswith("稳定前缀")
    assert "[OvenHeart reply target]" not in request.system_prompt
    assert not (await plugin.store.call("state", scope))["applications"]
    incoming.set_result(incoming.plain_result("回复正文"))
    await plugin.record_delivery(incoming)
    await plugin.record_delivery(incoming)
    sent = (await plugin.store.call("state", scope))["turn_traces"]
    assert len(sent) == 1 and sent[0]["status"] == "send_hook"
    assert not (await plugin.store.call("state", scope))["applications"]
    assert "亲切 简短反问" not in json.dumps(sent, ensure_ascii=False)
    other = await plugin.resolve_scope(event("other", session="different"))
    await plugin.store.call("touch_scope", other[0], "other", "default", "其他会话")
    assert not (await plugin.store.call("state", other[0]))["turn_traces"]
    monkeypatch.setattr(
        plugin.store, "trace_turn", Mock(side_effect=OSError("write failed"))
    )
    resilient = event("diagnostic-failure")
    retry = ProviderRequest(prompt=resilient.message_str)
    await plugin.apply_persona(resilient, retry)
    await plugin.apply_participation(resilient, retry)
    assert retry.extra_user_content_parts and not resilient.is_stopped()
