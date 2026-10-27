"""Observe admitted final requests, with stable identity after optional failures."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.pipeline import context_utils
from astrbot.core.star.star_handler import (
    EventType,
    StarHandlerMetadata,
    StarHandlerRegistry,
    star_handlers_registry,
)

from tests.test_conversation_planner import action, send_reply
from tests.test_participation import requests
from tests.test_runtime import event, sources

pytest_plugins = ["tests.test_participation", "tests.test_conversation_planner"]


def observe_requests(plugin, monkeypatch, snapshots, *, tail_first):
    module = type(plugin).__module__

    async def tail(incoming, request):
        snapshots.append(
            {
                "system": request.system_prompt,
                "tools": request.func_tool.openai_schema() if request.func_tool else [],
                "contexts": deepcopy(request.contexts),
                "extra": [part.text for part in request.extra_user_content_parts],
            }
        )

    observer = StarHandlerMetadata(
        event_type=EventType.OnLLMRequestEvent,
        handler_full_name="cache_observer_tail",
        handler_name="tail",
        handler_module_path="cache_observer",
        handler=tail,
        event_filters=[],
        extras_configs={"priority": -100000},
    )
    handlers = [
        replace(meta, handler=meta.handler.__get__(plugin, type(plugin)))
        for meta in star_handlers_registry.get_handlers_by_module_name(module)
        if meta.event_type == EventType.OnLLMRequestEvent
    ]
    registry = StarHandlerRegistry()
    for meta in [observer, *handlers] if tail_first else [*handlers, observer]:
        registry.append(meta)
    monkeypatch.setattr(context_utils, "star_handlers_registry", registry)
    for key in [module, "cache_observer"]:
        monkeypatch.setitem(
            context_utils.star_map,
            key,
            SimpleNamespace(name=key, activated=True, display_name=key),
        )


@pytest.mark.parametrize("tail_first", [True, False])
async def test_planned_and_direct_routes_have_the_same_final_cached_system(
    native_planner, monkeypatch, tail_first
):
    from astrbot_plugin_ovenheart.core.prompts import REPLY_EXECUTION_RULE

    plugin, provider = native_planner
    plugin.settings["guidance_delivery"] = "cached"
    provider.text_chat.side_effect = [
        action("reply", msg_id="1", reply_reference="接住这轮话题，不补收尾评价"),
        action("stop"),
    ]
    planned = event("planned-prefix", text="这轮话题")
    replies = plugin.participate(planned)
    first = await anext(replies)
    first.system_prompt = "固定宿主人格"
    snapshots = []
    observe_requests(plugin, monkeypatch, snapshots, tail_first=tail_first)
    assert not await context_utils.call_event_hook(planned, EventType.OnLLMRequestEvent, first)
    await send_reply(plugin, planned, "嗯，接着说")
    with pytest.raises(StopAsyncIteration):
        await anext(replies)
    direct = event("direct-prefix", text="直接叫你")
    direct.is_at_or_wake_command = True
    second = ProviderRequest(prompt=direct.get_message_str(), system_prompt="固定宿主人格")
    assert not await context_utils.call_event_hook(direct, EventType.OnLLMRequestEvent, second)
    assert len(snapshots) == 2
    assert snapshots[0]["system"] == snapshots[1]["system"]
    assert snapshots[0]["system"].count(REPLY_EXECUTION_RULE) == 1
    assert "接住这轮话题，不补收尾评价" not in snapshots[0]["system"]


@pytest.mark.parametrize("tail_first", [True, False])
async def test_cancelled_request_never_updates_cache_baseline(
    participant, monkeypatch, tail_first
):
    trigger = event("obsolete-plan")
    request = (await requests(participant, trigger))[0]
    # A real plan can expire while native recall/selection is awaiting a service.
    participant.settings["participation_mode"] = "off"
    snapshots = []
    observe_requests(participant, monkeypatch, snapshots, tail_first=tail_first)
    assert await context_utils.call_event_hook(
        trigger, EventType.OnLLMRequestEvent, request
    )
    assert trigger.is_stopped()
    assert snapshots == []


@pytest.mark.parametrize("tail_first", [True, False])
async def test_observer_sees_final_allowed_tools_and_normal_changes(
    participant, monkeypatch, tail_first
):
    participant.settings.update(
        autonomous_tool_policy="allowlist", autonomous_tools=["retained"]
    )
    trigger = event("admitted-plan")
    request = (await requests(participant, trigger))[0]
    registered = ToolSet(
        [
            FunctionTool(name=name, description=name, parameters={})
            for name in ["denied", "retained"]
        ]
    )
    request.func_tool = registered
    snapshots = []
    observe_requests(participant, monkeypatch, snapshots, tail_first=tail_first)
    assert not await context_utils.call_event_hook(
        trigger, EventType.OnLLMRequestEvent, request
    )
    assert snapshots[0]["tools"] == request.func_tool.openai_schema()
    assert [t.name for t in request.func_tool.tools] == ["retained"]
    assert [t.name for t in registered.tools] == ["denied", "retained"]
    assert snapshots[0]["extra"]
    # A later direct request genuinely loses a tool (for example plugin unload).
    # It must still reach diagnostics with the new set; no schema is pinned.
    current = event("direct-after-unload")
    current.is_at_or_wake_command = True
    later = ProviderRequest(prompt="本轮输入", func_tool=ToolSet([]))
    assert not await context_utils.call_event_hook(
        current, EventType.OnLLMRequestEvent, later
    )
    assert snapshots[1]["tools"] == []
    assert snapshots[1]["tools"] != snapshots[0]["tools"]


async def test_optional_selector_error_keeps_cached_system_and_native_history(plugin):
    plugin.settings.update(guidance_delivery="cached")
    scope = await sources(plugin)
    plugin.settings["learning_enabled"] = False
    await plugin.store.call("mutate", scope, "profile", {"behavior": "固定行为准则"})
    history = [{"role": "user", "content": "原生历史"}]
    systems = []
    for index, failure in enumerate([None, OSError("optional retrieval failed")]):
        plugin.retriever.select = AsyncMock(
            side_effect=failure,
            return_value={"traits": [], "mode": "lexical", "message": "fixture"},
        )
        trigger = event(f"selector-{index}")
        request = ProviderRequest(
            prompt=trigger.message_str,
            system_prompt="固定宿主人格",
            contexts=deepcopy(history),
        )
        await plugin.apply_persona(trigger, request)
        assert "固定行为准则" in request.system_prompt
        assert request.contexts == history
        assert not request.extra_user_content_parts
        systems.append(request.system_prompt)
        if failure:
            assert (
                trigger.get_extra("ovenheart_trace_metrics")["retrieval_mode"]
                == "error"
            )
    assert systems[0] == systems[1]


@pytest.mark.parametrize("stop", ["global", "profile", "cancel"])
async def test_selector_fallback_never_bypasses_pause_or_cancellation(plugin, stop):
    scope = await sources(plugin)
    plugin.settings.update(guidance_delivery="cached", learning_enabled=False)
    await plugin.store.call("mutate", scope, "profile", {"behavior": "固定行为准则"})

    async def failing(*args, **kwargs):
        if stop == "global":
            plugin.settings["apply_enabled"] = False
        elif stop == "profile":
            await plugin.store.call(
                "mutate", scope, "profile", {"apply_enabled": False}
            )
        else:
            raise asyncio.CancelledError
        raise OSError("optional retrieval failed")

    plugin.retriever.select = failing
    request = ProviderRequest(prompt="当前输入", system_prompt="原宿主人格")
    if stop == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await plugin.apply_persona(event("stopped-selector"), request)
    else:
        await plugin.apply_persona(event("stopped-selector"), request)
    assert request.system_prompt == "原宿主人格"
    assert not request.extra_user_content_parts
