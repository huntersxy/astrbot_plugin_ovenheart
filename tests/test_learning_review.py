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


def test_review_distinguishes_missing_fields_from_extra_content():
    with pytest.raises(LearningFormatError, match="缺少字段：reason"):
        parse_learning_review('{"approved_ids":[],"flagged":[{"candidate_id":1}]}', 1)
    with pytest.raises(LearningFormatError, match="多余字段") as error:
        parse_learning_review(
            '{"approved_ids":[],"flagged":[{"candidate_id":1,"reason":"证据不足","private-model-field":"private-content"}]}',
            1,
        )
    assert "private" not in str(error.value)


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
    phase = [
        call.kwargs
        for call in plugin.context.llm_generate.await_args_list
        if call.kwargs.get("system_prompt") == LEARNING_REVIEW_SYSTEM
    ]
    assert "task" not in inputs[0] and "task" not in inputs[1]
    assert inputs[1]["correction_task"]
    assert phase[0]["system_prompt"] == phase[1]["system_prompt"]
    assert phase[1]["prompt"].startswith(phase[0]["prompt"][:-1] + ",")


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


async def test_review_only_receives_memories_linked_to_cited_observations(plugin):
    plugin.context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(
            completion_text='{"approved_ids":[1],"flagged":[]}'
        )
    )
    trait = {
        "kind": "expression",
        "situation": "倾诉时",
        "content": "先听对方说",
        "evidence": [{"id": 1}],
    }
    base = {
        "identity_anchor": "小夏",
        "profile": {},
        "observations": [
            {"id": 1, "sender": "alice", "text": "我听着"},
            {"id": 2, "sender": "bob", "text": "我来处理"},
        ],
        "conversation_context": [],
        "memory_context": [
            {"id": "own", "content": "相关语境", "for_evidence_ids": [1]},
            {"id": "other", "content": "无关用户记忆", "for_evidence_ids": [2]},
            {"id": "legacy", "content": "兼容旧的无关联语境"},
        ],
    }
    await plugin._review_learning([trait], base, "fixture", time.monotonic() + 2)
    payload = json.loads(plugin.context.llm_generate.await_args.kwargs["prompt"])
    assert {m["id"] for m in payload["memory_context"]} == {"own", "legacy"}
    assert len(base["memory_context"]) == 3


async def test_learning_and_review_keep_original_capture_times(plugin):
    scope = await sources(plugin)
    batch = await plugin.store.call("batch", scope, 30)
    seen = []
    plugin.settings["learning_review_enabled"] = True

    async def generate(**kwargs):
        payload = json.loads(kwargs["prompt"])
        seen.append(payload)
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

    plugin.context.llm_generate = generate
    result = await plugin.learn(scope)
    assert result["status"] == "success" and len(seen) == 3
    captured = {m["id"]: m["created"] for m in batch}
    assert all(
        {m["id"]: m["created"] for m in payload["observations"]} == captured
        for payload in seen
    )


async def test_review_time_neighbors_exclude_gaps_and_unknown_times_but_keep_quotes(
    plugin,
):
    observations = [
        {"id": 1, "sender": "peer", "text": "旧话题", "created": 1000},
        {"id": 2, "sender": "mentor", "text": "这次我先听你说", "created": 5000},
        {"id": 3, "sender": "peer", "text": "五分钟内", "created": 5300},
        {"id": 4, "sender": "peer", "text": "超过五分钟", "created": 5301},
        {"id": 5, "sender": "mentor", "text": "缺少采集时间"},
    ]
    quoted = {
        "relation": "reply_to",
        "sender": "peer",
        "text": "明确引用的旧问题",
        "created": 1000,
        "for_evidence_ids": [2],
    }
    plugin.context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(
            completion_text='{"approved_ids":[1],"flagged":[]}'
        )
    )
    await plugin._review_learning(
        [
            {
                "kind": "behavior",
                "situation": "倾诉时",
                "content": "先倾听",
                "evidence": [{"id": 2}, {"id": 5}],
            }
        ],
        {
            "identity_anchor": "小夏",
            "profile": {},
            "observations": observations,
            "conversation_context": [quoted],
            "memory_context": [],
        },
        "fixture",
        time.monotonic() + 2,
    )
    payload = json.loads(plugin.context.llm_generate.await_args.kwargs["prompt"])
    assert payload["conversation_context"][0] == quoted
    neighbors = payload["conversation_context"][1:]
    assert len(neighbors) == 1
    assert neighbors[0]["observation_id"] == 3
    assert neighbors[0]["created"] == 5300
    assert neighbors[0]["for_evidence_ids"] == [2]
    assert {m["id"] for m in payload["observations"]} == {2, 5}


