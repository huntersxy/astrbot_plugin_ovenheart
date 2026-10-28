"""Global visibility includes pre-existing native stores and never copies them."""

import hashlib
import json

from tests.test_image_memory import capture, picture
from tests.test_person_memory import fact, remember, source
from tests.test_room_memory import SyntheticEmbedding, material, note
from tests.test_runtime import api_call, event

pytest_plugins = ["tests.test_room_memory", "tests.test_person_memory"]


async def test_global_recall_sees_existing_memories_and_restores_isolation(memories):
    p = memories
    a, sa, persona, rows = await material(p, "same-id", "Neko喜欢猫咪。", session="a")
    old = await p.room_memory.remember(
        a, sa, persona, note(content="Neko喜欢猫咪。"), rows
    )
    b, sb, _, rows = await material(p, "same-id", "Neko喜欢读书。", session="b")
    current = await p.room_memory.remember(
        b,
        sb,
        persona,
        note(quote="Neko喜欢读书", content="Neko喜欢读书。", relations=[]),
        rows,
    )
    p.settings.update(memory_isolation=False, memory_runtime_max_rooms=1)
    await p.store.call(
        "touch_scope", "f" * 24, "another-room", "another-persona", "Another"
    )
    assert "f" * 24 not in await p.room_memory.scope_keys(sb)
    # Include data already written to the persona-wide native store as well.
    shared = hashlib.sha256(
        json.dumps(["ovenheart-global-memory", persona], ensure_ascii=False).encode()
    ).hexdigest()[:24]
    await p.room_memory.call_storage(
        shared,
        "write",
        "Neko曾在湖边看小猫。",
        [
            {
                "source_id": "earlier-shared",
                "source_scope": sa,
                "sender": "alice",
                "quote": "Neko曾在湖边看小猫",
                "created": a.created_at,
            }
        ],
        [],
    )
    found = await p.room_memory.search(b, sb, persona, "Neko")
    assert {old["id"], current["id"]} <= {r["id"] for r in found}
    assert any("湖边" in r["content"] for r in found)
    assert len(p.room_memory.rooms) <= 1
    console = await api_call(
        p.page_api.memory_panel.read, query=f"scope={sb}&view=records&kind=paragraph"
    )
    assert console["data"]["total"] == 3
    p.settings["memory_isolation"] = True
    found = await p.room_memory.search(b, sb, persona, "Neko")
    assert {r["id"] for r in found} == {current["id"]}
    p.settings["memory_isolation"] = False
    c, sc, _, rows = await material(p, "third", "Neko喜欢茶。", session="c")
    third = await p.room_memory.remember(
        c,
        sc,
        persona,
        note(quote="Neko喜欢茶", content="Neko喜欢茶。", relations=[]),
        rows,
    )
    assert await p.room_memory.storage_scope(sc) == sc
    p.settings["memory_isolation"] = True
    assert any(
        r["id"] == third["id"]
        for r in await p.room_memory.search(c, sc, persona, "Neko")
    )


async def test_global_person_recall_and_cross_room_correction_keep_real_author(person):
    p = person
    alice = await source(p, "student", "我是学生。", session="a")
    saved = await remember(p, alice, fact("student", "我是学生", "职业：学生"))
    b = await source(p, "book", "我喜欢读书。", session="b")
    await remember(
        p,
        b,
        fact("book", "我喜欢读书", "喜欢读书", key="hobby", section="stable_facts"),
    )
    incoming, scope, persona, _ = b
    p.settings["memory_isolation"] = False
    profile = await p.person_memory.query(incoming, scope, persona, "alice")
    assert "学生" in profile["profile_text"] and "读书" in profile["profile_text"]
    assert not (await p.person_memory.query(incoming, scope, persona, "bob")).get(
        "profile_text"
    )
    corrected = await source(p, "teacher", "我现在是老师，不是学生。", session="b")
    await remember(
        p,
        corrected,
        fact(
            "teacher",
            "我现在是老师，不是学生。",
            "职业：老师",
            corrects=[saved["facts"][0]["claim_id"]],
        ),
    )
    profile = await p.person_memory.query(incoming, scope, persona, "alice")
    assert (
        "老师" in profile["profile_text"]
        and "职业：学生" not in profile["profile_text"]
    )
    p.settings["memory_isolation"] = True
    local = await p.person_memory.query(incoming, scope, persona, "alice")
    assert "读书" in local["profile_text"] and "老师" not in local["profile_text"]


async def test_global_image_recall_reads_preexisting_native_asset(memories):
    p = memories
    first, sa, persona, _ = await capture(
        p, "old-image", picture(), "绿色图片", session="a"
    )
    b = event("old-image", session="b")
    await p.capture(b)
    sb, _, _ = await p.resolve_scope(b)
    p.settings["memory_isolation"] = False
    found = await p.room_memory.call(
        sb, "image_search", picture(), "old-image", 0, event=b
    )
    assert found["success"] and any(
        r["match_kind"] == "exact_hash" for r in found["hits"]
    )
    p.settings["memory_isolation"] = True
    assert not (
        await p.room_memory.call(sb, "image_search", picture(), "old-image", 0, event=b)
    ).get("hits")


async def test_global_correction_and_undo_follow_the_original_record(memories):
    p = memories
    a, sa, persona, rows = await material(p, session="a")
    old = await p.room_memory.remember(a, sa, persona, note(), rows)
    p.settings["memory_isolation"] = False
    b, sb, _, rows = await material(p, "correct", "把看小猫的安排改为周日", session="b")
    corrected = await p.room_memory.change(
        b,
        sb,
        persona,
        "correct_memory",
        {
            "memory_id": old["id"],
            "content": "Neko周日去看小猫。",
            "relations": [],
            "evidence": [{"msg_id": "1", "quote": "把看小猫的安排改为周日"}],
        },
        rows,
    )
    assert corrected["success"], corrected
    assert corrected["new_ids"][0] in {
        r["id"] for r in await p.room_memory.search(b, sb, persona, "小猫")
    }
    undo, _, _, rows = await material(p, "undo", "撤销刚才的更正", session="b")
    result = await p.room_memory.change(
        undo,
        sb,
        persona,
        "undo_memory_change",
        {
            "evidence": [{"msg_id": "1", "quote": "撤销刚才的更正"}],
        },
        rows,
    )
    assert result["success"] and result["restored_ids"] == [old["id"]], result
    assert old["id"] in {
        r["id"] for r in await p.room_memory.search(undo, sb, persona, "小猫")
    }


async def test_global_scan_reuses_the_native_provider_query_embedding(memories):
    p = memories
    provider = SyntheticEmbedding()
    requests = []
    original = provider.get_embeddings

    async def tracked(texts):
        requests.extend(texts)
        return await original(texts)

    provider.get_embeddings = tracked
    p.settings["embedding_provider_id"] = "semantic"
    p.context.get_provider_by_id = lambda _: provider
    for session in ("a", "b"):
        e, scope, persona, rows = await material(p, session=session)
        await p.room_memory.remember(
            e, scope, persona, note(content=f"Neko喜欢猫咪。来源{session}"), rows
        )
    p.settings["memory_isolation"] = False
    requests.clear()
    result = await p.room_memory.search(e, scope, persona, "GATTO unique query")
    assert len(result) == 2
    assert requests.count("GATTO unique query") == 1
