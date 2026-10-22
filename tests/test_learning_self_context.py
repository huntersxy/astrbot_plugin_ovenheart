"""Check send-stage self context without treating generated text as evidence."""

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("astrbot.api")

from astrbot.api.provider import ProviderRequest  # noqa: E402
from astrbot.core.message.components import Reply  # noqa: E402

from tests.test_runtime import api_call, completion, event  # noqa: E402

pytest_plugins = ["tests.test_runtime"]


@pytest.mark.parametrize("targeted", [False, True])
async def test_sent_self_context_reaches_learning_and_review_without_persistence(
    plugin, targeted
):
    plugin.settings.update(
        learning_review_enabled=True,
        target_senders=["alice"] if targeted else [],
    )
    first = event("first", text="我有点不知道怎么说")
    await plugin.capture(first)
    await plugin.apply_persona(first, ProviderRequest(prompt=first.get_message_str()))
    scope, _, _ = await plugin.resolve_scope(first)
    first.set_result(first.plain_result("SELF_ONLY：你想先吐槽还是一起想办法？"))
    await plugin.record_delivery(first)
    await plugin.record_delivery(first)
    assert sum(bool(m.get("self")) for m in plugin.learning_context[scope]) == 1
    assert plugin.settings["participation_mode"] == "off"

    foreign = event("foreign", text="另一个群的问题", session="456")
    await plugin.capture(foreign)
    await plugin.apply_persona(
        foreign, ProviderRequest(prompt=foreign.get_message_str())
    )
    foreign.set_result(foreign.plain_result("FOREIGN_SELF_CONTEXT"))
    await plugin.record_delivery(foreign)
    await plugin.capture(event("second", text="想先吐槽，你听我说完吧"))
    before = await plugin.store.call("batch", scope, 30)
    assert len(before) == 2 and all(m["sender"] == "alice" for m in before)
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
    assert (await plugin.learn(scope))["status"] == "success"
    assert len(seen) == 3
    ids = {m["id"] for m in before}
    for payload in seen:
        assert {m["id"] for m in payload["observations"]} == ids
        assert "SELF_ONLY" not in json.dumps(payload["observations"])
        contexts = [c for c in payload["conversation_context"] if c.get("self")]
        assert len(contexts) == 1 and contexts[0]["sender"] == "bot"
        assert "SELF_ONLY" in contexts[0]["text"] and "id" not in contexts[0]
        assert set(contexts[0]["for_evidence_ids"]) == ids
        assert "FOREIGN_SELF_CONTEXT" not in json.dumps(payload)
    state = await plugin.store.call("state", scope)
    assert "SELF_ONLY" not in json.dumps(state)
    assert all(e["id"] in ids for t in state["traits"] for e in t["evidence"])
    assert not state["applications"]


@pytest.mark.parametrize(
    "condition", ["clear", "pause", "blocked", "expired", "persona"]
)
async def test_late_sent_self_context_respects_current_learning_boundary(
    plugin, condition
):
    incoming = event("late", text="正常的学习素材")
    await plugin.capture(incoming)
    await plugin.apply_persona(
        incoming, ProviderRequest(prompt=incoming.get_message_str())
    )
    scope, _, _ = await plugin.resolve_scope(incoming)
    incoming.set_result(incoming.plain_result("LATE_SELF_MUST_BE_EXCLUDED"))
    if condition == "clear":
        assert (
            await api_call(
                plugin.page_api.action, {"action": "clear_messages", "scope": scope}
            )
        )["status"] == "ok"
    elif condition == "pause":
        await plugin.store.call("mutate", scope, "profile", {"learning_enabled": False})
    elif condition == "blocked":
        await plugin.update_settings({"blocked_senders": ["alice"]})
    elif condition == "expired":
        for item in plugin.learning_context[scope]:
            item["created"] -= 301
    else:
        plugin.context.persona_manager.resolve_selected_persona.return_value = (
            "another",
            {"prompt": "另一个人格"},
            None,
            False,
        )
        await plugin.apply_persona(
            incoming, ProviderRequest(prompt=incoming.get_message_str())
        )
    await plugin.record_delivery(incoming)
    assert not any(m.get("self") for m in plugin.learning_context.get(scope, ()))
    if condition == "clear":
        assert scope not in plugin.learning_context


async def test_quoted_self_reply_is_marked_but_uncaptured_sends_are_not_retained(
    plugin,
):
    unrelated = event("uncaptured", text="/command")
    await plugin.capture(unrelated)
    unrelated.set_result(unrelated.plain_result("COMMAND_OUTPUT"))
    await plugin.record_delivery(unrelated)
    assert not plugin.learning_context
    for index in range(2):
        incoming = event(f"quoted-{index}", text="不是这个意思，我想先说完")
        incoming.message_obj.message = [
            Reply(id="old-bot", sender_id="bot", message_str="BOT_QUOTE_ONLY")
        ]
        await plugin.capture(incoming)
    scope, _, _ = await plugin.resolve_scope(incoming)
    assert (await plugin.learn(scope))["status"] == "success"
    payload = json.loads(plugin.context.llm_generate.await_args.kwargs["prompt"])
    contexts = payload["conversation_context"]
    assert len(contexts) == 2
    assert all(c["self"] and c["relation"] == "reply_to" for c in contexts)
    assert "BOT_QUOTE_ONLY" not in json.dumps(payload["observations"])