async def test_native_learning_refines_an_unlocked_habit_only_after_review(plugin):
    plugin.settings.update(
        activation_mode="auto", learning_review_enabled=True, learn_jargon=False
    )
    reviewed = []

    async def generate(**kwargs):
        payload = json.loads(kwargs["prompt"])
        if "candidates" in payload:
            reviewed.append(payload["candidates"])
            return SimpleNamespace(completion_text='{"approved_ids":[1],"flagged":[]}')
        known = payload["known_traits"]
        candidate = {
            "kind": "expression",
            "situation": "朋友轻松调侃时",
            "content": "双方愿意玩笑时，用简短反问接住语气"
            if known
            else "简短反问接住玩笑",
            "confidence": 0.95,
            "evidence_ids": [m["id"] for m in payload["observations"]],
        }
        if known:
            candidate.update(merge_id=known[0]["id"], refine=True)
        return SimpleNamespace(completion_text=json.dumps({"traits": [candidate]}))

    plugin.context.llm_generate = AsyncMock(side_effect=generate)
    scope = await sources(plugin)
    assert (await plugin.learn(scope))["status"] == "success"
    first = (await plugin.store.call("state", scope))["traits"][0]
    await plugin.capture(event("refine-1", text="哈哈，还是你会接话"))
    await plugin.capture(event("refine-2", text="愿意开玩笑时你这句反问挺自然"))
    assert (await plugin.learn(scope))["status"] == "success"
    current = (await plugin.store.call("state", scope))["traits"][0]
    assert current["id"] == first["id"] and current["meaning_stage"] == 1
    assert current["content"].startswith("双方愿意玩笑时")
    assert current["status"] == "active" and not current["manual"]
    assert reviewed[-1][0]["refine"] is True
    assert reviewed[-1][0]["merge_target"]["content"] == first["content"]
    assert plugin.context.llm_generate.await_count == 4


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


@pytest.mark.parametrize("approved", [True, False])
async def test_explicit_merge_review_receives_the_stored_target_and_preserves_it_when_flagged(
    plugin, approved
):
    plugin.settings.update(
        activation_mode="auto",
        learn_personality=False,
        learn_expression=False,
        learn_jargon=False,
    )
    scope = await sources(plugin)
    await plugin.learn(scope)
    before = (await plugin.store.call("state", scope))["traits"][0]
    plugin.settings["learning_review_enabled"] = True

    async def generate(**kwargs):
        payload = json.loads(kwargs["prompt"])
        if kwargs["system_prompt"] == LEARNING_REVIEW_SYSTEM:
            candidate = payload["candidates"][0]
            assert candidate["merge_target"] == {
                k: before[k] for k in ("id", "kind", "situation", "content", "status")
            }
            assert "confidence" not in candidate
            assert set(candidate["evidence_ids"]).isdisjoint(
                e["id"] for e in before["evidence"]
            )
            return SimpleNamespace(
                completion_text=json.dumps(
                    {
                        "approved_ids": [1] if approved else [],
                        "flagged": []
                        if approved
                        else [
                            {"candidate_id": 1, "reason": "有证据但不属于该目标习惯"}
                        ],
                    }
                )
            )
        return SimpleNamespace(
            completion_text=json.dumps(
                {
                    "traits": [
                        {
                            "kind": "behavior",
                            "situation": "处理请求时",
                            "content": "先确认具体对象再处理",
                            "confidence": 0.99,
                            "merge_id": before["id"],
                            "evidence_ids": [m["id"] for m in payload["observations"]],
                        }
                    ]
                }
            )
        )

    plugin.context.llm_generate = AsyncMock(side_effect=generate)
    for i in (1, 2):
        await plugin.capture(event(f"merge-{i}", text="先确认对象再继续"))
    assert (await plugin.learn(scope))["status"] == "success"
    state = await plugin.store.call("state", scope)
    assert len(state["traits"]) == 1
    after = state["traits"][0]
    if approved:
        assert after["support_count"] == before["support_count"] + 1
    else:
        assert after == before
    assert (
        state["runs"][0]["review"]["candidates"][0]["merge_target"]["id"]
        == before["id"]
    )


