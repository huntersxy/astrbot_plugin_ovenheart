"""Preserve concrete wording in learning references and atomic merge checks."""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from tests.test_learning import candidates
from tests.test_runtime import event, sources

pytest_plugins = ["tests.test_runtime"]


@pytest.fixture
async def sample_learning(plugin):
    """Learn one sampled expression and prepare a distinct reinforcement batch.

    Args:
        plugin: Native plugin with an isolated temporary database.

    Returns:
        Plugin, source scope, captured model inputs and concurrent-change switch.
    """
    plugin.settings.update(
        learn_personality=False,
        learn_jargon=False,
        learn_behavior=False,
        learning_review_enabled=True,
        activation_mode="auto",
        selection_mode="lexical",
    )
    plugin.prompts["learning"] = "旧习惯对照：{{known_traits}}"
    seen, control = [], {"change": False}

    async def complete(**kwargs):
        """Answer extraction/review and optionally emulate another committed update.

        Args:
            **kwargs: Actual native model request.

        Returns:
            A controlled completion with actual source-linked samples.
        """
        payload = json.loads(kwargs["prompt"])
        seen.append(payload)
        if "candidates" in payload:
            target = payload["candidates"][0]["merge_target"]
            if target and control["change"]:
                with sqlite3.connect(plugin.store.path) as db:
                    original = json.loads(
                        db.execute(
                            "SELECT examples FROM traits WHERE id=?", (target["id"],)
                        ).fetchone()[0]
                    )
                    changed = (
                        original[0]
                        if original
                        else {
                            "id": 1,
                            "sender": "alice",
                            "created": 1,
                            "source_id": "1",
                        }
                    ) | {"text": "你又看出来了"}
                    db.execute(
                        "UPDATE traits SET examples=? WHERE id=?",
                        (json.dumps([changed]), target["id"]),
                    )
            body = {"approved_ids": [1], "flagged": []}
        else:
            known = payload["known_traits"]
            ids = [m["id"] for m in payload["observations"]]
            patch = {
                "evidence_ids": ids,
                "examples": [
                    {"evidence_id": ids[0], "text": "还是你懂我" if known else "好家伙"}
                ],
            }
            if known:
                patch["merge_id"] = known[0]["id"]
            body = json.loads(candidates(**patch))
            if known:
                assert json.dumps(known, ensure_ascii=False) in kwargs["system_prompt"]
        return SimpleNamespace(completion_text=json.dumps(body))

    plugin.context.llm_generate.side_effect = complete
    scope = await sources(plugin)
    assert (await plugin.learn(scope))["status"] == "success"
    await plugin.capture(event("3", text="还是你懂我，这梗接得好"))
    await plugin.capture(event("4", text="好家伙，还是你懂我"))
    return plugin, scope, seen, control


async def test_extract_and_review_compare_old_samples_without_source_metadata(
    sample_learning,
):
    plugin, scope, seen, _ = sample_learning
    assert (await plugin.learn(scope))["status"] == "success"
    reference = seen[-2]["known_traits"][0]
    assert set(reference) == {
        "id",
        "kind",
        "situation",
        "content",
        "status",
        "examples",
    }
    assert reference["examples"] == ["好家伙"]
    assert seen[-1]["candidates"][0]["merge_target"] == reference
    assert seen[-1]["candidates"][0]["examples"][0]["text"] == "还是你懂我"
    trait = (await plugin.store.call("state", scope))["traits"][0]
    assert [e["text"] for e in trait["examples"]] == ["好家伙", "还是你懂我"]
    assert trait["support_count"] == 2 and trait["status"] == "active"
    assert plugin.context.llm_generate.await_count == 4


@pytest.mark.parametrize("empty_before", [False, True])
async def test_sample_only_changes_during_review_cancel_the_stale_merge(
    sample_learning, empty_before
):
    plugin, scope, seen, control = sample_learning
    if empty_before:
        with sqlite3.connect(plugin.store.path) as db:
            db.execute("UPDATE traits SET examples='[]'")
    control["change"] = True
    result = await plugin.learn(scope)
    assert result["status"] == "error" and "合并目标在自检后已修改" in result["message"]
    assert seen[-1]["candidates"][0]["merge_target"]["examples"] == (
        [] if empty_before else ["好家伙"]
    )
    state = await plugin.store.call("state", scope)
    assert state["runs"][0]["status"] == "error"
    assert state["traits"][0]["support_count"] == 1
    assert [e["text"] for e in state["traits"][0]["examples"]] == ["你又看出来了"]
    assert {m["source_id"] for m in await plugin.store.call("batch", scope, 30)} == {
        "3",
        "4",
    }
