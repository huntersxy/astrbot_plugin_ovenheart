"""Optional judgments use the real frozen ledger without taking reply authority."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.api.provider import ProviderRequest
from astrbot.core.message.components import At

from astrbot_plugin_ovenheart.integrations import oven_multi
from tests.test_participation import decision, requests
from tests.test_structured_conversation import incoming

pytest_plugins = ["tests.test_structured_conversation"]


@pytest.fixture
def companion(structured):
    owner = SimpleNamespace(
        ovenheart_protocol=1,
        closing=False,
        config={"jev": {"enabled": True}},
        ovenheart_jev_signature=lambda: "revision-1",
        analyze_for_ovenheart=AsyncMock(
            return_value={
                "status": "ready",
                "fields": {"intent": "提问求助"},
                "advice": [],
            }
        ),
    )
    structured.context.get_registered_star = lambda name: (
        SimpleNamespace(activated=True, star_cls=owner)
        if name == "astrbot_plugin_oven_multi"
        else None
    )
    return owner


async def snapshot(plugin):
    await plugin.capture(incoming("question", "汐兮雨是谁", sender="alice"))
    wake = incoming("at", "", sender="alice")
    wake.message_obj.message = [At(qq="bot")]
    wake.is_at_or_wake_command = True
    request = ProviderRequest(prompt="(友好地询问用户想要聊些什么)")
    await plugin.coordinate_direct_request(wake, request)
    scope = wake.get_extra("ovenheart_context_snapshot")["scope"]
    direct = plugin.direct_reply_context(wake, scope)
    return wake, request, direct, wake.get_extra("ovenheart_context_snapshot")["epoch"]


async def test_direct_native_pipeline_shares_target_and_temporary_judgment(
    structured, companion
):
    event, request, direct, epoch = await snapshot(structured)
    await structured.capture(incoming("future", "FUTURE_CHAT", sender="peer"))
    await structured.apply_persona(event, request)
    await structured.apply_participation(event, request)
    assert companion.analyze_for_ovenheart.await_count == 1
    payload = companion.analyze_for_ovenheart.await_args.args[1]
    assert payload["target"]["message_id"] == "at"
    assert payload["bare_mention"] and payload["direct_target"]
    assert "汐兮雨是谁" in json.dumps(payload["focus"], ensure_ascii=False)
    assert "FUTURE_CHAT" not in json.dumps(payload, ensure_ascii=False)
    assert "提问求助" in json.dumps(
        await request.assemble_context(), ensure_ascii=False
    )
    assert all(
        p.model_dump_for_context()["_no_save"] for p in request.extra_user_content_parts
    )
    repeated = await structured.oven_multi.analyze(
        event,
        direct["scope"],
        direct["reply_message"],
        direct["messages"],
        epoch=epoch,
        bare=True,
    )
    assert repeated["cache_hit"] and companion.analyze_for_ovenheart.await_count == 1


async def test_cache_is_target_and_configuration_specific(structured, companion):
    event, _, direct, epoch = await snapshot(structured)
    analyze = structured.oven_multi.analyze
    args = (event, direct["scope"], direct["reply_message"], direct["messages"])
    await analyze(*args, epoch=epoch)
    await analyze(*args, epoch=epoch)
    assert companion.analyze_for_ovenheart.await_count == 1
    await analyze(
        event, direct["scope"], direct["messages"][0], direct["messages"], epoch=epoch
    )
    assert companion.analyze_for_ovenheart.await_count == 2
    companion.ovenheart_jev_signature = lambda: "revision-2"
    await analyze(*args, epoch=epoch)
    assert companion.analyze_for_ovenheart.await_count == 3


async def test_timeout_returns_to_native_reply_without_extra_generation(
    structured, companion, monkeypatch
):
    event, request, _, _ = await snapshot(structured)
    cancelled = asyncio.Event()

    async def slow(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    companion.analyze_for_ovenheart.side_effect = slow
    monkeypatch.setattr(oven_multi, "JEV_DEADLINE", 0.01)
    await asyncio.wait_for(structured.apply_persona(event, request), 1)
    await structured.apply_participation(event, request)
    await asyncio.wait_for(cancelled.wait(), 1)
    assert event.get_extra("ovenheart_jev")["status"] == "timeout"
    assert "汐兮雨是谁" in json.dumps(
        await request.assemble_context(), ensure_ascii=False
    )
    assert "jev_reading" not in event.get_extra("ovenheart_direct_context")


@pytest.mark.parametrize("change", ["closing", "settings", "scope", "epoch"])
async def test_changed_authority_discards_inflight_result(
    structured, companion, change
):
    event, _, direct, epoch = await snapshot(structured)

    async def changed(*args):
        if change == "closing":
            companion.closing = True
        elif change == "settings":
            structured.settings_revision += "-changed"
        elif change == "scope":
            structured.resolve_scope = AsyncMock(return_value=("other-scope", "", ""))
        else:
            structured.participation.windows[direct["scope"]]["epoch"] = "new-epoch"
        return {"status": "ready", "fields": {"intent": "STALE"}, "advice": []}

    companion.analyze_for_ovenheart.side_effect = changed
    result = await structured.oven_multi.analyze(
        event, direct["scope"], direct["reply_message"], direct["messages"], epoch=epoch
    )
    assert result["status"] == "stale" and oven_multi.reading(result) is None


async def test_absent_companion_does_not_change_native_reply(structured):
    event, request, _, _ = await snapshot(structured)
    await structured.apply_persona(event, request)
    await structured.apply_participation(event, request)
    assert not structured.oven_multi.status()["connected"]
    assert event.get_extra("ovenheart_jev") is None
    assert "汐兮雨是谁" in json.dumps(
        await request.assemble_context(), ensure_ascii=False
    )


@pytest.mark.parametrize("selected", [1, 2])
async def test_planner_reading_is_reused_only_for_the_same_selected_target(
    structured, companion, selected
):
    structured.settings.update(
        participation_mode="auto",
        participation_min_messages=1,
        participation_cooldown=0,
    )
    await structured.capture(incoming("earlier", "EARLIER_QUESTION", sender="alice"))
    structured.context.llm_generate.return_value = decision(target_sequence=selected)
    trigger = incoming("trigger", "OTHER_TOPIC", sender="bob")
    yielded = await requests(structured, trigger)
    assert len(yielded) == 1
    planner = json.loads(structured.context.llm_generate.await_args.kwargs["prompt"])
    assert planner["jev_reading"]["target_sequence"] == 2
    await structured.apply_persona(trigger, yielded[0])
    await structured.apply_participation(trigger, yielded[0])
    calls = companion.analyze_for_ovenheart.await_args_list
    assert len(calls) == (1 if selected == 2 else 2)
    assert calls[-1].args[1]["target"]["message_id"] == (
        "trigger" if selected == 2 else "earlier"
    )
    plan = trigger.get_extra("ovenheart_decision")
    assert (
        plan["target"]["sequence"] == selected
        and plan["jev_reading"]["fields"]["intent"] == "提问求助"
    )