@pytest.mark.parametrize(
    "change",
    [
        {"content": "人工改成完全不同的习惯"},
        {"situation": "人工限制为另一种情境"},
        {"status": "rejected"},
    ],
)
async def test_target_changes_during_review_preserve_human_edits_and_the_whole_batch(
    plugin, change
):
    plugin.settings.update(
        activation_mode="auto",
        learn_personality=False,
        learn_expression=False,
        learn_jargon=False,
    )
    scope = await sources(plugin)
    await plugin.learn(scope)
    before = (await plugin.store.call("state", scope))["traits"][0]
    plugin.settings["learning_review_enabled"] = True

    async def generate(**kwargs):
        payload = json.loads(kwargs["prompt"])
        if kwargs["system_prompt"] == LEARNING_REVIEW_SYSTEM:
            await plugin.store.call(
                "mutate", scope, "trait", {"id": before["id"]} | change
            )
            return SimpleNamespace(completion_text='{"approved_ids":[1],"flagged":[]}')
        return SimpleNamespace(
            completion_text=json.dumps(
                {
                    "traits": [
                        {
                            "kind": "behavior",
                            "situation": "轻松聊天时",
                            "content": "先回应感受，不急着给建议",
                            "confidence": 0.99,
                            "merge_id": before["id"],
                            "evidence_ids": [m["id"] for m in payload["observations"]],
                        }
                    ]
                }
            )
        )

    plugin.context.llm_generate = AsyncMock(side_effect=generate)
    for i in (1, 2):
        await plugin.capture(event(f"changed-target-{i}", text="先接住感受"))
    result = await plugin.learn(scope)
    state = await plugin.store.call("state", scope)
    assert result["status"] == "error" and "合并目标" in result["message"]
    assert state["scope"]["pending"] == 2 and len(state["traits"]) == 1
    after = state["traits"][0]
    assert after["support_count"] == before["support_count"]
    assert after["evidence"] == before["evidence"]
    assert all(after[k] == v for k, v in change.items())


async def test_two_approved_candidates_can_share_a_target_that_activates_in_the_batch(
    plugin,
):
    plugin.settings.update(
        learn_personality=False, learn_expression=False, learn_jargon=False
    )
    scope = await sources(plugin)
    await plugin.learn(scope)
    before = (await plugin.store.call("state", scope))["traits"][0]
    assert before["status"] == "pending"
    plugin.settings.update(activation_mode="auto", learning_review_enabled=True)

    async def generate(**kwargs):
        payload = json.loads(kwargs["prompt"])
        if kwargs["system_prompt"] == LEARNING_REVIEW_SYSTEM:
            return SimpleNamespace(
                completion_text='{"approved_ids":[1,2],"flagged":[]}'
            )
        return SimpleNamespace(
            completion_text=json.dumps(
                {
                    "traits": [
                        {
                            "kind": "behavior",
                            "situation": "轻松聊天时",
                            "content": f"先回应感受，表达方式{n}",
                            "confidence": 0.99,
                            "merge_id": before["id"],
                            "evidence_ids": [m["id"] for m in payload["observations"]],
                        }
                        for n in (1, 2)
                    ]
                }
            )
        )

    plugin.context.llm_generate = AsyncMock(side_effect=generate)
    for i in (1, 2):
        await plugin.capture(event(f"shared-target-{i}", text="先回应感受"))
    assert (await plugin.learn(scope))["status"] == "success"
    state = await plugin.store.call("state", scope)
    assert state["traits"][0]["status"] == "active"
    assert state["traits"][0]["support_count"] == before["support_count"] + 1


async def test_conflicting_alias_and_reviewed_merge_roll_back_even_earlier_new_candidates(
    plugin,
):
    plugin.settings.update(
        activation_mode="auto",
        learn_personality=False,
        learn_expression=False,
        learn_jargon=False,
    )
    scope = await sources(plugin)

    async def seed(**kwargs):
        payload = json.loads(kwargs["prompt"])
        return SimpleNamespace(
            completion_text=json.dumps(
                {
                    "traits": [
                        {
                            "kind": "behavior",
                            "situation": "日常闲聊时",
                            "content": content,
                            "confidence": 0.99,
                            "evidence_ids": [m["id"] for m in payload["observations"]],
                        }
                        for content in ("先回应情绪", "任务不清楚时先确认材料")
                    ]
                }
            )
        )

    plugin.context.llm_generate = seed
    assert (await plugin.learn(scope))["status"] == "success"
    before = (await plugin.store.call("state", scope))["traits"]
    first, second = before
    plugin.settings["learning_review_enabled"] = True

    async def generate(**kwargs):
        payload = json.loads(kwargs["prompt"])
        if kwargs["system_prompt"] == LEARNING_REVIEW_SYSTEM:
            return SimpleNamespace(
                completion_text='{"approved_ids":[1,2],"flagged":[]}'
            )
        common = {
            "kind": "behavior",
            "confidence": 0.99,
            "evidence_ids": [m["id"] for m in payload["observations"]],
        }
        return SimpleNamespace(
            completion_text=json.dumps(
                {
                    "traits": [
                        common | {"situation": "新情境", "content": "这个候选本应新增"},
                        common
                        | {k: first[k] for k in ("situation", "content")}
                        | {"merge_id": second["id"]},
                    ]
                }
            )
        )

    plugin.context.llm_generate = generate
    for i in (1, 2):
        await plugin.capture(event(f"alias-conflict-{i}", text="新的一批素材"))
    result = await plugin.learn(scope)
    state = await plugin.store.call("state", scope)
    assert result["status"] == "error" and "别名" in result["message"]
    assert state["scope"]["pending"] == 2 and state["traits"] == before


