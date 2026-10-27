"""Keep local definitions separate even when other habits are shared."""


import pytest

from astrbot_plugin_ovenheart.core.settings import SHARED_SCOPE
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
