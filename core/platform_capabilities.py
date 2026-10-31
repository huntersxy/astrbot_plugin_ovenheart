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


def supports_group_member_lookup(event: Any) -> bool:
    """Whether the event can use OvenHeart's member-list lookup tool."""
    return bool(event.get_group_id()) and not is_qq_official(event)


def can_quote_target(event: Any, target_id: str | None) -> bool:
    """Whether a target can be represented as an outbound quote on this platform.

    QQ Official's message reference requires the current inbound message's
    platform-specific reference index. AstrBot can resolve that index for the
    current event, but OvenHeart's stored older message IDs are not enough.
    """
    target = str(target_id or "").strip()
    if not target or not is_qq_official(event):
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
        "group_member_lookup": False,
        "targeted_quote": "current_message_only",
        "reply_segment_limit": 3,
        "passive_reply_window_seconds": 3600 if private else 300,
        "passive_reply_limit": 4 if private else 5,
        "note": "QQ 官方机器人没有 OneBot 群成员列表接口；已知发送者仍可直接 @。",
    }