async def test_review_keeps_uncited_neighbor_requests_as_context_without_new_evidence(
    plugin,
):
    observations = [
        {
            "id": i,
            "sender": "mentor" if i in (3, 7) else "peer",
            "text": f"消息{i}" + "x" * 450,
            "created": 1700000000 + i,
        }
        for i in range(1, 22)
    ]
    original = {
        "sender": "outside-target",
        "text": "我只想倾诉",
        "for_evidence_ids": [3],
    }
    trait = {
        "kind": "behavior",
        "situation": "对方只想倾诉时",
        "content": "先听对方说完",
        "evidence": [{"id": 3}, {"id": 7}],
    }
    seen = []

    async def generate(**kwargs):
        seen.append(json.loads(kwargs["prompt"]))
        return SimpleNamespace(completion_text='{"approved_ids":[1],"flagged":[]}')

    plugin.context.llm_generate = generate
    base = {
        "identity_anchor": "小夏",
        "profile": {},
        "observations": observations,
        "conversation_context": [original],
        "memory_context": [],
        "known_traits": [],
    }
    reviewed, _ = await plugin._review_learning(
        [trait], base, "fixture", time.monotonic() + 1
    )
    payload = seen[0]
    assert {m["id"] for m in payload["observations"]} == {3, 7}
    assert payload["conversation_context"][0] == original
    neighbors = {m["observation_id"]: m for m in payload["conversation_context"][1:]}
    assert 2 in neighbors and neighbors[2]["for_evidence_ids"] == [3]
    assert neighbors[5]["for_evidence_ids"] == [3, 7]
    assert 21 not in neighbors and all(
        len(m["text"]) <= 400 for m in neighbors.values()
    )
    assert reviewed[0]["evidence"] == [{"id": 3}, {"id": 7}]
    assert base["observations"] == observations and base["conversation_context"] == [
        original
    ]


async def test_review_neighbor_context_retains_the_existing_total_bound(plugin):
    observations = [
        {"id": i, "sender": "person", "text": "普通消息", "created": 1700000000 + i}
        for i in range(1, 51)
    ]
    trait = {
        "kind": "expression",
        "situation": "闲聊",
        "content": "自然接话",
        "evidence": [{"id": i} for i in range(3, 49, 4)],
    }
    seen = []

    async def generate(**kwargs):
        seen.append(json.loads(kwargs["prompt"]))
        return SimpleNamespace(completion_text='{"approved_ids":[1],"flagged":[]}')

    plugin.context.llm_generate = generate
    await plugin._review_learning(
        [trait],
        {
            "identity_anchor": "小夏",
            "profile": {},
            "observations": observations,
            "conversation_context": [],
            "memory_context": [],
            "known_traits": [],
        },
        "fixture",
        time.monotonic() + 1,
    )
    assert len(seen[0]["conversation_context"]) == 16
    assert all(m["for_evidence_ids"] for m in seen[0]["conversation_context"])


async def test_context_is_not_lost_when_a_different_candidate_cites_the_request(plugin):
    seen = []

    async def generate(**kwargs):
        seen.append(json.loads(kwargs["prompt"]))
        return SimpleNamespace(completion_text='{"approved_ids":[1,2],"flagged":[]}')

    plugin.context.llm_generate = generate
    await plugin._review_learning(
        [
            {
                "kind": "behavior",
                "situation": "只想倾诉时",
                "content": "先倾听",
                "evidence": [{"id": 2}, {"id": 4}],
            },
            {
                "kind": "expression",
                "situation": "求助时",
                "content": "直说需求",
                "evidence": [{"id": 1}, {"id": 3}],
            },
        ],
        {
            "identity_anchor": "小夏",
            "profile": {},
            "observations": [
                {
                    "id": i,
                    "sender": "peer" if i % 2 else "mentor",
                    "text": "只想倾诉" if i % 2 else "先听你说",
                    "created": 1700000000 + i,
                }
                for i in range(1, 5)
            ],
            "conversation_context": [],
            "memory_context": [],
            "known_traits": [],
        },
        "fixture",
        time.monotonic() + 1,
    )
    contexts = {m["observation_id"]: m for m in seen[0]["conversation_context"]}
    assert 2 in contexts[1]["for_evidence_ids"]
    assert 4 in contexts[3]["for_evidence_ids"]
    assert all(
        m["observation_id"] not in m["for_evidence_ids"] for m in contexts.values()
    )


