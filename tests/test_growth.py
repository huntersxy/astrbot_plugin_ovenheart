"""Verify evidence accumulation, old-database migration and rollback semantics."""

import json

import pytest

from astrbot_plugin_ovenheart.core.learning import parse_candidates
from astrbot_plugin_ovenheart.core.settings import DEFAULTS
from astrbot_plugin_ovenheart.core.store import Store

SCOPE = "a" * 24


@pytest.fixture
async def store(tmp_path):
    instance = Store(tmp_path / "persona.sqlite3")
    await instance.call("initialize")
    return instance


async def batch(store, number, settings=None, **patch):
    """Commit an independent source batch describing a repeated habit.

    Args:
        store: Temporary SQLite store.
        number: Independent source batch number.
        settings: Optional effective activation and review settings.
        **patch: Candidate fields to vary.

    Returns:
        Current scope detail.
    """
    settings = settings or DEFAULTS
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
            settings,
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
    await store.call("finish_run", run, SCOPE, sources, [trait], settings, "")
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


@pytest.mark.parametrize("kind", ["expression", "behavior", "personality"])
async def test_reviewed_non_jargon_refinements_advance_once_then_respect_manual_lock(
    store, kind
):
    first = await batch(store, 1, kind=kind)
    identifier = first["traits"][0]["id"]
    improved = "对方愿意轻松互动时，用简短反问接住玩笑"
    second = await batch(
        store,
        2,
        kind=kind,
        merge_id=identifier,
        refine=True,
        _review_approved=True,
        content=improved,
    )
    assert second["traits"][0]["content"] == improved
    assert second["traits"][0]["meaning_stage"] == 1
    assert second["traits"][0]["status"] == "pending"
    third = await batch(
        store,
        3,
        kind=kind,
        merge_id=identifier,
        refine=True,
        _review_approved=True,
        content="另一种尚未达到批次门槛的细化",
    )
    assert third["traits"][0]["content"] == improved
    await store.call(
        "mutate",
        SCOPE,
        "trait",
        {"id": identifier, "content": "人工保留的表达", "status": "active"},
    )
    fourth = await batch(
        store,
        4,
        kind=kind,
        merge_id=identifier,
        refine=True,
        _review_approved=True,
        content="不能覆盖人工底稿的提议",
    )
    assert fourth["traits"][0]["content"] == "人工保留的表达"
    assert fourth["traits"][0]["meaning_stage"] == 1


@pytest.mark.parametrize(
    "condition",
    [
        "no_flag",
        "unreviewed",
        "review_disabled",
        "active_in_review_mode",
        "low_confidence",
    ],
)
async def test_non_jargon_refinement_does_not_bypass_approval_or_activation_policy(
    store, condition
):
    settings = DEFAULTS | {"activation_mode": "auto"}
    first = await batch(store, 1, settings=settings)
    old = first["traits"][0]
    assert old["status"] == "active" and not old["manual"]
    if condition == "review_disabled":
        settings = settings | {"learning_review_enabled": False}
    if condition == "active_in_review_mode":
        settings = settings | {"activation_mode": "review"}
    second = await batch(
        store,
        2,
        settings=settings,
        merge_id=old["id"],
        content="不应自动应用的细化",
        refine=condition != "no_flag",
        _review_approved=None if condition == "unreviewed" else True,
        confidence=0.6 if condition == "low_confidence" else 0.95,
    )
    assert second["traits"][0]["content"] == old["content"]
    assert second["traits"][0]["status"] == "active"
    assert second["traits"][0]["meaning_stage"] == 0


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


@pytest.mark.parametrize(
    "old_word,new_word",
    [("『好家伙』", "“好家伙”"), ("LUX", "ＬＵＸ"), ("‘“好家伙”’", "好家伙")],
)
async def test_normalized_jargon_names_refine_after_real_parse_and_commit(
    store, old_word, new_word
):
    known = []
    for number, word in enumerate((old_word, new_word), 1):
        for index in range(2):
            await store.call(
                "capture",
                SCOPE,
                "qq:GroupMessage:1",
                "default",
                "测试",
                f"normalized:{number}:{index}",
                "user",
                f"{word}：暂缓一下，之后还会继续。",
                DEFAULTS,
            )
        sources = await store.call("batch", SCOPE, 30)
        proposed = f"{word}：" + (
            "暂缓" if number == 1 else "暂缓一下，之后仍会继续，不代表取消"
        )
        candidate = {
            "kind": "jargon",
            "situation": "约定延后时",
            "content": proposed,
            "confidence": 0.95,
            "evidence_ids": [m["id"] for m in sources],
        }
        if known:
            candidate["merge_id"] = known[0]["id"]
        parsed = parse_candidates(
            json.dumps({"traits": [candidate]}), sources, DEFAULTS, known
        )
        assert len(parsed) == 1
        run = await store.call("start_run", SCOPE)
        await store.call("finish_run", run, SCOPE, sources, parsed, DEFAULTS, "")
        known = (await store.call("state", SCOPE))["traits"]
    assert len(known) == 1 and known[0]["support_count"] == 2
    assert known[0]["meaning_stage"] == 1
    assert known[0]["content"] == proposed
    assert known[0]["evidence_count"] == 4


