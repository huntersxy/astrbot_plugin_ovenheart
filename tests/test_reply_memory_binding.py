"""Route official LivingMemory hooks without changing third-party source files."""

import importlib.util
import sys
import time
from functools import partial
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from astrbot.core.pipeline.context_utils import call_event_hook
from astrbot.core.star.star import star_map
from astrbot.core.star.star_handler import (
    EventType,
    StarHandlerMetadata,
    star_handlers_registry,
)

from tests.test_participation import decision, requests
from tests.test_runtime import POLICY_PATH, event

pytest_plugins = ["tests.test_participation"]


@pytest.fixture
async def native_memory(participant, monkeypatch):
    """Register native-shaped memory hooks and the actual upstream scope policy.

    Args:
        participant: Real SDK OvenHeart fixture.
        monkeypatch: Isolated registry, policy and context replacements.

    Returns:
        Plugin, upstream-shaped owner, metadata and dispatch entries.
    """
    spec = importlib.util.spec_from_file_location(
        "memory_bridge_fixture.core.memory_scope", POLICY_PATH
    )
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    monkeypatch.setitem(sys.modules, spec.name, policy)

    class NativeMemory:
        __module__ = "memory_bridge_fixture.main"

        async def handle_memory_recall(self, source, req):
            self.calls.append(("recall", source, req))

        async def handle_memory_reflection(self, source, resp):
            self.calls.append(("reflection", source, resp))

    owner = NativeMemory()
    owner.calls = []
    owner.event_handler = SimpleNamespace(
        bind_reply_source=Mock(side_effect=AssertionError("Private protocol used"))
    )
    owner.initializer = SimpleNamespace(is_initialized=True, memory_engine=object())
    owner.config_manager = {}
    metadata = SimpleNamespace(
        name="astrbot_plugin_livingmemory",
        display_name="LivingMemory",
        activated=True,
        star_cls=owner,
    )
    entries = []
    for kind, name in (
        (EventType.OnLLMRequestEvent, "handle_memory_recall"),
        (EventType.OnLLMResponseEvent, "handle_memory_reflection"),
    ):
        entries.append(
            StarHandlerMetadata(
                event_type=kind,
                handler_name=name,
                handler_full_name=f"{owner.__module__}.{name}",
                handler_module_path=owner.__module__,
                handler=partial(getattr(NativeMemory, name), owner),
                event_filters=[],
            )
        )
    monkeypatch.setattr(
        star_handlers_registry,
        "_handlers",
        list(star_handlers_registry._handlers) + entries,
    )
    monkeypatch.setitem(star_map, owner.__module__, metadata)
    participant.context.get_registered_star = lambda name: (
        metadata if name == "astrbot_plugin_livingmemory" else None
    )
    yield participant, owner, metadata, entries
    participant.memory.disconnect()


async def test_next_planner_reply_can_return_to_the_triggering_member(native_memory):
    plugin, owner, _, entries = native_memory
    trigger = event("current-target", sender="alice")
    older = event("older-target", sender="bob")
    plugin.memory.bind_reply_source(trigger, older, lambda: True)
    await entries[0].handler(trigger, object())
    assert owner.calls[-1][1].get_sender_id() == "bob"
    plugin.memory.clear_reply_source(trigger)
    await entries[0].handler(trigger, object())
    assert owner.calls[-1][1] is trigger
    assert trigger not in plugin.memory._reply_sources


