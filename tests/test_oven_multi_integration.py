"""Exercise both checkouts through native response hooks and service boundaries."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

PARENT = Path(__file__).resolve().parents[2]
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

multi_module = pytest.importorskip("astrbot_plugin_oven_multi.main")

from astrbot.api.event import AstrMessageEvent  # noqa: E402
from astrbot.api.platform import MessageType  # noqa: E402
from astrbot.core.config.default import DEFAULT_CONFIG  # noqa: E402
from astrbot.core.message.components import At, Plain, Reply  # noqa: E402
from astrbot.core.message.message_event_result import ResultContentType  # noqa: E402
from astrbot.core.pipeline import context_utils  # noqa: E402
from astrbot.core.pipeline.respond.stage import RespondStage  # noqa: E402
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember  # noqa: E402
from astrbot.core.platform.platform_metadata import PlatformMetadata  # noqa: E402
from astrbot.core.star import star_handler  # noqa: E402

from astrbot_plugin_ovenheart.core.chat.reply_rhythm import ReplyRhythm  # noqa: E402
from astrbot_plugin_ovenheart.core.integrations.outgoing_media import (  # noqa: E402
    OutgoingMediaBridge,
)
from astrbot_plugin_ovenheart.core.settings import DEFAULTS  # noqa: E402
from astrbot_plugin_ovenheart.main import PersonaPlugin  # noqa: E402

OvenMultiPlugin = multi_module.OvenMultiPlugin
OvenHeartLink = multi_module.OvenHeartLink


def event_for(text="最新成员在问另一个问题"):
    message = AstrBotMessage()
    message.type = MessageType.GROUP_MESSAGE
    message.group_id = "room"
    message.self_id = "bot"
    message.sender = MessageMember("member", "成员")
    message.message_id = "latest"
    message.message = [Plain(text)]
    return AstrMessageEvent(
        text, message, PlatformMetadata("aiocqhttp", "test", "test"), "room"
    )


def heart_for(host):
    heart = PersonaPlugin.__new__(PersonaPlugin)
    heart.ready = True
    heart.closing = False
    heart.settings = DEFAULTS | {"learning_enabled": False, "reply_typing_speed": 0}
    heart.context = SimpleNamespace(get_config=lambda _: host)
    heart.direct_pending = {}
    heart.direct_priority = SimpleNamespace(entries={})
    heart.permitted = AsyncMock(return_value=True)
    heart.resolve_scope = AsyncMock(return_value=("scope", "persona", "room"))
    heart.store = SimpleNamespace(call=AsyncMock())
    heart.record_turn_trace = AsyncMock()
    heart.debug = lambda *args, **kwargs: None
    heart.conversation = SimpleNamespace(active=lambda _: True, delivered=AsyncMock())
    heart.participation = SimpleNamespace(
        room_key=lambda _: "room",
        current=lambda _: True,
        rooms={},
        windows={},
        rhythm=SimpleNamespace(observe=lambda *args, **kwargs: None),
    )
    heart.check_participation = AsyncMock(return_value=True)
    heart.outgoing_media = OutgoingMediaBridge(heart)
    heart.reply_rhythm = ReplyRhythm(heart)
    return heart


def native_hooks(monkeypatch, heart, multi, heart_loaded_first):
    registry = star_handler.StarHandlerRegistry()
    bindings = [
        (PersonaPlugin.record_delivery, heart.record_delivery, "heart"),
        (OvenMultiPlugin.send_pending_segments, multi.send_pending_segments, "multi"),
    ]
    if not heart_loaded_first:
        bindings.reverse()
    metadata = {}
    for method, bound, name in bindings:
        original = next(
            handler
            for handler in star_handler.star_handlers_registry
            if handler.handler is method
        )
        handler = copy.copy(original)
        handler.handler = bound
        registry.append(handler)
        metadata[handler.handler_module_path] = SimpleNamespace(
            name=name, display_name=name, activated=True, reserved=False
        )
    monkeypatch.setattr(context_utils, "star_handlers_registry", registry)
    monkeypatch.setattr(context_utils, "star_map", metadata)
    monkeypatch.setattr(star_handler, "star_map", metadata)


@pytest.mark.parametrize("heart_loaded_first", [False, True])
@pytest.mark.parametrize(
    "fail_at,raises,stop_after_first",
    [(None, False, False), (0, False, False), (1, False, False), (1, True, False), (None, False, True)],
)
async def test_native_segment_delivery_records_only_successful_text(
    monkeypatch, heart_loaded_first, fail_at, raises, stop_after_first
):
    host = copy.deepcopy(DEFAULT_CONFIG)
    host["platform_settings"]["segmented_reply"]["enable"] = False
    event = event_for()
    event.set_extra("ovenheart_reply_scope", "scope")
    event.set_extra(
        "ovenheart_decision",
        {
            "id": 1,
            "scope": "scope",
            "planner_session": True,
            "room_source": "latest",
            "target_sequence": 7,
            "target": {"source": "older", "text": "被选中的问题"},
        },
    )
    result = event.plain_result("第一段。\n第二段。\n第三段。")
    result.result_content_type = ResultContentType.LLM_RESULT
    result.chain.insert(0, Reply(id="older"))
    result.chain.insert(1, At(qq="target"))
    event.set_result(result)
    attempted, sent = [], []

    async def platform_send(message):
        attempted.append(message)
        if len(attempted) - 1 == fail_at:
            if raises:
                raise RuntimeError("platform unavailable")
            return False
        sent.append(message)
        if stop_after_first:
            event.stop_event()

    event.send = platform_send
    heart = heart_for(host)
    heart.outgoing_media.bind_event(event)
    heart.reply_rhythm.bind(event)
    multi = OvenMultiPlugin.__new__(OvenMultiPlugin)
    multi.config = {
        "segment_reply": {
            "enabled": True,
            "force_split_symbols": ["\n"],
            "typing_speed": 0,
            "merge_short_tail": False,
            "min_length": 10,
            "max_length": 50,
        }
    }
    multi.context = SimpleNamespace(
        get_config=lambda _: host,
        get_registered_star=lambda _: SimpleNamespace(activated=True, star_cls=heart),
    )
    multi.ovenheart = OvenHeartLink(multi)
    multi.segment = multi_module.SegmentReply()
    await multi.segment_reply_decorate(event)
    native_hooks(monkeypatch, heart, multi, heart_loaded_first)
    stage = RespondStage()
    await stage.initialize(SimpleNamespace(astrbot_config=host))
    await stage.process(event)

    expected = ["第一段。", "第二段。", "第三段。"][:1 if stop_after_first else fail_at]
    assert [message.get_plain_text() for message in sent] == expected
    assert event.get_extra("ovenheart_actual_text_segments", []) == expected
    receipt = event.get_extra("ovenheart_planner_receipt")
    assert receipt["reply_text"] == "\n".join(expected)
    assert receipt["send_hook"] is bool(expected)
    if fail_at is not None or stop_after_first:
        assert len(attempted) == (1 if stop_after_first else fail_at + 1)
        assert event.get_extra("ovenheart_text_delivery_incomplete") is True
    if expected:
        assert [type(part) for part in sent[0].chain[:2]] == [Reply, At]
        assert all(isinstance(part, Plain) for msg in sent[1:] for part in msg.chain)
        assert heart.conversation.delivered.await_args.args[3] == "\n".join(expected)
        assert receipt["partial_text"] is (fail_at is not None or stop_after_first)
    else:
        heart.conversation.delivered.assert_not_awaited()


async def test_direct_context_with_participation_off_keeps_all_sent_segments():
    event = event_for()
    event.set_result(event.plain_result("第一段"))
    event.set_extra("ovenheart_actual_text_segments", ["第一段", "后续段"])
    heart = heart_for({})
    heart.settings["participation_mode"] = "off"
    await heart.record_delivery(event)
    assert heart.conversation.delivered.await_args.args[3] == "第一段\n后续段"


@pytest.mark.parametrize("native_segmented", [False, True])
async def test_new_reply_clears_previous_multi_segment_state(native_segmented):
    event = event_for()
    event.set_result(event.plain_result("新的回复"))
    event.set_extra("oven_multi_segmented", True)
    event.set_extra("oven_multi_segment_incomplete", True)
    event.set_extra("oven_segment_pending", ["旧首段", "旧后文"])
    event.set_extra("oven_segment_suffix", [Plain("旧后缀")])
    multi = OvenMultiPlugin.__new__(OvenMultiPlugin)
    multi.config = {"segment_reply": {"enabled": native_segmented}}
    multi.context = SimpleNamespace(
        get_config=lambda _: {"platform_settings": {"segmented_reply": {"enable": True}}}
    )
    await multi.segment_reply_decorate(event)
    assert event.get_extra("oven_multi_segmented") is None
    assert event.get_extra("oven_multi_segment_incomplete") is None
    assert event.get_extra("oven_segment_pending") is None
    assert event.get_extra("oven_segment_suffix") is None
    assert event.get_result().get_plain_text() == "新的回复"


@pytest.mark.parametrize("target_text", ["被选中的文件问题", ""])
async def test_file_retrieval_uses_frozen_target_even_when_empty(target_text):
    event = event_for()
    event.set_extra("ovenheart_decision", {"target": {"text": target_text}})
    multi = OvenMultiPlugin.__new__(OvenMultiPlugin)
    multi.config = {"file_reader": {"rag": {"enabled": True}}}
    multi.ovenheart = OvenHeartLink(multi)
    multi.file_reader = SimpleNamespace(build_context=AsyncMock(return_value="文件资料"))
    request = SimpleNamespace(prompt="最新成员的问题", extra_user_content_parts=[])
    await multi.on_llm_request_files(event, request)
    if target_text:
        multi.file_reader.build_context.assert_awaited_once_with(
            event.unified_msg_origin, target_text
        )
        assert "文件资料" in request.extra_user_content_parts[0].text
    else:
        multi.file_reader.build_context.assert_not_awaited()
        assert request.extra_user_content_parts == []
    assert request.prompt == "最新成员的问题"


@pytest.mark.parametrize("target_text", ["绝绝子", ""])
async def test_meme_prefetch_uses_same_target_without_borrowing_trigger(target_text):
    event = event_for("另一个梗")
    event.set_extra("ovenheart_direct_context", {"reply_message": {"text": target_text}})
    wiki = multi_module.MemeWiki.__new__(multi_module.MemeWiki)
    wiki.plugin = SimpleNamespace(config={"memewiki": {"inject_guidance": False}})
    wiki._state = {}
    wiki._lookup_fast = AsyncMock(return_value="词库中的用法")
    request = SimpleNamespace(extra_user_content_parts=[])
    await wiki.prefetch_meme(event, request)
    if target_text:
        wiki._lookup_fast.assert_awaited_once_with(target_text)
        assert target_text in request.extra_user_content_parts[0].text
    else:
        wiki._lookup_fast.assert_not_awaited()
        assert request.extra_user_content_parts == []


@pytest.mark.parametrize("disabled_by", ["plugin_set", "session", "no_key", "during_call", None])
async def test_linked_jev_respects_multi_session_controls(monkeypatch, disabled_by):
    event = event_for()
    core = SimpleNamespace(
        ready=True,
        closing=False,
        settings={"enabled": True, "apply_enabled": True},
        oven_multi_protocol=1,
        owns_group_reply=AsyncMock(return_value=True),
        resolve_scope=AsyncMock(return_value=("scope", "persona", "room")),
    )
    enabled = disabled_by != "session"

    async def is_enabled(*args):
        return enabled

    async def judge(*args, **kwargs):
        nonlocal enabled
        if disabled_by == "during_call":
            enabled = False
        return {"ok": True, "intent": "询问", "emotion": "平静"}

    monkeypatch.setattr(
        "astrbot.core.star.session_plugin_manager.SessionPluginManager.is_plugin_enabled_for_session",
        is_enabled,
    )
    plugin = SimpleNamespace(
        closing=False,
        config={"jev": {"enabled": True, "api_key": "" if disabled_by == "no_key" else "test"}},
        _blocked=lambda _: False,
        context=SimpleNamespace(
            get_config=lambda _: {"plugin_set": ["other"] if disabled_by == "plugin_set" else ["*"]},
            get_registered_star=lambda _: SimpleNamespace(activated=True, star_cls=core),
        ),
        jev_scenes=SimpleNamespace(jev=SimpleNamespace(judge=AsyncMock(side_effect=judge))),
    )
    link = OvenHeartLink(plugin)
    response = await link.analyze(
        event,
        {
            "version": 1,
            "scope": "scope",
            "target": {"source": "older", "sender_id": "member", "text": "实际目标"},
            "messages": [],
            "bot_sender_id": "bot",
        },
    )
    if disabled_by in {"plugin_set", "session", "no_key"}:
        assert response["status"] == "disabled"
        plugin.jev_scenes.jev.judge.assert_not_awaited()
    elif disabled_by == "during_call":
        assert response["status"] == "stale"
    else:
        assert response["status"] == "ready"
        assert response["fields"] == {"intent": "询问", "emotion": "平静"}
