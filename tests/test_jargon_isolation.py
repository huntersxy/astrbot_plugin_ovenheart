"""Keep local definitions separate even when other habits are shared."""

import json
import sqlite3

import pytest

from astrbot_plugin_ovenheart.settings import SHARED_SCOPE
from tests.test_store import DEFAULTS, TRAIT, seed

pytest_plugins = ["tests.test_store"]


@pytest.mark.parametrize("shared", [False, True])
async def test_same_word_keeps_independent_meanings_and_support(store, shared):
    scopes = ["a" * 24, "b" * 24]
    settings = DEFAULTS | {"activation_mode": "auto", "session_isolation": not shared}
    definitions = ["好家伙：烤好了", "好家伙：暂停游戏"]
    ids = []
    for scope, definition in zip(scopes, definitions, strict=True):
        batch = await seed(store, scope)
        run = await store.call("start_run", scope)
        await store.call(
            "finish_run",
            run,
            scope,
            batch,
            [TRAIT | {"kind": "jargon", "content": definition}, TRAIT],
            settings,
            "",
            "",
            None,
            None,
            None,
            None,
            None,
            shared,
        )
        words = (await store.call("state", scope))["traits"]
        ids.append(next(t["id"] for t in words if t["kind"] == "jargon"))
    for shared_read in (True, False, True):
        for scope, definition, identifier in zip(scopes, definitions, ids, strict=True):
            pool = (await store.call("guidance", scope, shared_read))["traits"]
            jargon = [t for t in pool if t["kind"] == "jargon"]
            assert [(t["id"], t["content"], t["support_count"]) for t in jargon] == [
                (identifier, definition, 1)
            ]
            known = await store.call("state", scope, None, None, shared_read)
            assert {t["id"] for t in known["traits"] if t["kind"] == "jargon"} == {
                identifier
            }
            assert known["active_count"] == len(pool)
    assert not any(
        t["kind"] == "jargon"
        for t in (await store.call("guidance", "c" * 24, True))["traits"]
    )
    assert not await store.call(
        "applied", scopes[0], "foreign", [ids[1]], "lexical", True
    )
    assert await store.call("applied", scopes[0], "local", ids, "lexical", True)
    delivery = (await store.call("state", scopes[0]))["applications"][0]
    assert delivery["trait_ids"] == [ids[0]]


async def test_shared_batch_versions_both_owners_and_rejects_foreign_merge_atomically(
    store,
):
    scope, other = "a" * 24, "b" * 24
    settings = DEFAULTS | {"activation_mode": "auto", "session_isolation": False}
    batch = await seed(store, scope)
    run = await store.call("start_run", scope)
    word = TRAIT | {"kind": "jargon", "content": "好家伙：烤好了"}
    await store.call(
        "finish_run",
        run,
        scope,
        batch,
        [word, TRAIT],
        settings,
        "",
        "",
        None,
        None,
        None,
        None,
        None,
        True,
    )
    before = await store.call("state", scope, None, None, True)
    assert {r["scope"] for r in before["revisions"]} == {scope, SHARED_SCOPE}
    identifier = next(t["id"] for t in before["traits"] if t["kind"] == "jargon")
    batch = await seed(store, other)
    run = await store.call("start_run", other)
    with pytest.raises(ValueError, match="合并目标"):
        await store.call(
            "finish_run",
            run,
            other,
            batch,
            [TRAIT | {"content": "新表达"}, word | {"merge_id": identifier}],
            settings,
            "",
            "",
            None,
            None,
            None,
            None,
            None,
            True,
        )
    assert len(await store.call("batch", other, 30)) == 3
    assert (await store.call("state", SHARED_SCOPE))["traits"][0]["content"] == TRAIT[
        "content"
    ]
    revision = next(r for r in before["revisions"] if r["scope"] == scope)
    await store.call("mutate", scope, "restore", {"id": revision["id"]})
    assert not any(
        t["kind"] == "jargon"
        for t in (await store.call("guidance", scope, True))["traits"]
    )
    assert (await store.call("guidance", other, True))["traits"]