@pytest.mark.parametrize("missing", [False, True])
async def test_real_targets_use_owned_adapter_even_with_learning_off(
    native_memory, missing
):
    plugin, owner, _, entries = native_memory
    plugin.settings.update(
        learning_enabled=False, participation_mode="observe", participation_cooldown=0
    )
    older = event("older", text="甲的真实提问", sender="alice")
    await requests(plugin, older)
    plugin.settings["participation_mode"] = "auto"
    plugin.context.llm_generate.return_value = decision(target_sequence=1)
    if missing:
        plugin.participation.sources.clear()
    newest = event("newest", text="乙的另一件事", sender="bob")
    assert len(await requests(plugin, newest)) == 1
    newest.set_extra("llm_checkpoint_id", "current-checkpoint")
    request, response = object(), object()
    await entries[0].handler(newest, request)
    await entries[1].handler(newest, response)
    owner.event_handler.bind_reply_source.assert_not_called()
    assert (
        newest.get_sender_id() == "bob" and newest.get_message_str() == "乙的另一件事"
    )
    if missing:
        assert owner.calls == []
    else:
        assert [row[0] for row in owner.calls] == ["recall", "reflection"]
        for _, source, _ in owner.calls:
            assert source is not older and source.get_sender_id() == "alice"
            assert source.get_message_str() == "甲的真实提问"
            assert source.created_at == newest.created_at
            assert source.get_extra("llm_checkpoint_id") == "current-checkpoint"
        assert owner.calls[0][2] is request and owner.calls[1][2] is response
        assert not older.get_extra("llm_checkpoint_id")
    plugin.participation.notice(event("later", text="新的消息"))
    count = len(owner.calls)
    await entries[1].handler(newest, response)
    assert len(owner.calls) == count


async def test_native_dispatch_keeps_ordinary_and_other_plugins_identity(
    native_memory, monkeypatch
):
    plugin, owner, _, entries = native_memory
    plugin.memory.install()
    trigger, source = event("current", sender="bob"), event("older", sender="alice")
    incoming = object()
    seen = []

    async def other_hook(item, req):
        seen.append((item, req))

    other = StarHandlerMetadata(
        event_type=EventType.OnLLMRequestEvent,
        handler_name="other",
        handler_full_name="other.request",
        handler_module_path="other.main",
        handler=other_hook,
        event_filters=[],
    )
    monkeypatch.setitem(
        star_map,
        "other.main",
        SimpleNamespace(name="other", activated=True, display_name="Other"),
    )
    monkeypatch.setattr(star_handlers_registry, "_handlers", [*entries, other])
    await call_event_hook(trigger, EventType.OnLLMRequestEvent, incoming)
    assert owner.calls[-1][1] is trigger and seen[-1][0] is trigger
    plugin.memory.bind_reply_source(trigger, source, lambda: True)
    await call_event_hook(trigger, EventType.OnLLMRequestEvent, incoming)
    assert owner.calls[-1][1].get_sender_id() == "alice"
    assert seen[-1] == (trigger, incoming)
    assert trigger.get_sender_id() == "bob"


@pytest.mark.parametrize("invalid", ["session", "bot", "expired", "future", "missing"])
async def test_invalid_sources_never_fall_back_to_trigger(native_memory, invalid):
    plugin, owner, _, entries = native_memory
    trigger, source = event("trigger", sender="bob"), event("source", sender="alice")
    if invalid == "session":
        source = event("source", session="other")
    elif invalid == "bot":
        source = event("source", sender="bot")
    elif invalid == "expired":
        source.created_at = time.time() - 601
    elif invalid == "future":
        source.created_at = time.time() + 30
    else:
        source = None
    plugin.memory.bind_reply_source(trigger, source, lambda: True)
    await entries[0].handler(trigger, object())
    await entries[1].handler(trigger, object())
    assert owner.calls == []


async def test_access_revocation_eviction_and_disconnection(native_memory):
    plugin, owner, metadata, entries = native_memory
    trigger, source = event("trigger", sender="bob"), event("source", sender="alice")
    plugin.memory.bind_reply_source(trigger, source, lambda: True)
    owner.config_manager = {
        "access_control": {"whitelist_enabled": True, "whitelist": ["somewhere-else"]}
    }
    await entries[0].handler(trigger, object())
    assert owner.calls == []
    owner.config_manager = {}
    held = []
    for i in range(129):
        item = event(str(i))
        held.append(item)
        plugin.memory.bind_reply_source(item, source, lambda: True)
    await entries[1].handler(trigger, object())
    assert owner.calls == [] and len(plugin.memory._reply_sources) == 128
    original = plugin.memory._hooks[0][1]
    foreign = Mock()
    entries[1].handler = foreign
    plugin.memory.disconnect()
    assert entries[0].handler is original and entries[1].handler is foreign
    assert not plugin.memory._reply_sources