async def test_relevance_ranking_preserves_an_older_human_rejection_outside_recent_entries(
    plugin,
):
    plugin.settings.update(
        learn_personality=False, learn_expression=False, learn_jargon=False
    )
    scope = await sources(plugin)
    await plugin.learn(scope)
    target = (await plugin.store.call("state", scope))["traits"][0]
    await plugin.store.call(
        "mutate", scope, "trait", {"id": target["id"], "status": "rejected"}
    )
    seed_number = 0

    async def seed(**kwargs):
        payload = json.loads(kwargs["prompt"])
        return SimpleNamespace(
            completion_text=json.dumps(
                {
                    "traits": [
                        {
                            "kind": "behavior",
                            "situation": "其他任务情境",
                            "content": f"其他资料习惯 {seed_number}-{i}",
                            "confidence": 0.99,
                            "evidence_ids": [m["id"] for m in payload["observations"]],
                        }
                        for i in range(12)
                    ]
                }
            )
        )

    plugin.context.llm_generate = seed
    for seed_number in range(4):
        for i in (1, 2):
            await plugin.capture(event(f"older-{seed_number}-{i}", text="其他任务资料"))
        assert (await plugin.learn(scope))["status"] == "success"
    rows = (await plugin.store.call("state", scope))["traits"]
    assert next(i for i, t in enumerate(rows) if t["id"] == target["id"]) >= 40
    plugin.settings["learning_review_enabled"] = True

    async def generate(**kwargs):
        payload = json.loads(kwargs["prompt"])
        if kwargs["system_prompt"] == LEARNING_REVIEW_SYSTEM:
            assert payload["candidates"][0]["merge_target"]["status"] == "rejected"
            return SimpleNamespace(completion_text='{"approved_ids":[1],"flagged":[]}')
        offered = next(t for t in payload["known_traits"] if t["id"] == target["id"])
        assert offered["status"] == "rejected"
        return SimpleNamespace(
            completion_text=json.dumps(
                {
                    "traits": [
                        {
                            "kind": "behavior",
                            "situation": "情绪交流时",
                            "content": "先接住对方的情绪",
                            "confidence": 0.99,
                            "merge_id": target["id"],
                            "evidence_ids": [m["id"] for m in payload["observations"]],
                        }
                    ]
                }
            )
        )

    plugin.context.llm_generate = generate
    for i in (1, 2):
        await plugin.capture(event(f"old-rejection-{i}", text=target["content"]))
    assert (await plugin.learn(scope))["status"] == "success"
    state = await plugin.store.call("state", scope)
    after = next(t for t in state["traits"] if t["id"] == target["id"])
    assert after["status"] == "rejected" and len(state["traits"]) == 49
    assert after["support_count"] == target["support_count"]


async def test_debug_review_logs_actual_self_check_input_and_output(
    plugin, monkeypatch
):
    from astrbot_plugin_ovenheart.main import logger

    records = []
    monkeypatch.setattr(logger, "info", lambda *args: records.append(args))
    plugin.settings.update(debug_enabled=True, learning_review_enabled=True)

    async def generate(**kwargs):
        if kwargs.get("system_prompt") != LEARNING_REVIEW_SYSTEM:
            return await completion(**kwargs)
        payload = json.loads(kwargs["prompt"])
        return SimpleNamespace(
            completion_text=json.dumps(
                {
                    "approved_ids": [c["candidate_id"] for c in payload["candidates"]],
                    "flagged": [],
                }
            )
        )

    plugin.context.llm_generate.side_effect = generate
    scope = await sources(plugin)
    assert (await plugin.learn(scope))["status"] == "success"
    entries = [
        (args[1], json.loads(args[2]))
        for args in records
        if args[0].startswith("OvenHeart debug")
    ]
    review = next(body for stage, body in entries if stage == "review_request")
    actual = next(
        call.kwargs
        for call in plugin.context.llm_generate.await_args_list
        if call.kwargs["system_prompt"] == LEARNING_REVIEW_SYSTEM
    )
    assert review["prompt"] == json.loads(actual["prompt"])
    assert review["system"] == actual["system_prompt"]
    assert any(
        stage == "review_response" and json.loads(body["completion"])["approved_ids"]
        for stage, body in entries
    )
