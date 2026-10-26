"""Use the actual ported fact ledger/profile service, SQLite and native events."""

import asyncio
from types import SimpleNamespace

import pytest

from astrbot_plugin_ovenheart.person_memory import PersonMemory
from tests.test_participation import requests
from tests.test_runtime import api_call, event

pytest_plugins = ["tests.test_participation"]


def fact(
    source,
    quote,
    value,
    *,
    key="occupation",
    section="identity_settings",
    stability="stable",
    corrects=(),
):
    return {
        "key": key,
        "value": value,
        "section": section,
        "stability": stability,
        "cardinality": "single",
        "polarity": "positive",
        "corrects": list(corrects),
        "evidence": [{"source_id": source, "quote": quote}],
    }


@pytest.fixture
async def person(participant):
    participant.settings.update(
        person_memory_enabled=True,
        person_memory_min_messages=30,
        person_memory_interval=0,
    )
    return participant


async def source(plugin, identifier, text, sender="alice", session="123"):
    incoming = event(identifier, text=text, sender=sender, session=session)
    await plugin.capture(incoming)
    scope, persona, _ = await plugin.resolve_scope(incoming)
    plugin.participation.sources[scope, identifier] = incoming
    return (
        incoming,
        scope,
        persona,
        {
            "source": identifier,
            "sender_id": sender,
        },
    )


async def remember(plugin, record, *facts):
    incoming, scope, persona, message = record
    return await plugin.person_memory.remember(
        incoming, scope, persona, message, {"facts": list(facts)}
    )


async def test_actual_ledger_profile_snapshot_and_restart_keep_source_evidence(person):
    original = await source(person, "self-description", "我是学生，正在做毕设。")
    result = await remember(
        person, original, fact("self-description", "我是学生", "职业：学生")
    )
    assert "职业：学生" in result["profile_text"]
    claim = result["facts"][0]
    assert claim["status"] == "active"
    assert claim["evidence"][0]["metadata"]["quote"] == "我是学生"
    await person.person_memory.close()
    person.person_memory = PersonMemory(person)
    incoming, scope, persona, _ = original
    persisted = await person.person_memory.query(incoming, scope, persona, "alice")
    assert persisted["from_cache"]
    assert persisted["fact_claim_ids"] == [claim["claim_id"]]
    context = await person.person_memory.context(incoming, scope, persona)
    assert context[0]["sender_id"] == "alice"
    assert "职业：学生" in context[0]["profile"]


async def test_actual_upstream_conflict_and_explicit_correction(person):
    first = await source(person, "student", "我是学生。")
    result = await remember(person, first, fact("student", "我是学生", "职业：学生"))
    old = result["facts"][0]["claim_id"]
    second = await source(person, "worker", "我现在是程序员了，不是学生。")
    result = await remember(
        person,
        second,
        fact("worker", "是程序员", "职业：程序员"),
    )
    new = next(c for c in result["facts"] if c["value_text"] == "职业：程序员")
    assert new["status"] == "conflicted"
    assert "职业：学生" in result["profile_text"]
    corrected = await remember(
        person, second, fact("worker", "不是学生", "职业：程序员", corrects=[old])
    )
    assert "职业：程序员" in corrected["profile_text"]
    assert "职业：学生" not in corrected["profile_text"]
    assert corrected["facts"][0]["status"] == "active"


async def test_repeated_fact_keeps_ledger_cardinality_and_adds_new_evidence(person):
    first = await source(person, "likes-first", "我喜欢苹果。")
    original = fact("likes-first", "喜欢苹果", "喜欢苹果", key="favorite_food")
    original["cardinality"] = "set"
    saved = await remember(person, first, original)
    second = await source(person, "likes-again", "我确实喜欢苹果。")
    result = await remember(
        person,
        second,
        fact("likes-again", "喜欢苹果", "喜欢苹果", key="ＦＡＶＯＲＩＴＥ_ＦＯＯＤ"),
    )
    assert result["facts"][0]["claim_id"] == saved["facts"][0]["claim_id"]
    claims = await person.person_memory.call("claims", result["person_id"])
    assert len(claims) == 1 and claims[0]["cardinality"] == "set"
    assert {e["metadata"]["source_id"] for e in result["facts"][0]["evidence"]} == {
        "likes-first",
        "likes-again",
    }


