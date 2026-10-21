"""Exercise semantic review boundaries, human override and atomic failure."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_ovenheart.learning import (
    LEARNING_REVIEW_SYSTEM,
    LearningFormatError,
    parse_learning_review,
)
from tests.test_runtime import completion, event, sources

pytest_plugins = ["tests.test_runtime"]


@pytest.mark.parametrize(
    "data",
    [
        {"approved_ids": [1, True], "flagged": []},
        {"approved_ids": [1, 1], "flagged": []},
        {"approved_ids": [3], "flagged": []},
        {"approved_ids": [1], "flagged": []},
        {"approved_ids": [1], "flagged": [{"candidate_id": 1, "reason": "重复"}]},
        {"approved_ids": [1], "flagged": [{"candidate_id": 2, "reason": ""}]},
        {"approved_ids": [1], "flagged": [{"candidate_id": 2, "reason": "x" * 241}]},
        {
            "approved_ids": [1],
            "flagged": [{"candidate_id": 2, "reason": "建议", "content": "rewritten"}],
        },
        {"approved_ids": [1, 2], "flagged": [], "new_traits": []},
    ],
)
def test_reviewer_must_partition_exact_candidates_without_rewriting(data):
    with pytest.raises(LearningFormatError):
        parse_learning_review(json.dumps(data), 2)


def test_review_accepts_all_flagged_and_preserves_visible_reasons():
    result = parse_learning_review(
        '```json\n{"approved_ids":[],"flagged":[{"candidate_id":2,"reason":" 条件过度扩大 "},{"candidate_id":1,"reason":"人类身份不属于机器人"}]}\n```',
        2,
    )
    assert result == {2: "条件过度扩大", 1: "人类身份不属于机器人"}
    assert parse_learning_review('{"approved_ids":[1,2],"flagged":[]}', 2) == {
        1: None,
        2: None,
    }


async def test_flagged_auto_candidates_wait_for_a_human_and_cannot_reinforce_existing_entries(
    plugin,
):
    plugin.settings.update(learning_review_enabled=True, activation_mode="auto")
    scope = await sources(plugin)

    async def generate(**kwargs):
        if kwargs.get("system_prompt") != LEARNING_REVIEW_SYSTEM:
            return await completion(**kwargs)
        candidates = json.loads(kwargs["prompt"])["candidates"]
        return SimpleNamespace(
            completion_text=json.dumps(
                {
                    "approved_ids": [
                        t["candidate_id"] for t in candidates if t["kind"] != "behavior"
                    ],
                    "flagged": [
                        {
                            "candidate_id": t["candidate_id"],
                            "reason": "条件泛化，交给人工确认",
                        }
                        for t in candidates
                        if t["kind"] == "behavior"
                    ],
                }
            )
        )

    plugin.context.llm_generate = AsyncMock(side_effect=generate)
    assert (await plugin.learn(scope))["status"] == "success"
    state = await plugin.store.call("state", scope)
    flagged = next(t for t in state["traits"] if t["kind"] == "behavior")
    assert flagged["status"] == "pending"
    assert all(
        t["status"] == "active" for t in state["traits"] if t["kind"] != "behavior"
    )
    assert state["runs"][0]["review"]["flagged"] == 1
    await plugin.store.call(
        "mutate", scope, "trait", {"id": flagged["id"], "status": "active"}
    )
    await plugin.capture(event("new-1", text="好家伙，又猜中了"))
    await plugin.capture(event("new-2", text="好家伙，真有你的"))
    assert (await plugin.learn(scope))["status"] == "success"
    existing = next(
        t
        for t in (await plugin.store.call("state", scope))["traits"]
        if t["id"] == flagged["id"]
    )
    assert (
        existing["status"] == "active"
        and existing["support_count"] == flagged["support_count"]
    )
    assert existing["evidence"] == flagged["evidence"]


async def test_review_has_one_format_retry_and_keeps_original_evidence(plugin):
    plugin.settings["learning_review_enabled"] = True
    scope = await sources(plugin)
    inputs = []

    async def generate(**kwargs):
        if kwargs.get("system_prompt") != LEARNING_REVIEW_SYSTEM:
            return await completion(**kwargs)
        payload = json.loads(kwargs["prompt"])
        inputs.append(payload)
        if len(inputs) == 1:
            return SimpleNamespace(completion_text='{"approved_ids":[],"flagged":[]}')
        return SimpleNamespace(
            completion_text=json.dumps(
                {
                    "approved_ids": [c["candidate_id"] for c in payload["candidates"]],
                    "flagged": [],
                }
            )
        )

    plugin.context.llm_generate = AsyncMock(side_effect=generate)
    assert (await plugin.learn(scope))["status"] == "success"
    assert plugin.context.llm_generate.await_count == 4
    assert inputs[0]["observations"] == inputs[1]["observations"]
    assert inputs[0]["candidates"] == inputs[1]["candidates"]
    assert "format_error" in inputs[1]


@pytest.mark.parametrize("provider_error", [False, True])
async def test_review_failure_preserves_the_whole_batch_without_secret_diagnostics(
    plugin, provider_error
):
    plugin.settings["learning_review_enabled"] = True
    scope = await sources(plugin)

    async def generate(**kwargs):
        if kwargs.get("system_prompt") != LEARNING_REVIEW_SYSTEM:
            return await completion(**kwargs)
        if provider_error:
            raise RuntimeError("api_key=SECRET_SENTINEL")
        return SimpleNamespace(completion_text='{"approved_ids":[999],"flagged":[]}')

    plugin.context.llm_generate = AsyncMock(side_effect=generate)
    outcome = await plugin.learn(scope)
    state = await plugin.store.call("state", scope)
    assert outcome["status"] == "error" and "自检" in outcome["message"]
    assert state["scope"]["pending"] == 2 and not state["traits"]
    assert "SECRET_SENTINEL" not in json.dumps(state)
    assert plugin.context.llm_generate.await_count == (3 if provider_error else 4)


async def test_disabling_review_in_flight_discards_outdated_verdicts(plugin):
    plugin.settings["learning_review_enabled"] = True
    scope = await sources(plugin)

    async def generate(**kwargs):
        if kwargs.get("system_prompt") != LEARNING_REVIEW_SYSTEM:
            return await completion(**kwargs)
        plugin.settings["learning_review_enabled"] = False
        return SimpleNamespace(
            completion_text=json.dumps(
                {
                    "approved_ids": [
                        c["candidate_id"]
                        for c in json.loads(kwargs["prompt"])["candidates"]
                    ],
                    "flagged": [],
                }
            )
        )

    plugin.context.llm_generate = AsyncMock(side_effect=generate)
    result = await plugin.learn(scope)
    assert result["status"] == "error" and "开关" in result["message"]
    state = await plugin.store.call("state", scope)
    assert not state["traits"] and state["scope"]["pending"] == 2


async def test_review_uses_the_remaining_shared_deadline(plugin):
    scope = await sources(plugin)
    batch = await plugin.store.call("batch", scope, 30)
    trait = {
        "kind": "expression",
        "situation": "闲聊",
        "content": "轻松接话",
        "evidence": [{"id": m["id"]} for m in batch],
    }
    cancelled = asyncio.Event()

    async def slow(**kwargs):
        try:
            await asyncio.sleep(3)
        finally:
            cancelled.set()

    plugin.context.llm_generate = slow
    base = {
        "identity_anchor": "小夏",
        "profile": {},
        "observations": batch,
        "conversation_context": [],
        "memory_context": [],
    }
    started = time.monotonic()
    with pytest.raises(ValueError, match="自检超时"):
        await plugin._review_learning([trait], base, "fixture", started + 0.02)
    assert time.monotonic() - started < 0.5 and cancelled.is_set()


async def test_empty_extraction_does_not_spend_a_review_request(plugin):
    plugin.settings["learning_review_enabled"] = True
    scope = await sources(plugin)
    plugin.context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(completion_text='{"traits":[]}')
    )
    assert (await plugin.learn(scope))["status"] == "success"
    assert plugin.context.llm_generate.await_count == 2
    state = await plugin.store.call("state", scope)
    assert state["runs"][0]["review"]["candidates"] == []


async def test_transaction_rechecks_the_review_switch_before_writing(
    plugin, monkeypatch
):
    plugin.settings["learning_review_enabled"] = True
    scope = await sources(plugin)

    async def generate(**kwargs):
        if kwargs.get("system_prompt") != LEARNING_REVIEW_SYSTEM:
            return await completion(**kwargs)
        return SimpleNamespace(
            completion_text=json.dumps(
                {
                    "approved_ids": [
                        c["candidate_id"]
                        for c in json.loads(kwargs["prompt"])["candidates"]
                    ],
                    "flagged": [],
                }
            )
        )

    original = plugin.store.finish_run

    def switch_before_commit(db, *args):
        if not args[6]:
            plugin.store.settings(db, {"learning_review_enabled": False})
        return original(db, *args)

    monkeypatch.setattr(plugin.store, "finish_run", switch_before_commit)
    plugin.context.llm_generate = AsyncMock(side_effect=generate)
    result = await plugin.learn(scope)
    state = await plugin.store.call("state", scope)
    assert result["status"] == "error" and "提交前" in result["message"]
    assert state["scope"]["pending"] == 2 and not state["traits"]


async def test_review_flags_do_not_forget_aliases_of_a_human_rejection(plugin):
    plugin.settings.update(
        activation_mode="auto",
        learn_personality=False,
        learn_expression=False,
        learn_jargon=False,
    )
    scope = await sources(plugin)
    await plugin.learn(scope)
    rejected = (await plugin.store.call("state", scope))["traits"][0]
    await plugin.store.call(
        "mutate", scope, "trait", {"id": rejected["id"], "status": "rejected"}
    )
    plugin.settings["learning_review_enabled"] = True
    proposed = {
        "kind": "behavior",
        "situation": "轻松聊天时",
        "content": "换个说法先接住情绪",
        "confidence": 0.99,
    }

    async def generate(**kwargs):
        payload = json.loads(kwargs["prompt"])
        if kwargs["system_prompt"] == LEARNING_REVIEW_SYSTEM:
            return SimpleNamespace(
                completion_text='{"approved_ids":[],"flagged":[{"candidate_id":1,"reason":"原文支持不足"}]}'
            )
        candidate = proposed | {
            "evidence_ids": [m["id"] for m in payload["observations"]]
        }
        if plugin.settings["learning_review_enabled"]:
            candidate["merge_id"] = rejected["id"]
        return SimpleNamespace(completion_text=json.dumps({"traits": [candidate]}))

    plugin.context.llm_generate = AsyncMock(side_effect=generate)
    for turn in (1, 2):
        for number in (1, 2):
            await plugin.capture(
                event(f"alias-{turn}-{number}", text="好家伙，真有你的")
            )
        assert (await plugin.learn(scope))["status"] == "success"
        state = await plugin.store.call("state", scope)
        assert len(state["traits"]) == 1 and state["traits"][0]["status"] == "rejected"
        plugin.settings["learning_review_enabled"] = False
