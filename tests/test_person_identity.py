"""Person accounts follow MaiBot's platform + user_id identity."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.memory.person_memory import PersonMemory, get_person_id, person_identity
from core.settings import DEFAULTS


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


@pytest.fixture
def context_event(person_memory):
    extra = {}
    event = SimpleNamespace(
        get_extra=lambda key, default=None: extra.get(key, default),
        set_extra=extra.__setitem__,
        get_platform_name=lambda: "aiocqhttp",
        get_self_id=lambda: "bot",
        get_sender_id=lambda: "42",
    )
    person_memory.plugin.settings = DEFAULTS.copy()
    person_memory.plugin.room_memory = SimpleNamespace(
        context_revision=0,
        person_context=AsyncMock(
            return_value={"paragraphs": [], "relations": [], "episodes": []}
        ),
    )
    person_memory.plugin.conversation = SimpleNamespace(
        record=lambda _: {"sender_id": "42"}
    )
    person_memory.permitted = AsyncMock(return_value=True)
    return event


async def test_planner_and_reply_reuse_the_same_person_context(
    person_memory, context_event
):
    await person_memory.call(
        "register", get_person_id("aiocqhttp", "42"), "42", "Member", "aiocqhttp"
    )
    await person_memory.call(
        "panel_action",
        "profile_save",
        {
            "id": get_person_id("aiocqhttp", "42"),
            "text": "Member prefers short replies",
        },
        [get_person_id("aiocqhttp", "42")],
    )
    first = await person_memory.context(context_event, "scope", "persona")
    context_event.set_extra(
        "ovenheart_decision", {"scope": "scope", "target": {"sender_id": "42"}}
    )
    second = await person_memory.context(context_event, "scope", "persona")
    assert first[0]["profile"] == second[0]["profile"]
    assert second[0]["role"] == "reply_target"
    person_memory.plugin.room_memory.person_context.assert_awaited_once()
    assert context_event.get_extra("ovenheart_person_context_cache_stats") == {
        "queries": 2,
        "hits": 1,
    }
    # Explicit planner queries still read the current ledger and source context.
    await person_memory.query(context_event, "scope", "persona", "42")
    assert person_memory.plugin.room_memory.person_context.await_count == 2


@pytest.mark.parametrize("change", ["facts", "room", "settings", "scope", "blocked"])
async def test_person_context_cache_respects_updates_and_access(
    person_memory, context_event, change
):
    identity = get_person_id("aiocqhttp", "42")
    await person_memory.call("register", identity, "42", "Member", "aiocqhttp")
    await person_memory.query(
        context_event, "scope", "persona", "42", reuse_context=True
    )
    scope = "scope"
    if change == "facts":
        await person_memory.call(
            "panel_action",
            "profile_save",
            {"id": identity, "text": "Updated preference"},
            [identity],
        )
    elif change == "room":
        person_memory.plugin.room_memory.context_revision += 1
    elif change == "settings":
        person_memory.plugin.settings_revision += 1
    elif change == "scope":
        scope = "another-scope"
    else:
        person_memory.plugin.settings["blocked_senders"] = ["42"]
    result = await person_memory.query(
        context_event, scope, "persona", "42", reuse_context=True
    )
    assert context_event.get_extra("ovenheart_person_context_cache_stats")["hits"] == 0
    if change == "blocked":
        assert result == {}
        person_memory.plugin.room_memory.person_context.assert_awaited_once()
    else:
        assert person_memory.plugin.room_memory.person_context.await_count == 2
        if change == "facts":
            assert "Updated preference" in result["profile_text"]
