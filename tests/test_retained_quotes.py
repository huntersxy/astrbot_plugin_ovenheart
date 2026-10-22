"""Check durable explicit quotes without turning context into evidence."""

import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("astrbot.api")

from astrbot.core.message.components import Reply  # noqa: E402

from astrbot_plugin_ovenheart.settings import DEFAULTS  # noqa: E402
from astrbot_plugin_ovenheart.store import Store  # noqa: E402
from tests.test_runtime import api_call, completion, event  # noqa: E402

pytest_plugins = ["tests.test_runtime"]


async def test_quote_learning_survives_cache_loss_and_never_adds_evidence(plugin):
    plugin.settings["learning_review_enabled"] = True
    for index in range(2):
        incoming = event(f"answer-{index}", text="我先听你说完，慢慢来。")
        incoming.message_obj.message = [
            Reply(
                id=f"outside-{index}",
                sender_id="bob",
                message_str="QUOTED_ANCHOR：" + "原始前提" * 400,
            )
        ]
        await plugin.capture(incoming)
    scope, _, _ = await plugin.resolve_scope(incoming)
    with sqlite3.connect(plugin.store.path) as db:
        rows = db.execute(
            "SELECT reply_context FROM messages WHERE scope=?", (scope,)
        ).fetchall()
    assert len(rows) == 2 and all(len(json.loads(r[0])["text"]) == 1200 for r in rows)
    plugin.learning_context.clear()
    plugin.events.clear()
    plugin.speaker_events.clear()
    plugin.store = Store(plugin.store.path)
    await plugin.store.call("initialize")
    batch = await plugin.store.call("batch", scope, 30)
    ids = {m["id"] for m in batch}
    seen = []

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

    plugin.context.llm_generate.side_effect = generate
    assert (await plugin.learn(scope))["status"] == "success" and len(seen) == 3
    for payload in seen:
        assert "QUOTED_ANCHOR" not in json.dumps(payload["observations"])
        quotes = [
            c
            for c in payload["conversation_context"]
            if c.get("relation") == "reply_to"
        ]
        assert len(quotes) == 2 and all(len(c["text"]) == 400 for c in quotes)
        assert all("id" not in c and c["sender"] == "bob" for c in quotes)
        assert {c["for_evidence_ids"][0] for c in quotes} == ids
    state = await plugin.store.call("state", scope)
    assert all(
        e["id"] in ids and "reply_to" not in e
        for t in state["traits"]
        for e in t["evidence"]
    )
    assert "QUOTED_ANCHOR" not in json.dumps(state)
    assert (
        await api_call(
            plugin.page_api.action, {"action": "clear_messages", "scope": scope}
        )
    )["status"] == "ok"
    with sqlite3.connect(plugin.store.path) as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM messages WHERE scope=?", (scope,)
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("author", ["bob", ""])
async def test_current_blacklist_filters_previously_stored_quotes(plugin, author):
    for index in range(2):
        incoming = event(f"answer-{index}", text="先听对方说完。")
        incoming.message_obj.message = [
            Reply(
                id=f"outside-{index}",
                sender_id=author,
                message_str="QUOTED_DENY：不应再传入模型",
            )
        ]
        await plugin.capture(incoming)
    scope, _, _ = await plugin.resolve_scope(incoming)
    assert all(m.get("reply_to") for m in await plugin.store.call("batch", scope, 30))
    await plugin.update_settings({"blocked_senders": ["bob"]})
    batch = await plugin.store.call("batch", scope, 30, [], ["bob"])
    assert len(batch) == 2 and all("reply_to" not in m for m in batch)
    assert (await plugin.learn(scope))["status"] == "success"
    assert all(
        "QUOTED_DENY" not in call.kwargs["prompt"]
        for call in plugin.context.llm_generate.await_args_list
    )


async def test_legacy_message_schema_preserves_ids_and_quotes_follow_source_retention(
    tmp_path,
):
    path = tmp_path / "old.sqlite3"
    scope, now = "a" * 24, time.time()
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY,scope TEXT NOT NULL,source_id TEXT NOT NULL,sender TEXT NOT NULL,text TEXT NOT NULL,created REAL NOT NULL,consumed INTEGER NOT NULL DEFAULT 0,UNIQUE(scope,source_id))"
        )
        db.execute(
            "INSERT INTO messages VALUES (23,?,?,?,?,?,0)",
            (scope, "legacy", "alice", "原始素材", now),
        )
    store = Store(path)
    await store.call("initialize")
    before = await store.call("batch", scope, 30)
    assert (
        before[0]["id"] == 23
        and before[0]["source_id"] == "legacy"
        and "reply_to" not in before[0]
    )
    settings = DEFAULTS | {"session_isolation": True}
    assert not await store.call(
        "capture",
        scope,
        "qq:g:1",
        "default",
        "test",
        "legacy",
        "alice",
        "原始素材",
        settings,
    )
    assert await store.call(
        "capture",
        scope,
        "qq:g:1",
        "default",
        "test",
        "new",
        "alice",
        "新的回应",
        settings,
        False,
        {"sender": "bob", "text": "引用前提"},
    )
    await store.call("initialize")
    batch = await store.call("batch", scope, 30)
    assert [m["id"] for m in batch] == [23, 24]
    assert batch[1]["reply_to"] == {"sender": "bob", "text": "引用前提", "self": False}
    with sqlite3.connect(path) as db:
        db.execute("UPDATE messages SET created=?", (now - 15 * 86400,))
    await store.call("prune", 14)
    assert await store.call("batch", scope, 30) == []
