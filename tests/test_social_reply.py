"""Exercise social decisions through the real retrieval, request and memory hooks."""

import asyncio
import json
import sqlite3
from types import MethodType
from unittest.mock import AsyncMock

from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.message import TextPart

from tests.test_jev_owned import enable
from tests.test_participation import requests
from tests.test_retrieval import TRAITS
from tests.test_runtime import event

pytest_plugins = [
    "tests.test_participation",
    "tests.test_retrieval",
    "tests.test_planner_memory",
    "tests.test_reply_memory_binding",
]


def judged_habits():
    return {
        "status": "ready",
        "answers": {
            "habit_1": {"noul": 0.95},
            "habit_2": {"noul": 0.05},
            "reply_act": {"choice": "comfort", "confidence": 0.9},
            "reply_style": {"choice": "natural", "confidence": 0.9},
        },
    }


async def test_typed_selection_skips_chat_and_rerank_and_preserves_no_selection(
    participant, retrieval
):
    retriever, _ = retrieval
    enable(participant)
    participant.jev.client.evaluate = AsyncMock(return_value=judged_habits())
    retriever.rerank_pool = AsyncMock(side_effect=AssertionError("extra rerank"))
    settings = participant.settings | {
        "selection_mode": "contextual",
        "rerank_provider_id": "configured",
    }
    result = await retriever.select(
        "scope-a",
        TRAITS,
        "朋友情绪低落，我想陪她聊聊",
        settings,
        contextual_judge=participant.jev.select_habits,
    )
    assert [t["id"] for t in result["traits"]] == [1]
    assert result["selector_engine"] == "jev"
    assert result["expression_intent"]["reply_act"] == "陪伴倾听"
    retriever.rerank_pool.assert_not_awaited()
    participant.context.llm_generate.assert_not_awaited()
    participant.jev.client.evaluate.return_value["answers"]["habit_1"]["noul"] = 0.1
    result = await retriever.select(
        "scope-a",
        TRAITS,
        "这次没有适合的习惯",
        settings,
        contextual_judge=participant.jev.select_habits,
    )
    assert result["traits"] == [] and result["mode"] == "contextual"


async def test_direct_native_request_receives_one_typed_social_plan(participant):
    enable(participant)
    participant.settings.update(selection_mode="contextual", guidance_delivery="cached")
    incoming = event("direct-social", text="今天很难受，想找你说说话")
    incoming.is_at_or_wake_command = True
    await participant.capture(incoming)
    scope, _, _ = await participant.resolve_scope(incoming)
    with sqlite3.connect(participant.store.path) as db:
        for trait in TRAITS:
            db.execute(
                "INSERT INTO traits(id,scope,kind,situation,content,confidence,evidence,status,fingerprint,created,updated) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    trait["id"],
                    scope,
                    trait["kind"],
                    trait["situation"],
                    trait["content"],
                    trait["confidence"],
                    "[]",
                    "active",
                    str(trait["id"]),
                    1,
                    1,
                ),
            )
    participant.jev.client.evaluate = AsyncMock(return_value=judged_habits())
    request = ProviderRequest(
        prompt=incoming.get_message_str(), system_prompt="固定人格"
    )
    await participant.apply_persona(incoming, request)
    await participant.apply_participation(incoming, request)
    assert request.system_prompt == "固定人格"
    body = "\n".join(p.text for p in request.extra_user_content_parts)
    assert "陪伴倾听" in body and "先倾听，不急着给建议" in body
    assert "轻松调侃游戏中的操作" not in body
    assert all(
        p.model_dump_for_context()["_no_save"] for p in request.extra_user_content_parts
    )
    participant.jev.client.evaluate.assert_awaited_once()
    participant.context.llm_generate.assert_not_awaited()


async def test_failed_fast_judgment_offers_optional_candidates_without_claiming_selection(
    participant, retrieval
):
    retriever, _ = retrieval
    enable(participant)
    participant.jev.client.evaluate = AsyncMock(return_value={"status": "error"})
    result = await retriever.select(
        "scope-a",
        TRAITS,
        "今天心情不好",
        participant.settings | {"selection_mode": "contextual"},
        contextual_judge=participant.jev.select_habits,
    )
    assert result["mode"] == "contextual_inline"
    assert result["selector_engine"] == "reply_model"
    assert result["traits"] and "不代表已采用" in result["message"]
    participant.context.llm_generate.assert_not_awaited()


