"""Verify the selector receives native conversational text and live group turns."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

pytest.importorskip("astrbot.api")

from astrbot.api.provider import ProviderRequest  # noqa: E402
from astrbot.core.agent.message import TextPart  # noqa: E402
from astrbot.core.message.components import Plain, Reply  # noqa: E402
from astrbot.core.pipeline import context_utils  # noqa: E402
from astrbot.core.star.star_handler import (  # noqa: E402
    EventType,
    StarHandlerRegistry,
    star_handlers_registry,
)

from tests.test_participation import decision, requests  # noqa: E402
from tests.test_runtime import completion, event, sources  # noqa: E402

pytest_plugins = ["tests.test_participation"]


@pytest.fixture
async def selector(plugin):
    """Prepare real scoped habits before inspecting native selector requests.

    Args:
        plugin: Initialized SDK-backed plugin fixture.

    Returns:
        Plugin with approved habits and a deterministic selector response.
    """
    plugin.context.llm_generate.side_effect = completion
    scope = await sources(plugin)
    assert (await plugin.learn(scope))["status"] == "success"
    state = await plugin.store.call("state", scope)
    for trait in state["traits"]:
        await plugin.store.call(
            "mutate", scope, "trait", {"id": trait["id"], "status": "active"}
        )
    plugin.context.llm_generate.side_effect = None
    plugin.context.llm_generate.reset_mock()
    plugin.settings["selection_mode"] = "contextual"
    plugin.context.llm_generate.return_value = SimpleNamespace(
        completion_text='{"selected_ids":[]}'
    )
    return plugin


async def test_text_parts_reach_selector_without_media_thinking_or_temporary_data(
    selector,
):
    visible = TextPart(text="这会儿只想倾诉，不需要建议。")
    temporary = TextPart(text="PRIVATE_TEMP_CONTEXT").mark_as_temp()
    request = ProviderRequest(
        prompt="嗯，继续",
        contexts=[
            {
                "role": "user",
                "content": [
                    visible.model_dump_for_context(),
                    temporary.model_dump_for_context(),
                    {"type": "image_url", "image_url": {"url": "PRIVATE_IMAGE_URL"}},
                    {"type": "think", "think": "PRIVATE_THINKING"},
                ],
            },
            {"role": "assistant", "content": [{"type": "text", "text": "我听着。"}]},
            {"role": "tool", "content": "PRIVATE_TOOL_RESULT"},
            {"role": "system", "content": "PRIVATE_SYSTEM_RULE"},
        ],
    )
    await selector.apply_persona(event("parts"), request)
    payload = json.loads(selector.context.llm_generate.await_args.kwargs["prompt"])
    assert "user: 这会儿只想倾诉，不需要建议。" in payload["conversation"]
    assert "assistant: 我听着。" in payload["conversation"]
    assert "PRIVATE_" not in payload["conversation"]
    assert request.contexts[-1]["content"] == "PRIVATE_SYSTEM_RULE"


async def test_recent_context_is_bounded_without_truncating_current_input(selector):
    request = ProviderRequest(
        prompt="CURRENT_INPUT_" + "短" * 980,
        contexts=[{"role": "user", "content": "旧" * 10000}] * 6,
    )
    await selector.apply_persona(event("budget"), request)
    payload = json.loads(selector.context.llm_generate.await_args.kwargs["prompt"])
    assert len(payload["conversation"]) <= 2400
    assert request.prompt in payload["conversation"]


async def test_live_context_from_a_different_scope_is_never_queried(selector):
    incoming = event("different-scope")
    incoming.set_extra(
        "ovenheart_decision",
        {
            "scope": "different-persona-scope",
            "intent": "PRIVATE_OTHER_INTENT",
            "messages": [{"sender": "other", "text": "PRIVATE_OTHER_CHAT"}],
        },
    )
    await selector.apply_persona(incoming, ProviderRequest(prompt="继续吧"))
    payload = json.loads(selector.context.llm_generate.await_args.kwargs["prompt"])
    assert "PRIVATE_OTHER" not in payload["conversation"]


@pytest.mark.parametrize("chain_only", [False, True])
async def test_quoted_word_is_recalled_even_when_selector_chooses_no_style(
    selector, chain_only
):
    incoming = event(
        "quote-word", text="这个是什么意思？" + ("补充" * 700 if not chain_only else "")
    )
    incoming.message_obj.message = [
        Reply(
            id="outside-window",
            sender_nickname="小林",
            message_str="" if chain_only else "好家伙",
            chain=[Plain("好家伙")] if chain_only else [],
        )
    ]
    request = ProviderRequest(prompt=incoming.get_message_str())
    await selector.apply_persona(incoming, request)
    payload = json.loads(selector.context.llm_generate.await_args.kwargs["prompt"])
    assert "好家伙" in payload["conversation"]
    assert len(payload["conversation"]) <= 2400
    application = incoming.get_extra("ovenheart_application")
    assert application
    state = await selector.store.call("state", application["scope"])
    word = next(t for t in state["traits"] if t["kind"] == "jargon")
    assert word["id"] in application["ids"]


async def test_older_reply_target_pins_its_word_without_pinning_side_chatter(
    participant, selector
):
    participant.settings.update(
        participation_min_messages=2, guidance_delivery="cached"
    )
    participant.context.llm_generate.side_effect = [
        decision(target_sequence=1),
        SimpleNamespace(completion_text='{"selected_ids":[]}'),
    ]
    earlier = event("earlier-word", text="好家伙在这里是什么意思？", sender="alice")
    latest = event("side-chatter", text="小林，晚饭想吃什么？", sender="bob")
    assert await requests(participant, earlier) == []
    request = (await requests(participant, latest))[0]
    await participant.apply_persona(latest, request)
    payload = json.loads(participant.context.llm_generate.await_args.kwargs["prompt"])
    assert "当前输入：好家伙在这里是什么意思？" in payload["conversation"]
    assert "回应对象：测试用户" in payload["conversation"]
    application = latest.get_extra("ovenheart_application")
    assert application
    state = await participant.store.call("state", application["scope"])
    word = next(t for t in state["traits"] if t["kind"] == "jargon")
    assert word["id"] in application["ids"]


async def test_side_chatter_and_planner_intent_cannot_pin_a_word_to_another_target(
    participant, selector
):
    participant.settings["participation_min_messages"] = 2
    participant.context.llm_generate.side_effect = [
        decision(target_sequence=1, intent="讨论晚饭，不使用好家伙"),
        SimpleNamespace(completion_text='{"selected_ids":[]}'),
    ]
    earlier = event("dinner", text="晚饭吃什么？", sender="alice")
    latest = event("other-topic", text="好家伙又来了", sender="bob")
    latest.message_obj.message = [Reply(id="other-quote", message_str="好家伙")]
    assert await requests(participant, earlier) == []
    request = (await requests(participant, latest))[0]
    await participant.apply_persona(latest, request)
    assert not (latest.get_extra("ovenheart_application") or {}).get("ids")


async def test_native_hook_order_shares_live_context_with_habit_selection(
    participant, selector, monkeypatch
):
    participant.settings["participation_min_messages"] = 2
    participant.context.llm_generate.side_effect = [
        decision(),
        SimpleNamespace(completion_text='{"selected_ids":[]}'),
    ]
    earlier = event("group-context", "我现在只想倾诉，请先别给建议。", sender="alice")
    latest = event("group-current", "嗯，继续", sender="bob")
    assert await requests(participant, earlier) == []
    request = (await requests(participant, latest))[0]
    request.contexts = [{"role": "assistant", "content": "较早的游戏闲聊。"}]
    registry = StarHandlerRegistry()
    module = type(participant).__module__
    for metadata in star_handlers_registry.get_handlers_by_module_name(module):
        if metadata.event_type == EventType.OnLLMRequestEvent:
            registry.append(
                replace(
                    metadata,
                    handler=metadata.handler.__get__(participant, type(participant)),
                )
            )
    monkeypatch.setattr(context_utils, "star_handlers_registry", registry)
    monkeypatch.setitem(
        context_utils.star_map,
        module,
        SimpleNamespace(
            name="astrbot_plugin_ovenheart",
            activated=True,
            display_name="OvenHeart",
        ),
    )
    assert [h.handler_name for h in registry] == [
        "coordinate_direct_request",
        "apply_persona",
        "apply_participation",
    ]
    assert not await context_utils.call_event_hook(
        latest, EventType.OnLLMRequestEvent, request
    )
    payload = json.loads(participant.context.llm_generate.await_args.kwargs["prompt"])
    assert earlier.get_message_str() in payload["conversation"]
    assert "当前输入：嗯，继续" in payload["conversation"]
    assert request.extra_user_content_parts
    assert not latest.is_stopped()