@pytest.mark.parametrize("status", ["active", "disabled", "rejected", "pending"])
async def test_legacy_migration_preserves_records_and_quarantines_ambiguous_origins(
    store,
    status,
):
    scopes = ["a" * 24, "b" * 24]
    for scope in scopes:
        batch = await seed(store, scope)
        run = await store.call("start_run", scope)
        await store.call(
            "finish_run",
            run,
            scope,
            batch,
            [
                TRAIT
                | {
                    "kind": "jargon",
                    "content": f"词汇{scope[0]}：本地含义",
                    "evidence": [e | {"scope": scope} for e in TRAIT["evidence"]],
                }
            ],
            DEFAULTS | {"activation_mode": "auto"},
            "",
        )
    # Simulate an existing 0.18 database; no current writer creates common jargon.
    with sqlite3.connect(store.path) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute("SELECT * FROM traits ORDER BY id")]
        first, second = rows
        db.execute("UPDATE traits SET scope=?", (SHARED_SCOPE,))
        db.execute("UPDATE trait_aliases SET scope=?", (SHARED_SCOPE,))
        db.execute(
            "UPDATE traits SET manual=1,rating=-1,use_count=9,support_count=4,meaning_stage=2,status=? WHERE id=?",
            (status, first["id"]),
        )
        mixed = [e | {"scope": scopes[i]} for i, e in enumerate(TRAIT["evidence"])]
        db.execute(
            "UPDATE traits SET evidence=? WHERE id=?", (json.dumps(mixed), second["id"])
        )
        before = {r["id"]: dict(r) for r in db.execute("SELECT * FROM traits")}
        db.execute(
            "INSERT INTO embeddings VALUES (?, 'signature', 'digest', '[1.0,0.0]')",
            (first["id"],),
        )
    await store.call("initialize")
    local = (await store.call("state", scopes[0]))["traits"][0]
    assert local == before[first["id"]] | {
        "scope": scopes[0],
        "evidence": json.loads(first["evidence"]),
        "examples": json.loads(first["examples"]),
    }
    archived = (await store.call("state", SHARED_SCOPE))["traits"]
    assert len(archived) == 1 and archived[0]["id"] == second["id"]
    assert archived[0]["evidence"] == mixed
    assert not (await store.call("guidance", scopes[1], True))["traits"]
    assert not (await store.call("guidance", SHARED_SCOPE, True))["traits"]
    assert first["id"] in await store.call("vectors", "signature", scopes[0])
    revision_count = len((await store.call("state", scopes[0]))["revisions"])
    await store.call("initialize")
    assert len((await store.call("state", scopes[0]))["revisions"]) == revision_count
    assert (await store.call("library", "all", "jargon", "all"))["total"] == 2
    with pytest.raises(ValueError, match="来源尚未确定"):
        await store.call(
            "mutate", SHARED_SCOPE, "trait", {"id": second["id"], "status": "active"}
        )
    with sqlite3.connect(store.path) as db:
        assert (
            db.execute(
                "SELECT scope FROM trait_aliases WHERE trait_id=?", (first["id"],)
            ).fetchone()[0]
            == scopes[0]
        )


@pytest.mark.parametrize("incomplete", [True, False])
async def test_legacy_unknown_or_truncated_evidence_is_retained_without_injection(
    store, incomplete
):
    scope = "a" * 24
    batch = await seed(store, scope)
    run = await store.call("start_run", scope)
    evidence = (
        [e | {"scope": scope} for e in TRAIT["evidence"]]
        if incomplete
        else TRAIT["evidence"]
    )
    await store.call(
        "finish_run",
        run,
        scope,
        batch,
        [TRAIT | {"kind": "jargon", "evidence": evidence}],
        DEFAULTS | {"activation_mode": "auto"},
        "",
    )
    with sqlite3.connect(store.path) as db:
        db.execute(
            "UPDATE traits SET scope=?,evidence_count=?",
            (SHARED_SCOPE, 40 if incomplete else 2),
        )
        db.execute("UPDATE trait_aliases SET scope=?", (SHARED_SCOPE,))
    await store.call("initialize")
    assert len((await store.call("state", SHARED_SCOPE))["traits"]) == 1
    assert not (await store.call("guidance", scope, True))["traits"]
