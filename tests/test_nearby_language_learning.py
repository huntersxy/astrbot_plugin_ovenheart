"""Check evidence-local timing summaries through real capture and learning."""

import json
import sqlite3
import time
from contextlib import closing
from types import SimpleNamespace

from astrbot.core.message.components import Reply

from astrbot_plugin_ovenheart.core.learning import LEARNING_RHYTHM_RULE
from tests.test_runtime import event

pytest_plugins = ["tests.test_runtime"]


async def test_nearby_groups_preserve_speakers_sources_and_custom_review_policy(plugin):
    plugin.settings.update(
        target_senders=["alice", "bob"],
        blocked_senders=["blocked"],
        learn_personality=False,
        learn_behavior=False,
        learn_jargon=False,
        learning_review_enabled=True,
    )
    plugin.prompts["learning"] = "Custom learning instructions."
    plugin.prompts["review"] = "Custom review instructions."
    started = time.time() - 200
    captured_times = []
    for sender, offsets in (
        ("alice", [0, 10, 20, 35]),
        ("bob", [0, 40, 80, 120]),
        ("blocked", [0, 1, 2, 3]),
        ("outsider", [0, 1, 2, 3]),
    ):
        for index, offset in enumerate(offsets):
            incoming = event(f"{sender}-{index}", f"补充说明{index}", sender=sender)
            incoming.created_at = started + offset
            incoming.message_obj.message = [
                Reply(
                    id="quote", sender_id="quoted", message_str="引用不能变成近时发言"
                )
            ]
            await plugin.capture(incoming)
            captured_times.append((started + offset, f"{sender}-{index}"))
    await plugin.capture(event("self", "机器人自己不作为证据", sender="bot"))
    # Capture persists its own wall clock; seed historical persisted timestamps
    # rather than assuming the event timestamp is the stored sampling time.
    with closing(sqlite3.connect(plugin.store.path)) as db, db:
        db.executemany(
            "UPDATE messages SET created=? WHERE source_id=?", captured_times
        )
    scope = (await plugin.store.call("state"))["scopes"][0]["id"]
    seen = []

    async def complete(**kwargs):
        payload = json.loads(kwargs["prompt"])
        seen.append(payload)
        assert payload["language_feature_policy"] == LEARNING_RHYTHM_RULE
        if "candidates" in payload:
            return SimpleNamespace(completion_text='{"approved_ids":[1],"flagged":[]}')
        ids = [m["id"] for m in payload["observations"] if m["sender"] == "alice"]
        return SimpleNamespace(
            completion_text=json.dumps(
                {
                    "traits": [
                        {
                            "kind": "expression",
                            "situation": "逐项补充说明时",
                            "content": "用短句逐步补充，不把单句长度当成整次说明的上限",
                            "confidence": 0.95,
                            "evidence_ids": ids[:3],
                        }
                    ]
                }
            )
        )

    plugin.context.llm_generate.side_effect = complete
    result = await plugin.learn(scope)
    assert result["status"] == "success" and result["count"] == 1
    extraction, review = seen
    features = {f["sender"]: f for f in extraction["language_features"]}
    observations = {m["id"]: m for m in extraction["observations"]}
    assert set(features) == {"alice", "bob"}
    assert len(observations) == 8
    group = features["alice"]["nearby_groups"][0]
    assert len(group["evidence_ids"]) == 3
    assert group["span_seconds"] == 20
    assert group["captured_chars"] == sum(
        len(observations[i]["text"]) for i in group["evidence_ids"]
    )
    assert all(observations[i]["sender"] == "alice" for i in group["evidence_ids"])
    assert features["bob"]["nearby_groups"] == []
    assert {m["id"] for m in review["observations"]} == set(group["evidence_ids"])
    stored = (await plugin.store.call("state", scope))["traits"][0]
    assert stored["evidence_count"] == 3 and stored["support_count"] == 1
    assert all(e["sender"] == "alice" for e in stored["evidence"])
    assert plugin.context.llm_generate.await_count == 2


async def test_whole_group_bounds_do_not_chain_time_windows_or_change_vocabulary_pass(
    plugin,
):
    plugin.settings.update(
        batch_size=100, learn_personality=False, learn_behavior=False
    )
    started = time.time() - 600
    offsets = [0, 30, 31, *range(100, 109), 200, 201, 300, 301, *range(400, 408)]
    for index, offset in enumerate(offsets):
        incoming = event(
            f"alice-{index}", "很长的原始消息" * 200 if index == 0 else f"说明{index}"
        )
        incoming.created_at = started + offset
        await plugin.capture(incoming)
    with closing(sqlite3.connect(plugin.store.path)) as db, db:
        db.executemany(
            "UPDATE messages SET created=? WHERE source_id=?",
            [(started + offset, f"alice-{i}") for i, offset in enumerate(offsets)],
        )
    scope = (await plugin.store.call("state"))["scopes"][0]["id"]
    seen = []

    async def complete(**kwargs):
        payload = json.loads(kwargs["prompt"])
        seen.append(payload)
        return SimpleNamespace(completion_text='{"traits":[]}')

    plugin.context.llm_generate.side_effect = complete
    assert (await plugin.learn(scope))["status"] == "success"
    language = next(p for p in seen if p["enabled_kinds"] == ["expression"])
    words = next(p for p in seen if p["enabled_kinds"] == ["jargon"])
    feature = language["language_features"][0]
    assert len(feature["nearby_groups"]) == 3
    assert [len(g["evidence_ids"]) for g in feature["nearby_groups"]] == [2, 2, 8]
    assert feature["nearby_groups_omitted"] == 2
    assert feature["nearby_window_seconds"] == 30
    assert all(g["span_seconds"] <= 30 for g in feature["nearby_groups"])
    assert language["observations"] == words["observations"]
    assert len(language["observations"]) == len(offsets)
    assert len(language["observations"][0]["text"]) == 1200
    assert not words["language_features"] and not words["language_feature_policy"]
    assert plugin.context.llm_generate.await_count == 2
