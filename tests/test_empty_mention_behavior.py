"""QQ Official empty-mention handling respects the host waiting switch."""

from types import SimpleNamespace

from astrbot.core.message.components import At
from astrbot.core.platform.message_type import MessageType

from astrbot_plugin_ovenheart import main


class Event:
    def __init__(self):
        self.message_obj = SimpleNamespace(
            message=[At(qq="qq_official")],
            raw_message=SimpleNamespace(
                mentions=[SimpleNamespace(is_you=True, id="qq_official")],
                content="",
            ),
        )
        self.message_str = ""
        self.is_at_or_wake_command = True

    def get_message_type(self):
        return MessageType.GROUP_MESSAGE

    def get_platform_name(self):
        return "qq_official"

    def get_self_id(self):
        return "qq_official"

    def get_sender_id(self):
        return "member-openid"

    def get_message_str(self):
        return self.message_str

    def get_messages(self):
        return self.message_obj.message


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


def test_direct_wake_does_not_take_over_disabled_qq_empty_mention(monkeypatch):
    monkeypatch.setattr(main, "_active", lambda: plugin())
    event = Event()
    direct_filter = main.DirectWakeFilter(False)

    assert not direct_filter.filter(
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