async def test_jev_plans_with_authorized_shared_memory_and_concrete_intent(
    planner_memory,
):
    plugin, engine, _ = planner_memory
    enable(plugin)
    engine.search_memories.return_value[0].metadata = {
        "participants": ["alice"],
        "key_facts": ["这位成员希望交流时直接说出不同看法"],
        "create_time": "2026-10-24T12:00:00+08:00",
    }

    async def judge(cfg, state, questions):
        data = json.loads(state)
        assert data["memory_context"][0]["for_sequences"] == [1]
        assert "OLDER_SHARED_BACKGROUND" in data["memory_context"][0]["content"]
        assert data["memory_context"][0]["social"]["participants"] == ["member_1"]
        assert data["memory_context"][0]["social"]["key_facts"]
        assert "NOT owners" in data["RULES"]
        return {
            "status": "ready",
            "answers": {
                "action": {
                    "choice": str(data["eligible_targets"][-1]),
                    "confidence": 0.9,
                },
                "should_reply": {"noul": 0.9},
                "reply_act": {"choice": "disagree", "confidence": 0.9},
                "reply_style": {"choice": "brief", "confidence": 0.9},
            },
        }

    plugin.jev.client.evaluate = AsyncMock(side_effect=judge)
    incoming = event("social-memory", text="你也觉得上次那件事是我不对吗")
    assert len(await requests(plugin, incoming)) == 1
    plan = incoming.get_extra("ovenheart_decision")
    assert plan["expression_intent"]["reply_act"] == "表达不同意见"
    assert "表达不同意见" in plan["intent"]
    assert plan["expression_intent"]["focus"] == incoming.get_message_str()
    engine.search_memories.assert_awaited_once()
    plugin.jev.client.evaluate.assert_awaited_once()
    plugin.context.llm_generate.assert_not_awaited()


async def test_memory_context_stays_bounded_with_native_participants(planner_memory):
    plugin, engine, _ = planner_memory
    enable(plugin)
    plugin.settings["jev_max_state_chars"] = 1500
    engine.search_memories.return_value[0].metadata = {"participants": ["alice"]}
    plugin.jev.client.evaluate = AsyncMock(return_value={"status": "error"})
    assert not await requests(plugin, event("small-social-budget", text="刚才说到哪了"))
    state = plugin.jev.client.evaluate.await_args.args[1]
    assert len(state) <= 1500 and json.loads(state)["eligible_targets"]


async def test_jev_does_not_read_unselected_personal_memory(planner_memory):
    plugin, engine, policy = planner_memory
    enable(plugin)
    policy["filtering_settings.memory_scope_mode"] = "user"
    plugin.jev.client.evaluate = AsyncMock(return_value={"status": "error"})
    assert not await requests(plugin, event("personal-memory"))
    state = json.loads(plugin.jev.client.evaluate.await_args.args[1])
    assert state["memory_context"] == []
    engine.search_memories.assert_not_awaited()


async def test_native_memory_hook_adds_temporary_social_context_and_respects_pause(
    native_memory,
):
    plugin, owner, _, entries = native_memory

    async def native_recall(self, source, req):
        self.calls.append(("recall", source, req))
        req.extra_user_content_parts.append(
            TextPart(
                text="<RAG-Faiss-Memory>真实共同话题</RAG-Faiss-Memory>"
            ).mark_as_temp()
        )

    entries[0].handler = MethodType(native_recall, owner)
    assert plugin.memory.install()
    incoming = event("native-social")
    request = ProviderRequest(prompt="本轮问题", system_prompt="固定人格")
    await entries[0].handler(incoming, request)
    assert owner.calls[0][1] is incoming
    assert request.system_prompt == "固定人格" and not request.contexts
    assert len(request.extra_user_content_parts) == 2
    social = request.extra_user_content_parts[-1]
    assert social.text.startswith("[OvenHeart social memory]")
    assert social.model_dump_for_context()["_no_save"]
    plugin.settings["apply_enabled"] = False
    paused = ProviderRequest(prompt="暂停应用")
    await entries[0].handler(incoming, paused)
    assert len(paused.extra_user_content_parts) == 1


async def test_interleaved_followup_uses_short_window_and_still_can_observe(
    participant,
):
    participant.settings.update(
        participation_min_messages=6, participation_silence_seconds=8
    )
    direct = event("answered", text="第一步该怎么做")
    await participant.capture(direct)
    direct.set_result(direct.plain_result("先检查这一步"))
    await participant.record_delivery(direct)
    assert not await requests(
        participant, event("aside", sender="bob", text="我说件别的事")
    )

    async def quiet(changed, duration):
        assert duration == 2
        return False

    participant.participation.quiet_period = AsyncMock(side_effect=quiet)
    follow = event("continue", text="第一步做完了，接下来呢")
    assert len(await asyncio.wait_for(requests(participant, follow), 1)) == 1
    assert follow.get_extra("ovenheart_decision")["followup"] is True
    participant.context.llm_generate.assert_awaited_once()
