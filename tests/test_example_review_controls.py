"""Remove selected style samples atomically without rewriting their source rule."""

import pytest

from astrbot_plugin_ovenheart.core.learning import parse_candidates
from tests.test_learning import candidates
from tests.test_runtime import api_call, event, sources

pytest_plugins = ["tests.test_runtime"]


@pytest.fixture
async def sampled_entry(plugin):
    """Create one real pending expression with two validated source samples.

    Args:
        plugin: Native temporary plugin fixture.

    Returns:
        Plugin, native scope and stored sampled entry.
    """
    scope = await sources(plugin)
    batch = await plugin.store.call("batch", scope, 30)
    traits = parse_candidates(
        candidates(
            evidence_ids=[m["id"] for m in batch],
            examples=[
                {"evidence_id": batch[0]["id"], "text": "好家伙"},
                {"evidence_id": batch[1]["id"], "text": "还是你懂我"},
            ],
        ),
        batch,
        plugin.settings,
    )
    run = await plugin.store.call("start_run", scope)
    await plugin.store.call(
        "finish_run", run, scope, batch, traits, plugin.settings, "fixture"
    )
    return plugin, scope, (await plugin.store.call("state", scope))["traits"][0]


async def test_remove_and_approve_preserve_other_samples_rule_and_evidence_with_restore(
    sampled_entry,
):
    plugin, scope, before = sampled_entry
    response = await api_call(
        plugin.page_api.action,
        {
            "action": "trait",
            "scope": scope,
            "payload": {
                "id": before["id"],
                "status": "active",
                "content": before["content"],
                "situation": before["situation"],
                "remove_examples": ["好家伙"],
            },
        },
    )
    assert response["status"] == "ok"
    state = await plugin.store.call("state", scope)
    current = state["traits"][0]
    assert (
        current["content"] == before["content"]
        and current["situation"] == before["situation"]
    )
    assert current["status"] == "active" and current["manual"] == 1
    assert current["examples"] == before["examples"][1:]
    assert current["evidence"] == before["evidence"]
    revision = state["revisions"][0]["id"]
    assert (
        await api_call(
            plugin.page_api.action,
            {"action": "restore", "scope": scope, "payload": {"id": revision}},
        )
    )["status"] == "ok"
    restored = (await plugin.store.call("state", scope))["traits"][0]
    assert (
        restored["examples"] == before["examples"] and restored["status"] == "pending"
    )


async def test_stale_or_invalid_sample_removals_cannot_partially_approve_or_edit(
    sampled_entry,
):
    plugin, scope, before = sampled_entry
    revision_count = len((await plugin.store.call("state", scope))["revisions"])
    for removals in (["已不存在的片段"], "好家伙", ["好家伙", "好家伙"]):
        response = await api_call(
            plugin.page_api.action,
            {
                "action": "trait",
                "scope": scope,
                "payload": {
                    "id": before["id"],
                    "status": "active",
                    "content": "不能被部分保存的改写",
                    "remove_examples": removals,
                },
            },
        )
        assert response["status"] == "error"
        state = await plugin.store.call("state", scope)
        assert (
            state["traits"][0] == before and len(state["revisions"]) == revision_count
        )


async def test_manual_sample_removal_is_not_readded_by_future_reinforcement(
    sampled_entry,
):
    plugin, scope, before = sampled_entry
    assert (
        await api_call(
            plugin.page_api.action,
            {
                "action": "trait",
                "scope": scope,
                "payload": {"id": before["id"], "remove_examples": ["好家伙"]},
            },
        )
    )["status"] == "ok"
    await plugin.capture(event("new-one", text="好家伙，你也在这里"))
    await plugin.capture(event("new-two", text="好家伙，还是你懂我"))
    batch = await plugin.store.call("batch", scope, 30)
    traits = parse_candidates(
        candidates(
            merge_id=before["id"],
            evidence_ids=[m["id"] for m in batch],
            examples=[{"evidence_id": batch[0]["id"], "text": "好家伙"}],
        ),
        batch,
        plugin.settings,
        [before],
    )
    run = await plugin.store.call("start_run", scope)
    await plugin.store.call(
        "finish_run", run, scope, batch, traits, plugin.settings, "fixture"
    )
    after = (await plugin.store.call("state", scope))["traits"][0]
    assert [e["text"] for e in after["examples"]] == ["还是你懂我"]
    assert after["manual"] == 1 and after["support_count"] == 2
    plugin.context.llm_generate.assert_not_awaited()
