"""Reply-only projection preserves evidence and honors the actual wire budget."""

import copy

from astrbot_plugin_ovenheart.chat.reply_payload import (
    dumps,
    message_view,
    payload_view,
    reply_messages,
)


def record(identifier="m1", **fields):
    return {
        "source": "m:" + identifier,
        "message_id": identifier,
        "sender_id": "alice",
        "sender": "成员甲",
        "self": False,
        "text": "真实发言",
        "time": 1792900000.123,
        "arrival_order": 500,
        "sequence": 100,
        "mentions": [],
        "media": [],
        "image_refs": [],
        "reply_to": None,
        **fields,
    }


def test_projection_keeps_quote_media_and_unreplyable_provenance_without_mutation():
    original = record(
        restored=True,
        replyable=False,
        reply_to=record("quoted", sender_id="bob", text="被引用的原文"),
        media=[{"type": "image", "status": "pending", "text": "已有图片说明"}],
        image_refs=["gimg_" + "a" * 24],
    )
    before = copy.deepcopy(original)
    view = message_view(original)
    assert view["sender_id"] == "alice"
    assert view["reply_to"]["sender_id"] == "bob"
    assert view["reply_to"]["text"] == "被引用的原文"
    assert view["media"] == original["media"]
    assert view["image_refs"] == original["image_refs"]
    assert view["restored"] and view["replyable"] is False
    assert view["self"] is False and view["time_iso"].endswith("+00:00")
    assert not {"sequence", "arrival_order", "source", "time"} & view.keys()
    view["media"][0]["text"] = "edit"
    assert original == before


def test_sent_media_without_platform_id_keeps_its_stable_ledger_reference():
    value = message_view(record("", source="bot:stable", self=True))
    assert value["source"] == "bot:stable" and value["self"] is True


def test_budget_uses_wire_size_and_zero_disables_only_the_optional_window():
    rows = [record(str(i)) for i in range(40)]
    settings = {"reply_context_messages": 3, "reply_context_budget": 1000}
    selected = reply_messages(rows, settings)
    assert selected == rows[-3:]
    assert len(dumps([message_view(row) for row in selected])) <= 1000
    one = len(dumps([message_view(rows[-1])]))
    assert reply_messages(rows, settings | {"reply_context_budget": one}) == rows[-1:]
    for override in [{"reply_context_messages": 0}, {"reply_context_budget": 0}]:
        assert reply_messages(rows, settings | override) == []


def test_focus_references_matching_speech_but_never_borrows_a_different_caption():
    own = record("own", self=True, sender_id="bot", text="自己的原话")
    target = record("target", text="你刚才说什么")
    payload = {
        "messages": [own],
        "reply_message": target,
        "reply_focus": {"target": target, "recent_self": [copy.deepcopy(own)]},
    }
    view = payload_view(payload)
    assert view["reply_focus"]["target"] == {"ref": "reply_message"}
    assert view["reply_focus"]["recent_self"][0] == {
        "ref": "messages",
        "message_id": "own",
        "sender_id": "bot",
        "self": True,
    }
    payload["reply_focus"]["recent_self"][0]["media"] = [
        {"type": "image", "status": "ready", "text": "稍后才完成的描述"}
    ]
    changed = payload_view(payload)["reply_focus"]["recent_self"][0]
    assert "ref" not in changed and changed["media"][0]["text"] == "稍后才完成的描述"
    payload["reply_focus"]["recent_self"][0] = own | {"sender_id": "peer"}
    assert "ref" not in payload_view(payload)["reply_focus"]["recent_self"][0]


def test_projection_substantially_reduces_metadata_without_dropping_message_text():
    rows = [record(str(i), text=f"原文{i}") for i in range(40)]
    view = payload_view({"messages": rows})
    assert [r["text"] for r in view["messages"]] == [r["text"] for r in rows]
    assert len(dumps(view)) < len(dumps({"messages": rows})) * 0.65
