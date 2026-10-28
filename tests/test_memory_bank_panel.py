"""Read all visible source memories and the real native person ledger."""

from tests.test_person_memory import fact, remember, source
from tests.test_room_memory import material, note
from tests.test_runtime import api_call

pytest_plugins = ["tests.test_person_memory", "tests.test_room_memory"]


async def test_global_panel_includes_existing_memories_without_bank_selection(memories):
    event, scope, persona, rows = await material(memories)
    old = await memories.room_memory.remember(event, scope, persona, note(), rows)
    memories.settings["memory_isolation"] = False
    panel = memories.page_api.memory_panel
    active = await api_call(
        panel.read, query=f"scope={scope}&view=records&kind=paragraph"
    )
    assert active["status"] == "ok", active
    assert active["data"]["total"] == 1
    assert active["data"]["items"][0]["id"] == old["id"]
    assert active["data"]["items"][0]["storage_scope"] == scope
    assert memories.settings["memory_isolation"] is False
    invalid = await api_call(
        panel.read, query=f"scope={scope}&storage_scope=../metadata"
    )
    assert invalid["status"] == "error"


async def test_panel_uses_person_owner_ledger_and_filters_other_chats(person):
    p = person
    original = await source(p, "student", "我是学生。")
    await remember(p, original, fact("student", "我是学生", "职业：学生"))
    other = await source(p, "worker", "我是程序员。", session="456")
    await remember(p, other, fact("worker", "我是程序员", "职业：程序员"))
    _, scope, _, _ = original
    panel = p.page_api.memory_panel
    profiles = await api_call(panel.read, query=f"scope={scope}&view=profiles")
    assert profiles["status"] == "ok", profiles
    assert profiles["data"]["total"] == 1
    profile = profiles["data"]["items"][0]
    assert "学生" in profile["text"] and "程序员" not in profile["text"]
    assert "alice" in profile["title"]
    facts = await api_call(panel.read, query=f"scope={scope}&kind=fact")
    assert facts["data"]["total"] == 1
    claim = facts["data"]["items"][0]
    assert claim["metadata"]["evidence"][0]["quote"] == "我是学生"
    for action, payload in [
        ("profile_save", {"id": profile["id"], "text": "人工补充的画像"}),
        ("profile_aliases", {"id": profile["id"], "aliases": ["小爱"]}),
        ("fact_retract", {"id": claim["id"]}),
        ("fact_restore", {"id": claim["id"]}),
    ]:
        result = await api_call(
            panel.action, {"scope": scope, "action": action, "payload": payload}
        )
        assert result["status"] == "ok", result
    saved = await api_call(
        panel.read, query=f"scope={scope}&view=profiles&id={profile['id']}"
    )
    assert saved["data"]["items"][0]["manual_override"] == "人工补充的画像"
    assert saved["data"]["items"][0]["aliases"] == ["小爱"]
    wrong = await api_call(
        panel.action,
        {"scope": other[1], "action": "fact_retract", "payload": {"id": claim["id"]}},
    )
    assert wrong["status"] == "error"
    updated = await api_call(
        panel.action,
        {
            "scope": scope,
            "action": "fact_save",
            "payload": {"id": claim["id"], "text": "职业：老师"},
        },
    )
    assert updated["status"] == "ok", updated
    current = await api_call(panel.read, query=f"scope={scope}&kind=fact")
    assert any(item["text"] == "职业：老师" for item in current["data"]["items"])
    incoming = original[0]
    await p.store.call(
        "chat_context",
        scope,
        "cid",
        {
            "source": "bot:reply",
            "sender_id": incoming.get_self_id(),
            "self": True,
            "text": "知道了",
            "time": incoming.created_at,
        },
    )
    p.events.clear()
    persisted = await api_call(panel.read, query=f"scope={scope}&view=profiles")
    assert persisted["data"]["total"] == 1
    p.settings["memory_isolation"] = False
    archived = await api_call(panel.read, query=f"scope={scope}&view=profiles")
    assert profile["id"] in {row["id"] for row in archived["data"]["items"]}
    assert not p.room_memory.rooms
