"""Owned Jev judgments never replace permission, source or human-review boundaries."""

import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from astrbot_plugin_ovenheart.jev import participation_state
from astrbot_plugin_ovenheart.jev_transport import SystemOne, probability
from tests.test_participation import requests
from tests.test_runtime import api_call, event, sources

pytest_plugins = ["tests.test_participation"]


def enable(plugin):
    plugin.settings.update(
        jev_enabled=True, jev_participation_enabled=True, jev_learning_enabled=True
    )
    plugin.config["jev_connection"] = {
        "base_url": "http://example.invalid",
        "model": "jev-latest",
        "api_key": "synthetic-secret",
    }


async def test_jev_replaces_planner_but_explicit_wakes_bypass(participant):
    enable(participant)
    participant.jev.client.evaluate = AsyncMock(
        return_value={"status": "ready", "answers": {"should_reply": {"noul": 0.1}}}
    )
    assert not await requests(
        participant, event("low", text="This is a detailed question.")
    )
    call = participant.jev.client.evaluate.await_args
    assert set(call.args[2]) == {"action", "should_reply"}
    participant.context.llm_generate.assert_not_awaited()
    assert (
        json.loads(call.args[1])["messages"][-1]["text"]
        == "This is a detailed question."
    )
    incoming = event("direct", text="Explain it")
    incoming.is_at_or_wake_command = True
    await participant.prioritize_direct(incoming)
    await requests(participant, incoming)
    assert participant.jev.client.evaluate.await_count == 1


async def test_unavailable_jev_observes_without_main_model_fallback(participant):
    enable(participant)
    participant.jev.client.evaluate = AsyncMock(
        return_value={"status": "error", "http_status": 503}
    )
    assert not await requests(participant, event("fallback"))
    participant.context.llm_generate.assert_not_awaited()


async def test_minimum_jev_budget_keeps_a_real_target_with_long_persona(
    participant, monkeypatch
):
    enable(participant)
    participant.settings["jev_max_state_chars"] = 1500
    monkeypatch.setattr(
        participant.context.persona_manager,
        "get_persona_v3_by_id",
        lambda _: {"prompt": "identity " * 1000},
    )
    participant.jev.client.evaluate = AsyncMock(return_value={"status": "error"})
    await requests(participant, event("small-budget", text='"' * 700))
    state = participant.jev.client.evaluate.await_args.args[1]
    assert len(state) <= 1500
    data = json.loads(state)
    assert data["messages"][-1]["text"] and data["eligible_targets"]
    participant.context.llm_generate.assert_not_awaited()


@pytest.mark.parametrize(
    "selected,value,expected",
    [
        ("1", 0.9, "reply"),
        ("1", 0.1, "observe"),
        ("999", 0.99, "observe"),
        ("wait", 0, "wait"),
        ("observe", 0.9, "observe"),
    ],
)
async def test_typed_jev_selects_real_target_without_main_planner(
    participant, selected, value, expected
):
    enable(participant)
    participant.jev.client.evaluate = AsyncMock(
        return_value={
            "status": "ready",
            "answers": {
                "action": {"choice": selected, "confidence": 0.9},
                "should_reply": {"noul": value},
            },
        }
    )
    incoming = event("typed")
    scope, persona, _ = await participant.resolve_scope(incoming)
    await participant.capture(incoming)
    state = await participant.store.call("guidance", scope, False)
    plan = await participant.participation.plan(
        incoming,
        scope,
        persona,
        state,
        [{"sequence": 1, "sender_id": "alice", "text": "What do you think?"}],
        participant.settings,
        False,
    )
    assert plan["action"] == expected
    assert plan["target_sequence"] == (1 if expected == "reply" else 0)
    participant.context.llm_generate.assert_not_awaited()


async def test_target_survives_escaped_input_within_state_budget(participant):
    enable(participant)
    participant.settings["jev_max_state_chars"] = 1500
    participant.jev.client.evaluate = AsyncMock(return_value={"status": "error"})
    target = {
        "sequence": 4,
        "sender_id": "author",
        "text": '"' * 700,
        "reply_to": {"text": "quoted" * 100},
        "media": [{"text": "image" * 100}] * 2,
    }
    await participant.jev.participation(event("budget"), "scope", target, [target])
    state = participant.jev.client.evaluate.await_args.args[1]
    assert len(state) <= 1500 and json.loads(state)["TARGET"]["text"]


