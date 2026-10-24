"""Exercise image races against the actual native queue and request format."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.api.provider import ProviderRequest
from astrbot.builtin_stars.astrbot.group_chat_context import GroupChatContext
from astrbot.core.message.components import Image, Plain

from tests.test_runtime import event

pytest_plugins = ["tests.test_participation"]


def message(key, text="", image=False, session="123"):
    incoming = event(key, text=text, sender=key, sender_name=key, session=session)
    incoming.message_obj.message = (
        [Image.fromURL("https://example.invalid/image.png")] if image else [Plain(text)]
    )
    return incoming


@pytest.fixture
async def ordered_group(participant, monkeypatch):
    participant.context.get_config = lambda umo: {
        "plugin_set": ["*"],
        "provider_settings": {"image_caption_prompt": "describe"},
        "provider_ltm_settings": {
            "group_icl_enable": True,
            "group_message_max_cnt": 30,
            "image_caption": True,
            "image_caption_provider_id": "caption",
            "active_reply": {
                "enable": False,
                "method": "possibility_reply",
                "possibility_reply": 0,
            },
        },
    }
    owner = GroupChatContext(None, participant.context)
    metadata = SimpleNamespace(
        activated=True, star_cls=SimpleNamespace(group_chat_context=owner)
    )
    participant.context.get_all_stars = lambda: [metadata]
    started, release = asyncio.Event(), asyncio.Event()

    async def caption(*args):
        started.set()
        await release.wait()
        return "CAPTION_COMPLETE"

    monkeypatch.setattr(owner, "get_image_caption", AsyncMock(side_effect=caption))
    assert participant.group_context.install()
    yield participant, owner, metadata, started, release
    release.set()
    participant.group_context.disconnect()


async def test_pending_caption_keeps_place_and_does_not_leak_next_turn(ordered_group):
    _, group, _, started, release = ordered_group
    pending = asyncio.create_task(
        group.handle_message(message("earlier_image", image=True))
    )
    try:
        await asyncio.wait_for(started.wait(), 1)
        peer, trigger, later = (
            message("peer", "TEXT_AFTER_IMAGE"),
            message("trigger", "CURRENT_TRIGGER"),
            message("later", "AFTER_TRIGGER"),
        )
        for incoming in (peer, trigger, later):
            await group.handle_message(incoming)
        request = ProviderRequest(prompt=trigger.message_str)
        await group.on_req_llm(trigger, request)
        block = request.extra_user_content_parts[0].text
        assert len(request.extra_user_content_parts) == 1
        assert block.index("[earlier_image/") < block.index("[peer/")
        assert "[Image]" in block
        assert "CURRENT_TRIGGER" not in block and "AFTER_TRIGGER" not in block
        release.set()
        await pending
        queued = "\n".join(group.raw_records[trigger.unified_msg_origin])
        assert "AFTER_TRIGGER" in queued and "earlier_image" not in queued
        next_turn = message("next", "NEXT_TRIGGER")
        await group.handle_message(next_turn)
        following = ProviderRequest(prompt=next_turn.message_str)
        await group.on_req_llm(next_turn, following)
        assert "AFTER_TRIGGER" in following.extra_user_content_parts[0].text
        assert "CAPTION_COMPLETE" not in following.extra_user_content_parts[0].text
    finally:
        release.set()
        await pending


async def test_ready_caption_updates_original_slot_and_keeps_native_format(
    ordered_group,
):
    _, group, _, _, release = ordered_group
    release.set()
    await group.handle_message(message("image", image=True))
    trigger = message("trigger", "TRIGGER")
    await group.handle_message(trigger)
    request = ProviderRequest()
    await group.on_req_llm(trigger, request)
    block = request.extra_user_content_parts[0].text
    assert block.startswith("<system_reminder>You are in a group chat.")
    assert block.count("[image/") == 1 and "[Image: CAPTION_COMPLETE]" in block


@pytest.mark.parametrize("discard", ["trimmed", "reset", "cancelled", "unloaded"])
async def test_discarded_or_cancelled_image_does_not_resurrect(ordered_group, discard):
    plugin, group, _, started, release = ordered_group
    incoming = message("slow", image=True)
    pending = asyncio.create_task(group.handle_message(incoming))
    try:
        await asyncio.wait_for(started.wait(), 1)
        umo = incoming.unified_msg_origin
        if discard == "trimmed":
            for i in range(30):
                await group.handle_message(message(f"later-{i}", f"text-{i}"))
        elif discard == "reset":
            await group.remove_session(incoming)
        elif discard == "cancelled":
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            plugin.group_context.disconnect()
        before = list(group.raw_records.get(umo, ()))
        release.set()
        if discard != "cancelled":
            await pending
        assert list(group.raw_records.get(umo, ())) == before
        assert "CAPTION_COMPLETE" not in "\n".join(before)
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


async def test_evicted_trigger_cannot_consume_another_messages_slot(ordered_group):
    _, group, _, _, _ = ordered_group
    trigger = message("old-trigger", "OLD")
    await group.handle_message(trigger)
    for i in range(30):
        await group.handle_message(message(f"new-{i}", f"NEW-{i}"))
    before = list(group.raw_records[trigger.unified_msg_origin])
    request = ProviderRequest()
    await group.on_req_llm(trigger, request)
    assert not request.extra_user_content_parts
    assert list(group.raw_records[trigger.unified_msg_origin]) == before


async def test_groups_and_native_restoration_keep_existing_queues(ordered_group):
    plugin, group, metadata, _, _ = ordered_group
    first, second = (
        message("one", "FIRST", session="111"),
        message("two", "SECOND", session="222"),
    )
    await group.handle_message(first)
    await group.handle_message(second)
    records, owned = group.raw_records, group.handle_message
    assert plugin.group_context.install() and group.handle_message is owned
    plugin.group_context.disconnect()
    assert group.handle_message.__func__ is GroupChatContext.handle_message
    assert group.raw_records is records
    assert "FIRST" in "".join(records[first.unified_msg_origin])
    assert "SECOND" in "".join(records[second.unified_msg_origin])
    replacement = GroupChatContext(None, plugin.context)
    metadata.star_cls.group_chat_context = replacement
    assert plugin.group_context.install()
    assert group.handle_message.__func__ is GroupChatContext.handle_message
    assert replacement.handle_message is not owned


async def test_disabled_session_uses_original_native_path(ordered_group):
    plugin, group, _, started, release = ordered_group
    plugin.settings["enabled"] = False
    incoming = message("native", image=True)
    pending = asyncio.create_task(group.handle_message(incoming))
    try:
        await asyncio.wait_for(started.wait(), 1)
        assert not group.raw_records.get(incoming.unified_msg_origin)
        release.set()
        await pending
        assert "CAPTION_COMPLETE" in "".join(
            group.raw_records[incoming.unified_msg_origin]
        )
    finally:
        release.set()
        await pending


async def test_caption_failure_retains_the_image_marker(ordered_group):
    _, group, _, _, _ = ordered_group
    group.get_image_caption.side_effect = RuntimeError("Synthetic caption failure")
    incoming = message("failed-image", image=True)
    await group.handle_message(incoming)
    assert "[Image]" in "".join(group.raw_records[incoming.unified_msg_origin])


async def test_foreign_wrapper_is_not_overwritten_or_nested(ordered_group):
    plugin, group, _, _, _ = ordered_group
    previous = group.handle_message

    async def foreign(event):
        return await previous(event)

    group.handle_message = foreign
    assert not plugin.group_context.install()
    assert group.handle_message is foreign
    assert group.on_req_llm.__func__ is GroupChatContext.on_req_llm