async def test_correction_keeps_existing_attribute_type_and_normalizes_key(person):
    old = await source(person, "old-job", "我是学生。")
    saved = await remember(person, old, fact("old-job", "我是学生", "职业：学生"))
    current = await source(person, "new-job", "我现在是程序员，不是学生。")
    corrected = fact(
        "new-job",
        "现在是程序员",
        "职业：程序员",
        key="ＯＣＣＵＰＡＴＩＯＮ",
        corrects=[saved["facts"][0]["claim_id"]],
    )
    corrected["cardinality"] = "set"
    result = await remember(person, current, corrected)
    assert "职业：程序员" in result["profile_text"]
    assert "职业：学生" not in result["profile_text"]
    claims = await person.person_memory.call("claims", result["person_id"])
    assert len(claims) == 1 and claims[0]["cardinality"] == "single"


async def test_unattributed_or_fabricated_evidence_cannot_write_any_part(person):
    original = await source(person, "own", "我喜欢简洁的回答。")
    valid = fact(
        "own",
        "喜欢简洁的回答",
        "互动偏好：简洁",
        key="reply_preference",
        section="interaction_preferences",
    )
    fabricated = fact("own", "我是医生", "职业：医生")
    with pytest.raises(ValueError, match="原文证据"):
        await remember(person, original, valid, fabricated)
    incoming, scope, persona, _ = original
    result = await person.person_memory.query(incoming, scope, persona, "alice")
    assert result["facts"] == []
    with pytest.raises(ValueError, match="原文证据"):
        await remember(
            person, original, fact("other-message", "喜欢简洁的回答", "偏好：简洁")
        )


async def test_uncertain_and_temporary_facts_do_not_become_stable_injection(person):
    original = await source(person, "temporary", "也许我以后去学医，今天有点累。")
    await remember(
        person,
        original,
        fact(
            "temporary",
            "也许我以后去学医",
            "可能想学医",
            section="uncertain_notes",
            stability="uncertain",
        ),
        fact(
            "temporary",
            "今天有点累",
            "今天有点累",
            key="current_state",
            section="recent_interactions",
            stability="temporal",
        ),
    )
    incoming, scope, persona, _ = original
    result = await person.person_memory.query(incoming, scope, persona, "alice")
    assert len(result["facts"]) == 2
    assert "不确定信息" in result["profile_text"]
    assert not await person.person_memory.context(incoming, scope, persona)


async def test_people_and_groups_are_isolated_even_when_habit_learning_is_shared(
    person,
):
    person.settings["session_isolation"] = False
    alice = await source(person, "alice-fact", "我是学生。", "alice")
    await remember(person, alice, fact("alice-fact", "我是学生", "职业：学生"))
    bob = await source(person, "bob-fact", "我是程序员。", "bob")
    bob_result = await remember(
        person, bob, fact("bob-fact", "我是程序员", "职业：程序员")
    )
    assert "职业：学生" not in bob_result["profile_text"]
    incoming, scope, persona, _ = alice
    assert not await person.person_memory.query(incoming, scope, persona, "bob-unknown")
    other_group = await source(
        person, "other-group", "我来这个群看看。", "alice", session="456"
    )
    other, other_scope, other_persona, _ = other_group
    result = await person.person_memory.query(
        other, other_scope, other_persona, "alice"
    )
    assert not result.get("facts")


async def test_persona_change_does_not_reuse_another_persona_profile(person):
    from unittest.mock import AsyncMock

    original = await source(person, "old-persona", "我是学生。")
    await remember(person, original, fact("old-persona", "我是学生", "职业：学生"))
    person.context.persona_manager.resolve_selected_persona = AsyncMock(
        return_value=("other", {"prompt": "另一人格"}, None, False)
    )
    incoming, scope, persona, _ = await source(person, "new-persona", "你好呀")
    assert persona == "other"
    assert not (
        await person.person_memory.query(incoming, scope, persona, "alice")
    ).get("facts")