def test_context_preserves_distinct_authors_self_quote_and_subsequent_answer():
    rows = [
        {
            "sequence": 1,
            "source": "bot-source",
            "sender_id": "111111",
            "self": True,
            "text": "I sent a cat sticker.",
        },
        {
            "sequence": 2,
            "sender_id": "222222",
            "text": "Cute sticker!",
            "reply_to": {
                "source": "bot-source",
                "sender_id": "111111",
                "text": "I sent a cat sticker.",
            },
        },
        {"sequence": 3, "sender_id": "333333", "text": "It was a cat."},
    ]
    data = json.loads(participation_state(rows[1], rows, 1500))
    assert data["TARGET"]["quoted_speaker"] == "bot"
    assert data["PRIOR_MESSAGES"][0]["speaker"] == "bot"
    assert data["FOLLOWING_MESSAGES"][0]["speaker"] != data["TARGET"]["speaker"]
    assert "111111" not in json.dumps(data)


def test_noisy_context_keeps_relevant_bot_and_same_member_before_chatter():
    rows = [
        {
            "sequence": 1,
            "source": "self",
            "sender_id": "bot",
            "self": True,
            "text": "I posted a meme.",
        }
    ]
    rows += [
        {
            "sequence": i,
            "sender_id": f"member-{i}",
            "text": "unrelated conversation " * 35,
        }
        for i in range(2, 22)
    ]
    target = {
        "sequence": 22,
        "sender_id": "alice",
        "text": "That meme was great!",
        "reply_to": {"source": "self", "sender_id": "bot", "text": "I posted a meme."},
    }
    data = json.loads(participation_state(target, rows + [target], 1500))
    assert any(m["speaker"] == "bot" for m in data["PRIOR_MESSAGES"])
    assert data["TARGET"]["text"] == target["text"]


async def test_learning_shares_evidence_and_english_rules_once(plugin):
    enable(plugin)
    original = "I prefer short answers when asking for help."
    traits = [
        {
            "kind": "behavior",
            "situation": "help requests",
            "content": content,
            "confidence": 0.8,
            "evidence": [{"id": 1, "sender": "12345678", "text": original}],
        }
        for content in ["answer briefly", "avoid repetition"]
    ]
    plugin.jev.client.evaluate = AsyncMock(return_value={"status": "error"})
    await plugin.jev.learning(
        traits,
        {
            "conversation_context": [
                {"sender": "12345678", "text": original, "for_evidence_ids": [1]}
            ]
        },
        time.monotonic() + 5,
    )
    _, state, questions = plugin.jev.client.evaluate.await_args.args
    data = json.loads(state)
    assert state.count(original) == 1 and len(data["EVIDENCE"]) == 1
    assert data["candidates"][0]["evidence"] == data["candidates"][1]["evidence"]
    assert len(questions) == 2 and data["REVIEW_RULES"].isascii()
    assert data["related_context"] == []


async def test_learning_only_lowers_confidence_and_preserves_review(plugin):
    enable(plugin)
    plugin.settings.update(activation_mode="auto", learning_review_enabled=True)
    from types import SimpleNamespace

    from tests.test_runtime import completion

    async def generate(**kwargs):
        payload = json.loads(kwargs["prompt"])
        if "candidates" in payload:
            return SimpleNamespace(
                completion_text=json.dumps(
                    {
                        "approved_ids": [
                            c["candidate_id"] for c in payload["candidates"]
                        ],
                        "flagged": [],
                    }
                )
            )
        return await completion(**kwargs)

    plugin.context.llm_generate.side_effect = generate
    scope = await sources(plugin)

    async def judge(cfg, state, questions):
        assert all(q["instructions"].isascii() for q in questions.values())
        assert "synthetic-secret" not in state
        return {"status": "ready", "answers": {key: {"noul": 0.2} for key in questions}}

    plugin.jev.client.evaluate = AsyncMock(side_effect=judge)
    result = await plugin.learn(scope)
    assert result["status"] == "success"
    state = await plugin.store.call("state", scope)
    assert state["traits"]
    assert all(
        t["confidence"] <= 0.2 and t["status"] == "pending" for t in state["traits"]
    )
    review = state["runs"][0]["review"]
    assert review["enabled"] and review["jev"]["status"] == "ready"


