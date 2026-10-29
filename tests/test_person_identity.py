"""Person accounts follow MaiBot's platform + user_id identity."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from core.memory.person_memory import PersonMemory, get_person_id, person_identity


def test_get_person_id_matches_maibot_formula():
    assert get_person_id("aiocqhttp", "1833505972") == hashlib.md5(
        b"aiocqhttp_1833505972"
    ).hexdigest()
    assert get_person_id("aiocqhttp", "1833505972") == person_identity(
        "aiocqhttp", "1833505972"
    )


def test_get_person_id_ignores_session_dimensions():
    first = person_identity("aiocqhttp", "u1", "bot", ["umo", "scope"], "persona")
    second = person_identity("aiocqhttp", "u1", "other-bot", ["other", "scope2"], "p2")
    assert first == second
    assert first != get_person_id("telegram", "u1")


@pytest.fixture
async def person_memory(tmp_path):
    plugin = SimpleNamespace(
        store=SimpleNamespace(path=tmp_path / "ovenheart.sqlite3"),
        settings={},
        settings_revision=1,
        track=lambda task: task,
    )
    plugin.store.path.parent.mkdir(parents=True, exist_ok=True)
    memory = PersonMemory(plugin)
    await memory.call("initialize")
    yield memory
    await memory.close()


async def test_register_aliases_legacy_session_buckets(person_memory):
    canonical = get_person_id("aiocqhttp", "1833505972")
    legacy = hashlib.md5(b"legacy-session-bucket").hexdigest()
    await person_memory.call("register", legacy, "1833505972", "汐兮雨", "")
    await person_memory.call("register", canonical, "1833505972", "汐兮雨", "aiocqhttp")
    ids = await person_memory.call(
        "ledger_ids", canonical, "1833505972", "aiocqhttp"
    )
    assert ids[0] == canonical
    assert legacy in ids


async def test_register_keeps_person_name_and_group_cards(person_memory):
    import json

    from sqlmodel import Session

    from core.vendor.maibot.amemorix.host import PersonInfo

    person_id = get_person_id("aiocqhttp", "42")
    await person_memory.call(
        "register", person_id, "42", "群里叫小雨", "aiocqhttp", "1001"
    )
    await person_memory.call(
        "register", person_id, "42", "另一个群名片", "aiocqhttp", "1002"
    )
    await person_memory.call(
        "register", person_id, "42", "私聊昵称", "aiocqhttp", ""
    )
    with Session(person_memory.engine) as session:
        row = session.get(PersonInfo, person_id)
    assert row.person_name == "群里叫小雨"
    assert row.user_nickname == "私聊昵称"
    cards = json.loads(row.group_cardname)
    assert cards[0] == {"group_id": "1002", "group_cardname": "另一个群名片"}
    assert {"group_id": "1001", "group_cardname": "群里叫小雨"} in cards
    assert all(isinstance(item, dict) for item in cards)


async def test_identity_uses_platform_name(person_memory):
    event = SimpleNamespace(
        get_platform_name=lambda: "aiocqhttp",
        get_platform_id=lambda: "qq-main",
        get_self_id=lambda: "bot",
        unified_msg_origin="qq-main:GroupMessage:1",
    )
    assert person_memory.identity(event, "scope", "persona", "42") == get_person_id(
        "aiocqhttp", "42"
    )
    assert await person_memory.scope_ids(event, "scope", "persona", "42") == [
        get_person_id("aiocqhttp", "42")
    ]
