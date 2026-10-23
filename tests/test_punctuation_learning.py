"""Ground per-speaker punctuation summaries in eligible original messages."""

import json
from types import SimpleNamespace

from astrbot.core.message.components import Reply

from tests.test_runtime import event

pytest_plugins = ["tests.test_runtime"]


async def test_punctuation_features_stay_speaker_local_and_support_grounded_samples(
    plugin,
):
    plugin.settings.update(
        target_senders=["alice", "bob"],
        blocked_senders=["blocked"],
        learn_personality=False,
        learn_behavior=False,
        learn_jargon=False,
    )
    for sender, texts in (
        ("alice", ["好家伙！！", "真的好强！！", "可以啊～", "真的吗？！"]),
        ("bob", ["知道了", "行", "继续", "可以"]),
        ("blocked", ["？？？"] * 4),
        ("outsider", ["！！！"] * 4),
    ):
        for index, text in enumerate(texts):
            source = event(f"{sender}-{index}", text=text, sender=sender)
            source.message_obj.message = [
                Reply(id="quote", sender_id="peer", message_str="不应统计的句尾？？？")
            ]
            await plugin.capture(source)
    scope = (await plugin.store.call("state"))["scopes"][0]["id"]
    seen = []

    async def complete(**kwargs):
        """Use real observation IDs for a controlled style candidate.

        Args:
            **kwargs: Native learning request parameters.

        Returns:
            One grounded expression without adding any model stage.
        """
        payload = json.loads(kwargs["prompt"])
        seen.append(payload)
        ids = [m["id"] for m in payload["observations"] if m["sender"] == "alice"]
        return SimpleNamespace(
            completion_text=json.dumps(
                {
                    "traits": [
                        {
                            "kind": "expression",
                            "situation": "轻松惊讶时",
                            "content": "用叠加的感叹号加强口语语气",
                            "confidence": 0.95,
                            "evidence_ids": ids[:2],
                            "examples": [{"evidence_id": ids[0], "text": "！！"}],
                        }
                    ]
                }
            )
        )

    plugin.context.llm_generate.side_effect = complete
    result = await plugin.learn(scope)
    assert result["status"] == "success" and result["count"] == 1
    features = {f["sender"]: f for f in seen[0]["language_features"]}
    assert set(features) == {"alice", "bob"}
    alice, bob = features["alice"], features["bob"]
    assert alice["terminal_punctuation"][0]["marks"] == "！！"
    assert alice["terminal_punctuation"][0]["messages"] == 2
    assert alice["no_terminal_punctuation"]["messages"] == 0
    assert bob["terminal_punctuation"] == []
    assert bob["no_terminal_punctuation"]["messages"] == 4
    observations = {m["id"]: m for m in seen[0]["observations"]}
    for speaker, feature in features.items():
        for group in feature["terminal_punctuation"]:
            assert group["messages"] == len(group["evidence_ids"])
            assert all(
                observations[i]["sender"] == speaker for i in group["evidence_ids"]
            )
            assert all(
                observations[i]["text"].endswith(group["marks"])
                for i in group["evidence_ids"]
            )
    trait = (await plugin.store.call("state", scope))["traits"][0]
    assert trait["examples"][0]["text"] == "！！"
    plugin.context.llm_generate.assert_awaited_once()


async def test_summary_bounds_distinguish_omission_and_leave_the_raw_batch_unchanged(
    plugin,
):
    plugin.settings.update(
        learn_personality=False, learn_behavior=False, learn_jargon=False
    )
    for i, text in enumerate(
        [
            "句尾！",
            "句尾。",
            "句尾？",
            "句尾，",
            "句尾；",
            "句尾：",
            "句尾～",
            "x" * 1200 + "。",
            "好家伙" + "！" * 13,
            "没有标点",
        ]
    ):
        await plugin.capture(event(f"bounds-{i}", text=text))
    scope = (await plugin.store.call("state"))["scopes"][0]["id"]
    plugin.context.llm_generate.side_effect = None
    plugin.context.llm_generate.return_value = SimpleNamespace(
        completion_text='{"traits":[]}'
    )
    assert (await plugin.learn(scope))["status"] == "success"
    payload = json.loads(plugin.context.llm_generate.await_args.kwargs["prompt"])
    feature = payload["language_features"][0]
    assert len(feature["terminal_punctuation"]) == 6
    assert feature["other_terminal_punctuation_messages"] == 1
    assert [r["marks"] for r in feature["terminal_punctuation"]] == sorted(
        ["！", "。", "？", "，", "；", "：", "～"]
    )[:6]
    by_source = {m["source_id"]: m for m in await plugin.store.call("batch", scope, 30)}
    assert by_source == {}  # Successful analysis consumes only the original rows.
    raw = {m["text"]: m["id"] for m in payload["observations"]}
    assert feature["terminal_summary_omitted_ids"] == [
        raw["x" * 1200],
        raw["好家伙" + "！" * 13],
    ]
    assert feature["no_terminal_punctuation"] == {
        "messages": 1,
        "evidence_ids": [raw["没有标点"]],
    }
    assert len(payload["observations"]) == 10
    plugin.context.llm_generate.assert_awaited_once()