async def test_learning_snapshot_restores_evidence_and_suppresses_later_aliases(store):
    first = await batch(store, 1)
    identifier = first["traits"][0]["id"]
    second = await batch(store, 2, merge_id=identifier, content="换句话表达相同习惯")
    revision = second["revisions"][0]["id"]
    await store.call("mutate", SCOPE, "restore", {"id": revision})
    state = await store.call("state", SCOPE)
    assert state["traits"][0]["support_count"] == 1
    assert state["traits"][0]["evidence_count"] == 2




async def test_jargon_evidence_excerpts_stay_bounded(store):
    for number in range(20):
        state = await batch(store, number)
    habit = state["traits"][0]
    assert habit["evidence_count"] == 40 and len(habit["evidence"]) == 32
    assert habit["support_count"] == 20


@pytest.mark.parametrize("field", ["situation", "content"])
async def test_similar_text_does_not_merge_opposite_conditions_or_actions(store, field):
    first = "对方明确表示当前需要建议并愿意继续讨论时"
    opposite = "对方明确表示当前不需要建议并愿意继续讨论时"
    await batch(store, 1, **{field: first})
    state = await batch(store, 2, **{field: opposite})
    assert len(state["traits"]) == 2
    assert {t[field] for t in state["traits"]} == {first, opposite}
    assert all(t["support_count"] == 1 for t in state["traits"])


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("auto", [False, True])
async def test_duplicate_candidates_in_one_run_count_as_one_independent_batch(
    store, existing, auto
):
    if existing:
        await batch(store, 1)
    for number in range(4):
        await store.call(
            "capture",
            SCOPE,
            "qq:GroupMessage:1",
            "default",
            "测试",
            f"new:{number}",
            "user",
            "好家伙，又被你发现了",
            DEFAULTS,
        )
    sources = await store.call("batch", SCOPE, 30)
    traits = [
        {
            "kind": "expression",
            "situation": "轻松接话时",
            "content": "简短反问接住玩笑",
            "confidence": confidence,
            "evidence": sources[index : index + 2],
        }
        for index, confidence in ((0, 0.99), (2, 0.61))
    ]
    run = await store.call("start_run", SCOPE)
    settings = DEFAULTS | {"activation_mode": "auto" if auto else "review"}
    await store.call("finish_run", run, SCOPE, sources, traits, settings, "")
    habit = (await store.call("state", SCOPE))["traits"][0]
    assert habit["support_count"] == 1 + existing
    assert habit["evidence_count"] == 4 + 2 * existing
    assert habit["confidence"] == pytest.approx(
        (0.95 * existing + 0.8) / (1 + existing)
    )
    assert habit["status"] == "pending"


async def test_duplicate_jargon_candidates_do_not_advance_multiple_refinement_stages(
    store,
):
    state = await batch(store, 1, kind="jargon", content="鸽子灯：约定延后")
    identifier = state["traits"][0]["id"]
    for number in range(6):
        await store.call(
            "capture",
            SCOPE,
            "qq:GroupMessage:1",
            "default",
            "测试",
            f"jargon:{number}",
            "user",
            "鸽子灯亮了，这次约定又延后了",
            DEFAULTS,
        )
    sources = await store.call("batch", SCOPE, 30)
    traits = [
        {
            "kind": "jargon",
            "situation": "约好的活动推迟时",
            "content": f"鸽子灯：约定延后，同批释义{number}",
            "confidence": 0.95,
            "merge_id": identifier,
            "evidence": sources[number * 2 : number * 2 + 2],
        }
        for number in range(3)
    ]
    run = await store.call("start_run", SCOPE)
    await store.call("finish_run", run, SCOPE, sources, traits, DEFAULTS, "")
    habit = (await store.call("state", SCOPE))["traits"][0]
    assert habit["support_count"] == 2 and habit["meaning_stage"] == 1
    assert habit["evidence_count"] == 8
    assert habit["content"].endswith("同批释义0")
