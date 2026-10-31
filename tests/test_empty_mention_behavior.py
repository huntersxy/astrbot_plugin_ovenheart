"""QQ Official empty-mention handling respects the host waiting switch."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.core.message.components import At
from astrbot.core.platform.message_type import MessageType
from astrbot.core.provider.entities import ProviderRequest

from astrbot_plugin_ovenheart import main
from core import prompts


class Event:
    def __init__(self, message_str="", *, self_id="qq_official"):
        self.message_obj = SimpleNamespace(
            message=[At(qq=self_id)],
            raw_message=SimpleNamespace(
                mentions=[SimpleNamespace(is_you=True, id=self_id)],
                content="",
            ),
            message_id="trigger",
            sender=SimpleNamespace(user_id="member-openid", nickname="member"),
        )
        self.message_str = message_str
        self.is_at_or_wake_command = True
        self.unified_msg_origin = "qq_official:GroupMessage:room"
        self.extra = {}
        self.call_llm = False

    def get_message_type(self):
        return MessageType.GROUP_MESSAGE

    def get_platform_name(self):
        return "qq_official"

    def get_platform_id(self):
        return "qq_official"

    def get_self_id(self):
        return "qq_official"

    def get_sender_id(self):
        return "member-openid"

    def get_message_str(self):
        return self.message_str

    def get_messages(self):
        return self.message_obj.message

    def get_extra(self, key, default=None):
        return self.extra.get(key, default)

    def set_extra(self, key, value):
        self.extra[key] = value

    def request_llm(self, **kwargs):
        return ProviderRequest(**kwargs)


def plugin():
    return SimpleNamespace(
        ready=True,
        closing=False,
        settings={"enabled": True, "apply_enabled": True},
    )


def test_qq_empty_mention_filter_respects_host_waiting_switch(monkeypatch):
    monkeypatch.setattr(main, "_active", lambda: plugin())
    event = Event()
    handler_filter = main.QQOfficialEmptyMentionFilter()

    assert handler_filter.filter(
        event, {"platform_settings": {"empty_mention_waiting": True}}
    )
    assert not handler_filter.filter(
        event, {"platform_settings": {"empty_mention_waiting": False}}
    )


def test_direct_wake_replies_immediately_when_qq_empty_mention_waiting_is_disabled(
    monkeypatch,
):
    monkeypatch.setattr(main, "_active", lambda: plugin())
    event = Event()
    direct_filter = main.DirectWakeFilter(False)

    assert direct_filter.filter(
        event, {"platform_settings": {"empty_mention_waiting": False}}
    )
    assert direct_filter.filter(
        event, {"platform_settings": {"empty_mention_waiting": True}}
    )


def test_native_empty_mention_handler_is_removed_only_by_identity():
    native = SimpleNamespace(
        handler_name="handle_empty_mention",
        handler_module_path="astrbot.builtin_stars.astrbot.main",
    )
    other = SimpleNamespace(
        handler_name="participate",
        handler_module_path="astrbot_plugin_ovenheart.main",
    )

    assert main.without_native_empty_mention_handler([native, other]) == [other]
    assert main.QQ_OFFICIAL_EMPTY_MENTION_WAIT_SECONDS == 2


@pytest.mark.parametrize(
    ("target_text", "target_source"),
    [
        ("", "trigger"),
        ("", "older-empty-at"),
    ],
)
async def test_participation_request_fills_blank_prompt_for_empty_at(
    target_text, target_source
):
    """Bare @ replies must not yield a blank host prompt.

    AstrBot build_main_agent rejects empty prompts before OnLLMRequest hooks
    inject OvenHeart context, which previously finished the agent with no output.
    """
    event = Event()
    conversation = SimpleNamespace(cid="cid")
    owner = main.PersonaPlugin.__new__(main.PersonaPlugin)
    owner.context = SimpleNamespace(
        conversation_manager=SimpleNamespace(
            get_curr_conversation_id=AsyncMock(return_value="cid"),
            get_conversation=AsyncMock(return_value=conversation),
            new_conversation=AsyncMock(return_value="cid"),
        )
    )
    plan = {
        "scope": "scope",
        "id": "decision",
        "room_source": "trigger",
        "target": {
            "source": target_source,
            "text": target_text,
            "message_id": "target",
            "sender": "member",
            "sender_id": "member-openid",
        },
    }
    requests = [
        request
        async for request in owner.participation_request(event, plan)
    ]
    assert len(requests) == 1
    assert requests[0].prompt == prompts.EMPTY_MENTION_CONTINUATION
    assert requests[0].conversation is conversation


async def test_participation_request_keeps_real_trigger_text():
    event = Event(message_str="帮我看下这个")
    conversation = SimpleNamespace(cid="cid")
    owner = main.PersonaPlugin.__new__(main.PersonaPlugin)
    owner.context = SimpleNamespace(
        conversation_manager=SimpleNamespace(
            get_curr_conversation_id=AsyncMock(return_value="cid"),
            get_conversation=AsyncMock(return_value=conversation),
            new_conversation=AsyncMock(return_value="cid"),
        )
    )
    plan = {
        "scope": "scope",
        "id": "decision",
        "room_source": "trigger",
        "target": {
            "source": "trigger",
            "text": "帮我看下这个",
            "message_id": "target",
            "sender": "member",
            "sender_id": "member-openid",
        },
    }
    requests = [
        request
        async for request in owner.participation_request(event, plan)
    ]
    assert requests[0].prompt == "帮我看下这个"


async def test_direct_participation_request_fills_blank_initial_prompt():
    event = Event()
    conversation = SimpleNamespace(cid="cid")
    owner = main.PersonaPlugin.__new__(main.PersonaPlugin)
    owner.context = SimpleNamespace(
        conversation_manager=SimpleNamespace(
            get_curr_conversation_id=AsyncMock(return_value="cid"),
            get_conversation=AsyncMock(return_value=conversation),
            new_conversation=AsyncMock(return_value="cid"),
        )
    )
    blank = ProviderRequest(prompt="", conversation=conversation)
    event.set_extra("ovenheart_planner_initial_request", blank)
    plan = {
        "scope": "scope",
        "id": "decision",
        "room_source": "trigger",
        "direct": True,
        "target": {
            "source": "trigger",
            "text": "",
            "message_id": "target",
            "sender": "member",
            "sender_id": "member-openid",
        },
    }
    requests = [
        request
        async for request in owner.participation_request(event, plan)
    ]
    assert requests[0] is blank
    assert blank.prompt == prompts.EMPTY_MENTION_CONTINUATION
