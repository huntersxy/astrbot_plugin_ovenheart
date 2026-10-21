"""Verify evidence accumulation, old-database migration and rollback semantics."""

import json
import sqlite3

import pytest

from astrbot_plugin_ovenheart.settings import DEFAULTS
from astrbot_plugin_ovenheart.store import Store

SCOPE = "a" * 24


@pytest.fixture
async def store(tmp_path):
    instance = Store(tmp_path / "persona.sqlite3")
    await instance.call("initialize")
    return instance


async def batch(store, number, **patch):
    """Commit an independent source batch describing a repeated habit.

    Args:
        store: Temporary SQLite store.
        number: Independent source batch number.
        **patch: Candidate fields to vary.

    Returns:
        Current scope detail.
    """
    for i in range(2):
        await store.call(
            "capture",
            SCOPE,
            "qq:GroupMessage:1",
            "default",
            "测试",
            f"{number}:{i}",
            "user",
            "好家伙还是你懂我",
            DEFAULTS,
        )
    sources = await store.call("batch", SCOPE, 30)
    trait = {
        "kind": "expression",
        "situation": "轻松接话时",
        "content": "简短反问接住玩笑",
        "confidence": 0.95,
        "evidence": [
            {
                "id": s["id"],
                "source_id": s["source_id"],
                "sender": s["sender"],
                "text": s["text"],
                "created": s["created"],
            }
            for s in sources
        ],
        **patch,
    }
    run = await store.call("start_run", SCOPE)
    await store.call("finish_run", run, SCOPE, sources, [trait], DEFAULTS, "")
    return await store.call("state", SCOPE)


async def test_independent_evidence_accumulates_and_replayed_run_is_idempotent(store):
    first = await batch(store, 1)
    second = await batch(store, 2)
    assert len(second["traits"]) == 1
    habit = second["traits"][0]
    assert habit["support_count"] == 2 and habit["evidence_count"] == 4
    assert habit["id"] == first["traits"][0]["id"]
    assert "补充证据 1 条" in second["runs"][0]["message"]
    await store.call("finish_run", second["runs"][0]["id"], SCOPE, [], [], DEFAULTS, "")
    assert (await store.call("state", SCOPE))["traits"][0]["support_count"] == 2


async def test_paraphrases_merge_but_never_overwrite_human_edits(store):
    state = await batch(store, 1)
    identifier = state["traits"][0]["id"]
    state = await batch(store, 2, merge_id=identifier, content="用反问自然接住玩笑")
    assert len(state["traits"]) == 1 and state["traits"][0]["support_count"] == 2
    await store.call(
        "mutate",
        SCOPE,
        "trait",
        {"id": identifier, "content": "人工设定的表达", "status": "active"},
    )
    state = await batch(store, 3, merge_id=identifier, content="短句反问接话")
    assert state["traits"][0]["content"] == "人工设定的表达"
    assert state["traits"][0]["status"] == "active"
    assert state["traits"][0]["evidence_count"] == 6
    await store.call("mutate", SCOPE, "trait", {"id": identifier, "status": "rejected"})
    state = await batch(store, 4, merge_id=identifier, content="换个措辞再学回来")
    assert len(state["traits"]) == 1 and state["traits"][0]["status"] == "rejected"


async def test_jargon_is_refined_at_independent_batch_thresholds(store):
    state = await batch(store, 1, kind="jargon", content="好家伙：惊讶")
    identifier = state["traits"][0]["id"]
    state = await batch(
        store,
        2,
        kind="jargon",
        content="好家伙：轻松惊讶，不是责备",
        merge_id=identifier,
    )
    assert state["traits"][0]["meaning_stage"] == 1
    assert state["traits"][0]["content"] == "好家伙：轻松惊讶，不是责备"
    state = await batch(
        store, 3, kind="jargon", content="好家伙：临时另一种解释", merge_id=identifier
    )
    assert state["traits"][0]["meaning_stage"] == 1
    assert state["traits"][0]["content"] != "好家伙：临时另一种解释"


async def test_learning_snapshot_restores_evidence_and_suppresses_later_aliases(store):
    first = await batch(store, 1)
    identifier = first["traits"][0]["id"]
    second = await batch(store, 2, merge_id=identifier, content="换句话表达相同习惯")
    revision = second["revisions"][0]["id"]
    await store.call("mutate", SCOPE, "restore", {"id": revision})
    state = await store.call("state", SCOPE)
    assert state["traits"][0]["support_count"] == 1
    assert state["traits"][0]["evidence_count"] == 2


async def test_legacy_database_is_copied_once_and_original_is_preserved(tmp_path):
    legacy = tmp_path / "old.sqlite3"
    with sqlite3.connect(legacy) as db:
        db.executescript("""
        CREATE TABLE traits (id INTEGER PRIMARY KEY, scope TEXT, kind TEXT, situation TEXT,
            content TEXT, confidence REAL, evidence TEXT, status TEXT, fingerprint TEXT,
            created REAL, updated REAL, UNIQUE(scope,fingerprint));
        """)
        db.execute(
            "INSERT INTO traits VALUES (1,?,?,?,?,?,?,?,?,?,?)",
            (
                SCOPE,
                "expression",
                "日常",
                "人工调整的旧习惯",
                0.9,
                json.dumps([{"id": 1, "created": 1, "text": "证据", "sender": "u"}]),
                "active",
                "legacy",
                1.0,
                1.0,
            ),
        )
    store = Store(tmp_path / "new.sqlite3")
    await store.call("initialize", legacy)
    with sqlite3.connect(store.path) as db:
        row = db.execute("SELECT content,manual,evidence_count FROM traits").fetchone()
        assert row == ("人工调整的旧习惯", 1, 1)
        db.execute("UPDATE traits SET content='新目录中的改动'")
    await store.call("initialize", legacy)
    with sqlite3.connect(store.path) as db:
        assert (
            db.execute("SELECT content FROM traits").fetchone()[0] == "新目录中的改动"
        )
    with sqlite3.connect(legacy) as db:
        assert "manual" not in {r[1] for r in db.execute("PRAGMA table_info(traits)")}
        assert (
            db.execute("SELECT content FROM traits").fetchone()[0] == "人工调整的旧习惯"
        )


async def test_jargon_evidence_excerpts_stay_bounded(store):
    for number in range(20):
        state = await batch(store, number)
    habit = state["traits"][0]
    assert habit["evidence_count"] == 40 and len(habit["evidence"]) == 32
    assert habit["support_count"] == 20


async def test_legacy_import_includes_uncheckpointed_wal(tmp_path):
    legacy = Store(tmp_path / "old.sqlite3")
    await legacy.call("initialize")
    source = sqlite3.connect(legacy.path)
    try:
        source.execute("PRAGMA journal_mode=WAL")
        source.execute(
            "INSERT INTO settings VALUES (1, ?)",
            (json.dumps({"activation_mode": "auto"}),),
        )
        source.commit()
        assert legacy.path.with_suffix(".sqlite3-wal").stat().st_size > 0
        current = Store(tmp_path / "new.sqlite3")
        await current.call("initialize", legacy.path)
        assert (await current.call("settings"))["activation_mode"] == "auto"
    finally:
        source.close()