async def test_missing_or_failed_learning_measurements_keep_original(plugin):
    enable(plugin)
    traits = [
        {
            "kind": "behavior",
            "situation": "when asked",
            "content": "answer briefly",
            "confidence": 0.7,
            "evidence": [{"id": 1, "sender": "12345678", "text": "hello"}],
        }
    ]
    plugin.jev.client.evaluate = AsyncMock(
        return_value={"status": "ready", "answers": {"candidate_1": {"noul": 0.99}}}
    )
    checked, audit = await plugin.jev.learning(traits, {}, time.monotonic() + 5)
    assert checked[0]["confidence"] == 0.7 and traits[0]["confidence"] == 0.7
    state = plugin.jev.client.evaluate.await_args.args[1]
    assert "12345678" not in state
    plugin.jev.client.evaluate.return_value = {"status": "error"}
    checked, audit = await plugin.jev.learning(traits, {}, time.monotonic() + 5)
    assert checked is traits and audit["status"] == "error"


async def test_native_credentials_never_enter_panel_or_settings_mirror(plugin):
    enable(plugin)
    await plugin.update_settings({"jev_enabled": True})
    data = await api_call(plugin.page_api.state)
    assert "synthetic-secret" not in json.dumps(data)
    assert plugin.config["jev_connection"]["api_key"] == "synthetic-secret"
    mirror = await plugin.store.call("settings")
    assert "synthetic-secret" not in json.dumps(mirror)


@pytest.mark.parametrize(
    "value", [True, False, None, float("nan"), float("inf"), -1, 1.1, "0.5"]
)
def test_invalid_probability_never_becomes_a_gate(value):
    assert probability({"noul": value}) is None


async def test_native_http_contract_cache_and_failure_backoff():
    calls = []
    fail = False

    async def handler(request):
        nonlocal fail
        data = await request.json()
        calls.append(data)
        assert request.headers["Authorization"] == "Bearer synthetic-test"
        assert set(data) == {"model", "state", "questions"}
        if fail:
            return web.json_response({}, status=503)
        return web.json_response(
            {
                "model": "jev-latest",
                "answers": {"reply": {"type": "noul", "noul": 0}},
                "usage": {"input_tokens": 8, "output_tokens": 0},
            }
        )

    app = web.Application()
    app.router.add_post("/v1/systemone", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    client = SystemOne()
    cfg = {
        "base_url": f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1",
        "api_key": "synthetic-test",
        "model": "jev-latest",
    }
    try:
        question = {
            "reply": {"type": "noul", "instructions": "Does this message need a reply?"}
        }
        result = await client.evaluate(cfg, "synthetic", question)
        assert (
            probability(result["answers"]["reply"]) == 0
            and result["usage"]["output_tokens"] == 0
        )
        assert (await client.evaluate(cfg, "synthetic", question))["cache_hit"]
        assert len(calls) == 1
        fail = True
        await client.evaluate(cfg, "first failure", question)
        await client.evaluate(cfg, "second failure", question)
        assert (await client.evaluate(cfg, "third failure", question))[
            "status"
        ] == "backoff" and len(calls) == 3
    finally:
        await client.close()
        await runner.cleanup()


async def test_direct_priority_cancels_an_inflight_jev_participation_call(participant):
    enable(participant)
    entered = asyncio.Event()

    async def slow(*args):
        entered.set()
        await asyncio.Event().wait()

    participant.jev.client.evaluate = AsyncMock(side_effect=slow)
    task = asyncio.create_task(requests(participant, event("waiting-jev")))
    await asyncio.wait_for(entered.wait(), 1)
    direct = event("explicit")
    direct.is_at_or_wake_command = True
    await participant.prioritize_direct(direct)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not direct.is_stopped()
