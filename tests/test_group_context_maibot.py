from __future__ import annotations

from types import SimpleNamespace

import pytest
from astrbot.api.platform import MessageType
from astrbot.core.agent.message import Message, TextPart
from astrbot.core.config.default import DEFAULT_CONFIG

from core.chat.conversation import ConversationContext
from core.chat.reply_payload import reply_messages
from core.settings import DEFAULTS
from core.store import Store


def _record(index: int, *, self_message: bool = False) -> dict:
    return {
        "source": f"source-{index}",
        "message_id": f"message-{index}",
        "sender": "bot" if self_message else f"member-{index}",
        "sender_id": "bot" if self_message else f"member-{index}",
        "self": self_message,
        "text": f"消息 {index}",
        "time": float(index),
        "media": [],
        "image_refs": [],
        "reply_to": None,
    }


@pytest.mark.asyncio
async def test_structured_store_keeps_maibot_two_window_then_trims(tmp_path):
    store = Store(tmp_path / "context.sqlite3")
    await store.call("initialize")
    try:
        for index in range(9):
            state = await store.call(
                "chat_context",
                "scope",
                "cid",
                _record(index, self_message=index == 3),
                None,
                None,
                4,
                False,
                True,
            )
        assert [row["text"] for row in state["messages"]] == [
            "消息 5",
            "消息 6",
            "消息 7",
            "消息 8",
        ]
        assert len(state["removed"]) == 5
    finally:
        await store.close()


def test_structured_reply_window_does_not_add_a_second_short_limit():
    records = [_record(index) for index in range(80)]
    settings = DEFAULTS | {"context_mode": "structured", "group_context_messages": 40}
    assert len(reply_messages(records, settings)) == 80


def test_takeover_requires_both_native_group_features_to_be_off():
    event = SimpleNamespace(
        unified_msg_origin="test:group",
        get_message_type=lambda: MessageType.GROUP_MESSAGE,
    )
    plugin = SimpleNamespace(
        settings=DEFAULTS | {"context_mode": "structured"},
        context=SimpleNamespace(
            get_config=lambda _: {
                "provider_ltm_settings": {
                    "group_icl_enable": False,
                    "active_reply": {"enable": False},
                }
            }
        ),
    )
    conversation = ConversationContext(plugin)
    assert conversation.active(event)

    plugin.context.get_config = lambda _: {
        "provider_ltm_settings": {
            "group_icl_enable": True,
            "active_reply": {"enable": False},
        }
    }
    assert not conversation.active(event)

    plugin.context.get_config = lambda _: {
        "provider_ltm_settings": {
            "group_icl_enable": False,
            "active_reply": {"enable": True},
        }
    }
    assert not conversation.active(event)


@pytest.mark.asyncio
async def test_structured_runner_view_preserves_native_history_for_saving():
    host = DEFAULT_CONFIG | {
        "provider_ltm_settings": {
            "group_icl_enable": False,
            "active_reply": {"enable": False},
        }
    }
    plugin = SimpleNamespace(
        settings=DEFAULTS | {"context_mode": "structured", "planner_visual_enabled": False},
        context=SimpleNamespace(get_config=lambda _: host),
    )
    conversation = ConversationContext(plugin)
    event = SimpleNamespace(
        unified_msg_origin="test:group",
        extras={},
        get_message_type=lambda: MessageType.GROUP_MESSAGE,
        get_extra=lambda key, default=None: event.extras.get(key, default),
        set_extra=lambda key, value: event.extras.__setitem__(key, value),
    )
    request = SimpleNamespace(prompt="当前问题", conversation=None)
    native = [
        Message(role="system", content="人格"),
        Message(role="user", content=[TextPart(text="原生旧消息")]),
        Message(role="user", content=[TextPart(text="当前问题")]),
    ]
    conversation.bind_request(
        event,
        request,
        [_record(index) for index in range(3)],
        _record(2),
    )
    runner = SimpleNamespace(run_context=SimpleNamespace())
    event.set_extra("ovenheart_usage_runner", runner)
    run_context = SimpleNamespace(messages=native)
    runner.run_context = run_context

    await conversation.begin(event, run_context)
    assert len(run_context.messages) == 5
    assert run_context.messages[0].role == "system"
    assert all(message._no_save for message in run_context.messages[1:])
    assert run_context.messages[-1].content[-1].text == "当前问题"

    conversation.finish_run(event, run_context)
    assert [message.content for message in run_context.messages[:3]] == [
        "人格",
        [TextPart(text="原生旧消息")],
        [TextPart(text="当前问题")],
    ]
