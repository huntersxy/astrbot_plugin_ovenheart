"""Unit coverage for experimental group sender concurrency helpers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from core.chat.group_concurrency import (
    GroupConcurrencyBridge,
    GroupSenderKey,
    SaveContext,
    build_group_sender_key,
    format_sender_scoped_umo,
    locate_current_unit,
)


class DummyEvent:
    def __init__(self, *, umo, sender_id, group_id="g1", private=False):
        self.unified_msg_origin = umo
        self._sender_id = sender_id
        self._group_id = group_id
        self._private = private
        self.message_obj = SimpleNamespace(
            group_id=group_id, sender=SimpleNamespace(user_id=sender_id)
        )
        self._extras = {}

    def is_private_chat(self):
        return self._private

    def get_group_id(self):
        return self._group_id

    def get_sender_id(self):
        return self._sender_id

    def get_extra(self, key):
        return self._extras.get(key)

    def set_extra(self, key, value):
        self._extras[key] = value


def test_sender_lock_key_format():
    key = GroupSenderKey(
        umo="aiocqhttp:GroupMessage:1", sender_id="42"
    )
    assert format_sender_scoped_umo(key).endswith("#ovenheart_sender:42")


def test_build_group_sender_key_skips_private():
    event = DummyEvent(
        umo="aiocqhttp:FriendMessage:1", sender_id="42", private=True
    )
    assert build_group_sender_key(event) is None


def test_build_group_sender_key_for_group():
    event = DummyEvent(
        umo="aiocqhttp:GroupMessage:1", sender_id="42", group_id="1"
    )
    key = build_group_sender_key(event)
    assert key == GroupSenderKey(umo="aiocqhttp:GroupMessage:1", sender_id="42")


def test_sender_lock_key_when_bridge_enabled():
    bridge = GroupConcurrencyBridge(SimpleNamespace())
    bridge._installed = True
    type(bridge)._active = bridge
    event = DummyEvent(
        umo="aiocqhttp:GroupMessage:9", sender_id="u1", group_id="9"
    )
    assert bridge.enabled
    assert bridge.sender_lock_key(event) == (
        "aiocqhttp:GroupMessage:9#ovenheart_sender:u1"
    )
    bridge.disconnect()
    assert not bridge.enabled


def test_locate_current_unit_and_merge_append():
    anchor = SimpleNamespace(role="user", content="hi", _no_save=False)
    messages = [
        SimpleNamespace(role="system", content="sys"),
        {"role": "assistant", "content": "old"},
        anchor,
        SimpleNamespace(role="assistant", content="reply"),
    ]
    located = locate_current_unit(messages, anchor, False)
    assert located == (1, 3)

    bridge = GroupConcurrencyBridge(SimpleNamespace())
    base = [{"role": "user", "content": "a"}]
    latest = base + [{"role": "user", "content": "other"}, {"role": "assistant", "content": "x"}]
    new_history = [
        {"role": "user", "content": "a"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "reply"},
    ]
    save = SaveContext(
        umo="umo",
        conversation_id="cid",
        base_history=base,
        unit_start=1,
        expected_total=3,
    )
    # Without a receipt, merge must refuse concurrent latest.
    chosen, branch = bridge.choose_merged_history(save, latest, new_history)
    assert branch == "fallback-untrusted-latest"
    assert chosen is new_history

    async def with_receipt():
        bridge._remember_receipt(save, latest)
        return bridge.choose_merged_history(save, latest, new_history)

    chosen, branch = asyncio.run(with_receipt())
    assert branch == "merge-append"
    assert chosen == [
        {"role": "user", "content": "a"},
        {"role": "user", "content": "other"},
        {"role": "assistant", "content": "x"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "reply"},
    ]