async def test_correction_cannot_target_another_person_claim(person):
    alice = await source(person, "alice-student", "我是学生。", "alice")
    result = await remember(
        person, alice, fact("alice-student", "我是学生", "职业：学生")
    )
    bob = await source(person, "bob-worker", "我是程序员。", "bob")
    with pytest.raises(ValueError, match="同一属性"):
        await remember(
            person,
            bob,
            fact(
                "bob-worker",
                "我是程序员",
                "职业：程序员",
                corrects=[result["facts"][0]["claim_id"]],
            ),
        )


async def test_learning_revocation_blocks_tools_but_keeps_existing_memory(person):
    original = await source(person, "before-stop", "我是学生。")
    await remember(person, original, fact("before-stop", "我是学生", "职业：学生"))
    person.settings["learning_enabled"] = False
    with pytest.raises(ValueError, match="真实作者来源"):
        await remember(person, original, fact("before-stop", "我是学生", "职业：学生"))
    incoming, scope, persona, _ = original
    assert (
        "职业：学生"
        in (await person.person_memory.query(incoming, scope, persona, "alice"))[
            "profile_text"
        ]
    )


async def test_background_extraction_uses_actual_retained_inputs_and_updates_cursor(
    person,
):
    person.settings["person_memory_min_messages"] = 1
    import json

    person.context.llm_generate.side_effect = None
    person.context.llm_generate.return_value = SimpleNamespace(
        completion_text=json.dumps(
            {"facts": [fact("auto-self", "我是学生", "职业：学生")]}, ensure_ascii=False
        )
    )
    original = await source(person, "auto-self", "我是学生。")
    await asyncio.wait_for(asyncio.gather(*person.person_memory.busy.values()), 5)
    incoming, scope, persona, _ = original
    result = await person.person_memory.query(incoming, scope, persona, "alice")
    assert "职业：学生" in result["profile_text"]
    progress = await person.person_memory.call("progress", result["person_id"])
    assert progress["last_message"] > 0
    assert person.context.llm_generate.await_count == 1
    await person.person_memory.observe(incoming, scope, persona)
    assert not person.person_memory.busy


async def test_background_extraction_repairs_evidence_once_before_writing(
    person, caplog
):
    import json

    person.settings["person_memory_min_messages"] = 1
    person.context.llm_generate.side_effect = [
        SimpleNamespace(
            completion_text=json.dumps(
                {
                    "facts": [
                        fact(
                            "repair-source", "private-invalid-model-quote", "职业：医生"
                        )
                    ]
                }
            )
        ),
        SimpleNamespace(
            completion_text=json.dumps(
                {"facts": [fact("repair-source", "我是学生", "职业：学生")]}
            )
        ),
    ]
    incoming, scope, persona, _ = await source(person, "repair-source", "我是学生。")
    await asyncio.wait_for(asyncio.gather(*person.person_memory.busy.values()), 5)
    result = await person.person_memory.query(incoming, scope, persona, "alice")
    assert (
        "职业：学生" in result["profile_text"] and "医生" not in result["profile_text"]
    )
    assert await person.person_memory.call("processed", result["person_id"]) == {
        "repair-source"
    }
    assert person.context.llm_generate.await_count == 2
    request = json.loads(person.context.llm_generate.call_args.kwargs["prompt"])
    assert "原文证据" in request["validation_error"]
    assert "private-invalid-model-quote" not in caplog.text


