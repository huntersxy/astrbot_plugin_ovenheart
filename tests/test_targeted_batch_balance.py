"""Cover quieter explicitly selected speakers within the same bounded batch."""

import json
from collections import Counter
from types import SimpleNamespace

import pytest

from tests.test_runtime import event
from tests.test_store import DEFAULTS

pytest_plugins = ["tests.test_store", "tests.test_runtime"]


@pytest.fixture
async def speaker_queue(store):
    """Queue a frequent speaker ahead of a quieter target and an excluded user.

    Args:
        store: Temporary SQLite fixture.

    Returns:
        Store and native source scope.
    """
    scope = "a" * 24
    for sender, count in (("alice", 40), ("bob", 4), ("carol", 3)):
        for index in range(count):
            await store.call(
                "capture",
                scope,
                "qq:GroupMessage:123",
                "default",
                "fixture",
                f"{sender}-{index}",
                sender,
                f"{sender} 的原话 {index}",
                DEFAULTS,
            )
    return store, scope


@pytest.mark.parametrize("minimum,size,each", [(2, 8, 4), (3, 6, 3)])
async def test_selected_speakers_get_evidence_groups_and_only_selected_rows_are_consumed(
    speaker_queue, minimum, size, each
):
    store, scope = speaker_queue
    before = await store.call("batch", scope, 100)
    batch = await store.call("batch", scope, size, ["alice", "bob"], [], minimum)
    assert Counter(m["sender"] for m in batch) == {"alice": each, "bob": each}
    assert [m["id"] for m in batch] == sorted(m["id"] for m in batch)
    run = await store.call("start_run", scope)
    await store.call(
        "finish_run",
        run,
        scope,
        batch,
        [],
        DEFAULTS | {"min_evidence": minimum, "target_senders": ["alice", "bob"]},
        "fixture",
    )
    after = await store.call("batch", scope, 100)
    assert {m["id"] for m in after} == {m["id"] for m in before} - {
        m["id"] for m in batch
    }


async def test_all_speaker_single_target_and_blacklist_keep_existing_fifo(
    speaker_queue,
):
    store, scope = speaker_queue
    ordinary = await store.call("batch", scope, 8)
    selected = await store.call("batch", scope, 8, ["alice"])
    blocked = await store.call("batch", scope, 8, ["alice", "bob"], ["bob"], 2)
    assert ordinary == selected == blocked
    assert [m["source_id"] for m in ordinary] == [f"alice-{i}" for i in range(8)]


async def test_native_learning_receives_both_selected_objects_without_another_model_call(
    plugin,
):
    plugin.settings.update(
        target_senders=["alice", "bob"],
        batch_size=8,
        learn_personality=False,
        learn_jargon=False,
        learn_behavior=False,
    )
    for sender, count in (("alice", 40), ("bob", 4)):
        for index in range(count):
            await plugin.capture(
                event(f"{sender}-{index}", text="先听你说说，我在。", sender=sender)
            )
    scope = (await plugin.store.call("state"))["scopes"][0]["id"]
    seen = []

    async def complete(**kwargs):
        """Return separate evidence-linked expression rules for both actual speakers.

        Args:
            **kwargs: Native learning request.

        Returns:
            Two controlled candidate objects without a live provider.
        """
        payload = json.loads(kwargs["prompt"])
        seen.append(payload)
        return SimpleNamespace(
            completion_text=json.dumps(
                {
                    "traits": [
                        {
                            "kind": "expression",
                            "situation": "轻松交流时",
                            "content": content,
                            "confidence": 0.95,
                            "evidence_ids": [
                                m["id"]
                                for m in payload["observations"]
                                if m["sender"] == sender
                            ],
                        }
                        for sender, content in (
                            ("alice", "先简短回应，再接住问题"),
                            ("bob", "先回应感受，再等待对方补充"),
                        )
                    ]
                }
            )
        )

    plugin.context.llm_generate.side_effect = complete
    result = await plugin.learn(scope)
    assert result["status"] == "success" and result["count"] == 2
    assert Counter(m["sender"] for m in seen[0]["observations"]) == {
        "alice": 4,
        "bob": 4,
    }
    assert {f["sender"]: f["messages"] for f in seen[0]["language_features"]} == {
        "alice": 4,
        "bob": 4,
    }
    plugin.context.llm_generate.assert_awaited_once()
    state = await plugin.store.call("state", scope)
    assert state["scope"]["pending"] == 36
    assert all(len({e["sender"] for e in t["evidence"]}) == 1 for t in state["traits"])
