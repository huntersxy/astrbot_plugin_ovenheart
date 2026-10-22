"""Exercise short human responses through native capture, extraction and review."""

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("astrbot.api")

from astrbot.core.message.components import Reply  # noqa: E402

from tests.test_runtime import completion, event  # noqa: E402

pytest_plugins = ["tests.test_runtime"]


@pytest.mark.parametrize("targeted", [False, True])
async def test_single_character_replies_keep_context_and_existing_evidence_rules(
    plugin, targeted
):
    plugin.settings.update(
        target_senders=["alice"] if targeted else [],
        blocked_senders=["mallory"],
        learning_review_enabled=True,
    )
    first = event("short-0", text="嗯")
    first.message_obj.message = [
        Reply(id="bot-question", sender_id="bot", message_str="你是想先吐槽吗？")
    ]
    await plugin.capture(first)
    for index, text in enumerate(("好", "？", "6", "👍"), start=1):
        await plugin.capture(event(f"short-{index}", text=text))
    await plugin.capture(event("peer-short", text="哈", sender="bob"))
    await plugin.capture(event("blocked-short", text="啊", sender="mallory"))
    await plugin.capture(event("own-short", text="哦", sender="bot"))
    await plugin.capture(event("empty-short", text=" \n "))
    await plugin.capture(event("command-short", text="/"))
    scope, _, _ = await plugin.resolve_scope(first)
    batch = await plugin.store.call(
        "batch", scope, 30, plugin.settings["target_senders"], ["mallory"]
    )
    assert len(batch) == (5 if targeted else 6)
    assert {m["text"] for m in batch if m["sender"] == "alice"} == {
        "嗯",
        "好",
        "？",
        "6",
        "👍",
    }
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

    plugin.context.llm_generate = generate
    assert (await plugin.learn(scope))["status"] == "success" and len(seen) == 3
    ids = {m["id"] for m in batch}
    for payload in seen:
        assert {m["id"] for m in payload["observations"]} <= ids
        assert all(
            m["sender"] not in ("mallory", "bot") for m in payload["observations"]
        )
        quotes = [
            c
            for c in payload["conversation_context"]
            if c.get("relation") == "reply_to"
        ]
        assert len(quotes) == 1 and quotes[0]["self"]
        assert quotes[0]["text"] == "你是想先吐槽吗？"
        if "expression" in payload.get("enabled_kinds", []):
            stats = next(
                f for f in payload["language_features"] if f["sender"] == "alice"
            )
            assert stats["messages"] == 5 and stats["median_chars"] == 1
            assert set(stats["evidence_ids"]) <= ids
        elif payload.get("enabled_kinds") == ["jargon"]:
            assert not payload["language_features"]
    state = await plugin.store.call("state", scope)
    assert state["scope"]["pending"] == 0
    assert all(len(t["evidence"]) >= 2 for t in state["traits"])
    assert all(e["id"] in ids for t in state["traits"] for e in t["evidence"])
    assert not state["applications"]