async def test_upstream_reload_reattaches_without_changing_class_methods(native_memory):
    plugin, owner, metadata, entries = native_memory
    original_method = type(owner).handle_memory_recall
    assert plugin.memory.install() and plugin.memory.install()
    original = plugin.memory._hooks[0][1]
    await plugin.release_memory_adapter(metadata)
    assert entries[0].handler is original
    for entry, name in zip(
        entries, ["handle_memory_recall", "handle_memory_reflection"], strict=True
    ):
        entry.handler = partial(getattr(type(owner), name), owner)
    await plugin.refresh_memory_adapter(metadata)
    assert plugin.memory.status()["reply_source_binding"]
    assert type(owner).handle_memory_recall is original_method
    assert len(plugin.memory._hooks) == 2


async def test_recover_abandoned_own_hooks_without_stacking_or_using_stale_owner(
    native_memory,
):
    from astrbot_plugin_ovenheart.memory_bridge import MemoryBridge

    plugin, owner, metadata, entries = native_memory
    previous = MemoryBridge(plugin.context)
    assert previous.install()
    originals = [item[1] for item in previous._hooks]
    abandoned = [entry.handler for entry in entries]
    assert plugin.memory.install()
    assert [item[1] for item in plugin.memory._hooks] == originals
    incoming = event("recovered-hook")
    await entries[0].handler(incoming, object())
    await entries[1].handler(incoming, object())
    assert len(owner.calls) == 2
    previous.disconnect()
    assert plugin.memory.status()["reply_source_binding"]
    plugin.memory.disconnect()
    assert [entry.handler for entry in entries] == originals
    for entry, wrapper in zip(entries, abandoned, strict=True):
        entry.handler = wrapper
    metadata.star_cls = object()
    assert not plugin.memory.install()
    assert [entry.handler for entry in entries] == abandoned
    metadata.star_cls = owner
    assert plugin.memory.install()


async def test_unsupported_hook_shape_cancels_cross_message_admission(native_memory):
    plugin, owner, _, entries = native_memory
    plugin.settings.update(
        learning_enabled=False, participation_mode="observe", participation_cooldown=0
    )
    await requests(plugin, event("older", text="较早问题", sender="alice"))
    entries[1].enabled = False
    plugin.settings["participation_mode"] = "auto"
    plugin.context.llm_generate.return_value = decision(target_sequence=1)
    incoming = event("newest", text="新的话题", sender="bob")
    assert await requests(plugin, incoming) == []
    assert incoming.get_extra("ovenheart_memory_source_blocked")
    assert incoming.call_llm
    assert owner.calls == []


@pytest.mark.parametrize("boundary", ["guard", "plugin_set", "instance", "session"])
async def test_changed_runtime_policy_suppresses_both_memory_hooks(
    native_memory, monkeypatch, boundary
):
    plugin, owner, metadata, entries = native_memory
    trigger, source = event("trigger", sender="bob"), event("source", sender="alice")
    live = [True]
    plugin.memory.bind_reply_source(trigger, source, lambda: live[0])
    if boundary == "guard":
        live[0] = False
    elif boundary == "plugin_set":
        plugin.context.get_config = lambda umo: {
            "plugin_set": ["astrbot_plugin_ovenheart"]
        }
    elif boundary == "instance":
        metadata.star_cls = object()
    else:
        from unittest.mock import AsyncMock

        from astrbot_plugin_ovenheart.memory_bridge import SessionPluginManager

        monkeypatch.setattr(
            SessionPluginManager,
            "is_plugin_enabled_for_session",
            AsyncMock(return_value=False),
        )
    await entries[0].handler(trigger, object())
    await entries[1].handler(trigger, object())
    assert owner.calls == []


async def test_queued_private_build_request_is_not_misattributed_after_upgrade(
    native_memory,
):
    plugin, owner, _, entries = native_memory
    plugin.memory.install()
    incoming = event("legacy-queued", sender="bob")
    incoming.set_extra("livingmemory_reply_source_requested", True)
    await entries[0].handler(incoming, object())
    await entries[1].handler(incoming, object())
    assert owner.calls == []
