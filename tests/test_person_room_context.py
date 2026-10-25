"""Real native records feed the original profile service and leave on deletion."""

from tests.test_memory_consolidation import capture, episode, summary
from tests.test_person_memory import fact, remember, source
from tests.test_room_memory import material, note

pytest_plugins = ["tests.test_person_memory", "tests.test_memory_consolidation"]


async def test_native_recent_evidence_updates_cached_profile_without_owning_mentions(
    person,
):
    person.settings["native_memory_enabled"] = True
    person.context.get_provider_by_id = lambda name: None
    original = await source(person, "student", "我是学生。")
    stable = await remember(person, original, fact("student", "我是学生", "职业：学生"))
    await source(person, "bob", "大家好。", sender="bob")
    incoming, scope, persona, messages = await material(
        person, "trip", "周六我和bob一起去书店。"
    )
    saved = await person.room_memory.remember(
        incoming,
        scope,
        persona,
        note(
            quote="周六我和bob一起去书店",
            content="alice周六和bob一起去书店。",
            relations=[{"subject": "alice", "predicate": "同行", "object": "bob"}],
        ),
        messages,
    )
    profile = await person.person_memory.query(incoming, scope, persona, "alice")
    assert "职业：学生" in profile["profile_text"]
    assert profile["fact_claim_ids"] == stable["fact_claim_ids"]
    assert saved["id"] in {r["memory_id"] for r in profile["recent_interactions"]}
    assert any(e["object"] == "bob" for e in profile["relation_edges"])
    bob = await person.person_memory.query(incoming, scope, persona, "bob")
    assert not bob["recent_interactions"]
    assert not bob["facts"]
    cached = await person.person_memory.query(incoming, scope, persona, "alice")
    assert cached["snapshot_id"] == profile["snapshot_id"] and cached["from_cache"]
    incoming, scope, persona, messages = await material(
        person, "forget", "忘掉周六去书店的安排。"
    )
    deleted = await person.room_memory.change(
        incoming,
        scope,
        persona,
        "forget_memory",
        {
            "memory_id": saved["id"],
            "evidence": [{"msg_id": "1", "quote": "忘掉周六去书店"}],
        },
        messages,
    )
    assert deleted["success"], deleted
    updated = await person.person_memory.query(incoming, scope, persona, "alice")
    assert not updated["recent_interactions"]
    assert not updated["relation_edges"]
    assert "书店" not in updated["profile_text"]
    assert "职业：学生" in updated["profile_text"]


async def test_original_episode_enters_person_context_and_native_pause_removes_it(
    consolidator,
):
    p = consolidator
    p.settings.update(person_memory_enabled=True, person_memory_min_messages=30)
    p.context.llm_generate.side_effect = [summary(), episode()]
    incoming, scope, persona = await capture(p)
    profile = await p.person_memory.query(incoming, scope, persona, "alice")
    recent = profile["recent_interactions"]
    assert any(
        row["type"] == "episode" and "周六书店之约" in row["content"] for row in recent
    )
    assert not profile["facts"]
    context = await p.person_memory.context(incoming, scope, persona)
    assert context[0]["recent_interactions"]
    p.settings["native_memory_enabled"] = False
    paused = await p.person_memory.query(incoming, scope, persona, "alice")
    assert not paused["recent_interactions"]
    assert "书店" not in paused["profile_text"]