async def test_profile_is_injected_into_native_reply_as_temporary_reference(person):
    original = await source(person, "profile-input", "我不喜欢被叫宝宝。")
    await remember(
        person,
        original,
        fact(
            "profile-input",
            "不喜欢被叫宝宝",
            "互动偏好：不要叫宝宝",
            key="address_preference",
            section="interaction_preferences",
        ),
    )
    person.settings["learning_enabled"] = False
    incoming = event("native-query", text="你还记得我不喜欢怎么称呼吗？")
    request = (await requests(person, incoming))[0]
    await person.apply_persona(incoming, request)
    injected = "\n".join(part.text for part in request.extra_user_content_parts)
    assert "[OvenHeart person profiles]" in injected
    assert "不要叫宝宝" in injected
    assert "不要叫宝宝" not in request.prompt


async def test_clearing_learning_material_does_not_stall_future_person_updates(person):
    import json

    person.settings["person_memory_min_messages"] = 1
    person.context.llm_generate.side_effect = None
    person.context.llm_generate.return_value = SimpleNamespace(
        completion_text=json.dumps(
            {"facts": [fact("before-clear", "我是学生", "职业：学生")]},
            ensure_ascii=False,
        )
    )
    _, scope, _, _ = await source(person, "before-clear", "我是学生。")
    await asyncio.wait_for(asyncio.gather(*person.person_memory.busy.values()), 5)
    await api_call(person.page_api.action, {"action": "clear_messages", "scope": scope})
    person.context.llm_generate.return_value = SimpleNamespace(
        completion_text=json.dumps(
            {
                "facts": [
                    fact(
                        "after-clear",
                        "我喜欢短回答",
                        "互动偏好：简短回答",
                        key="reply_length",
                        section="interaction_preferences",
                    )
                ]
            },
            ensure_ascii=False,
        )
    )
    incoming, scope, persona, _ = await source(person, "after-clear", "我喜欢短回答。")
    await asyncio.wait_for(asyncio.gather(*person.person_memory.busy.values()), 5)
    result = await person.person_memory.query(incoming, scope, persona, "alice")
    assert "职业：学生" in result["profile_text"]
    assert "简短回答" in result["profile_text"]
    assert person.context.llm_generate.await_count == 2


async def test_failed_extraction_keeps_existing_facts_and_retries_inputs(person):
    original = await source(person, "known", "我是学生。")
    await remember(person, original, fact("known", "我是学生", "职业：学生"))
    person.settings["person_memory_min_messages"] = 1
    person.context.llm_generate.side_effect = None
    person.context.llm_generate.return_value = SimpleNamespace(
        completion_text='{"facts":[{"made_up":"invalid"}]}'
    )
    incoming, scope, persona, _ = original
    await person.person_memory.observe(incoming, scope, persona)
    await asyncio.wait_for(asyncio.gather(*person.person_memory.busy.values()), 5)
    result = await person.person_memory.query(incoming, scope, persona, "alice")
    assert result["facts"][0]["value_text"] == "职业：学生"
    assert await person.person_memory.call("processed", result["person_id"]) == set()
    assert person.context.llm_generate.await_count == 2


def test_fact_machine_and_profile_algorithms_match_the_pinned_source():
    import ast
    import hashlib
    import json
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    upstream = root.parent / "MaiBot"
    if not (upstream / ".git").exists():
        pytest.skip("pinned upstream checkout unavailable")
    port = root / "vendor/maibot/amemorix"
    manifest = json.loads((port / "source_manifest.json").read_text(encoding="utf-8"))
    for relative in ("storage/metadata_fact.py", "utils/person_profile_service.py"):
        entry = manifest["files"][relative]
        original = subprocess.check_output(
            ["git", "show", manifest["commit"] + ":" + entry["source"]], cwd=upstream
        )
        current = (port / relative).read_bytes()
        assert hashlib.sha256(original).hexdigest() == entry["upstream_sha256"]
        assert hashlib.sha256(current).hexdigest() == entry["ported_sha256"]
        if relative.endswith("metadata_fact.py"):
            assert current == original
        else:

            def methods(blob):
                cls = next(
                    n for n in ast.parse(blob).body if isinstance(n, ast.ClassDef)
                )
                return {
                    n.name: ast.dump(n)
                    for n in cls.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and n.name != "_build_retriever"
                }

            assert methods(original) == methods(current)
