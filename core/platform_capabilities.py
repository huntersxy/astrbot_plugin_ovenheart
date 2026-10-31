"""Platform-specific capabilities used by OvenHeart's native integrations."""

from __future__ import annotations

from typing import Any

QQ_OFFICIAL_PLATFORMS = frozenset({"qq_official", "qq_official_webhook"})


def platform_name(event_or_name: Any) -> str:
    """Return a platform adapter name from an event or a raw name."""
    if isinstance(event_or_name, str):
        return event_or_name
    getter = getattr(event_or_name, "get_platform_name", None)
    if callable(getter):
        try:
            return str(getter() or "")
        except Exception:
            return ""
    return str(getattr(getattr(event_or_name, "platform_meta", None), "name", "") or "")


def is_qq_official(event_or_name: Any) -> bool:
    """Whether an event belongs to the QQ Official Bot adapter."""
    return platform_name(event_or_name) in QQ_OFFICIAL_PLATFORMS


def is_qq_official_at(event: Any) -> bool:
    """Detect a QQ Official @ event when the host parser uses a placeholder ID."""
    if not is_qq_official(event):
        return False
    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    if raw is None:
        return False
    channel_id = getattr(raw, "channel_id", None)
    group_openid = getattr(raw, "group_openid", None)
    mentions = getattr(raw, "mentions", None) or []
    content = str(getattr(raw, "content", "") or "")
    if channel_id and not group_openid:
        return bool(mentions) or "<qqbot-at-user" in content or "<@" in content
    self_id = str(event.get_self_id() or "")
    return any(
        getattr(mention, "is_you", False) is True
        or (self_id and str(getattr(mention, "id", "")) == self_id)
        for mention in mentions
    )


def qq_official_reference_id(event: Any, *, quoted: bool = False) -> str:
    """Read QQ's platform reference index from the raw message scene."""
    if not is_qq_official(event):
        return ""
    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    raw_data = getattr(raw, "raw_data", None)
    if not isinstance(raw_data, dict) and isinstance(raw, dict):
        raw_data = raw
    if not isinstance(raw_data, dict):
        return ""
    scene = raw_data.get("message_scene")
    if not isinstance(scene, dict):
        return ""
    wanted = "ref_msg_idx=" if quoted else "msg_idx="
    for item in scene.get("ext") or []:
        value = str(item or "").strip()
        if value.startswith(wanted):
            reference = value[len(wanted) :].strip()
            if reference:
                return reference
    return ""


def supports_group_member_lookup(event: Any) -> bool:
    """Whether the event has a verified directory for mention lookup.

    QQ Official does not expose the OneBot full member-list action.  The
    plugin can still use the members observed in the current native event and
    its frozen reply context; ``group_mentions`` stores that directory on the
    event before this capability is advertised.
    """
    if not event.get_group_id():
        return False
    if not is_qq_official(event):
        return True
    observed = event.get_extra("ovenheart_qq_observed_members", [])
    return isinstance(observed, list) and bool(observed)


def can_quote_target(event: Any, target_id: str | None) -> bool:
    """Whether a target can be represented as an outbound quote on this platform.

    QQ Official's message reference requires the current inbound message's
    platform-specific reference index. AstrBot can preserve a ``REFIDX_``
    reference from an incoming quote; ordinary stored platform message IDs
    are still not enough to reconstruct an older quote.
    """
    target = str(target_id or "").strip()
    if not target or not is_qq_official(event):
        return True
    if target.startswith("REFIDX_"):
        return True
    current = str(
        getattr(getattr(event, "message_obj", None), "message_id", "") or ""
    ).strip()
    return bool(current and target == current)


def reply_segment_limit(event: Any, configured: int) -> int:
    """Apply QQ Official's conservative per-reply segment limit."""
    limit = max(1, int(configured))
    if is_qq_official(event):
        # QQ Official passive replies are capped per inbound message. Leave
        # room for a follow-up or a separately sent media component.
        return min(limit, 3)
    return limit


def claim_reply_slot(event: Any) -> bool:
    """Reserve one QQ Official reply slot for the current inbound event."""
    if not is_qq_official(event):
        return True
    limit = 4 if event.is_private_chat() else 5
    used = int(event.get_extra("ovenheart_qq_official_reply_count", 0) or 0)
    if used >= limit:
        return False
    event.set_extra("ovenheart_qq_official_reply_count", used + 1)
    return True


def describe(event: Any) -> dict[str, Any]:
    """Describe transport capabilities for the model's temporary reply context."""
    name = platform_name(event)
    if name not in QQ_OFFICIAL_PLATFORMS:
        return {"platform": name}
    private = bool(event.is_private_chat())
    return {
        "platform": name,
        "user_id_kind": "OpenID",
        "proactive_message": True,
        "proactive_target": "group_openid_or_openid",
        "proactive_delivery": "AstrBot native session send",
        "group_member_lookup": supports_group_member_lookup(event),
        "targeted_quote": "current_message_or_refidx",
        "reply_segment_limit": 3,
        "passive_reply_window_seconds": 3600 if private else 300,
        "passive_reply_limit": 4 if private else 5,
        "note": (
            "QQ 官方机器人使用 OpenID。主动消息复用 AstrBot 原生会话发送；"
            "群成员查找只使用当前事件、提及和已冻结上下文中观察到的成员，"
            "不能当作完整群成员目录。"
        ),
    }
