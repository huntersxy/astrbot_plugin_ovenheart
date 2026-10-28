"""Small model-facing views of an already authorized, frozen room snapshot.

The full records stay in the ledger and admission checks. Only the final reply
input is projected here; planning, source IDs and delivery permissions are intact.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def message_view(record):
    """Keep speaker, quote and media meaning without scheduler/storage fields."""
    result = {
        key: copy.deepcopy(record[key])
        for key in (
            "message_id",
            "sender_id",
            "sender",
            "self",
            "text",
            "mentions",
            "media",
            "image_refs",
            "restored",
        )
        if record.get(key) not in (None, "", [], {})
    }
    # A bot's sent record may not have a platform ID yet; its ledger ID remains
    # the stable reference. Never invent an ID or attach another member's media.
    if not result.get("message_id") and record.get("source"):
        result["source"] = record["source"]
    if "replyable" in record and not record["replyable"]:
        result["replyable"] = False
    if isinstance(record.get("time"), (int, float)):
        result["time_iso"] = datetime.fromtimestamp(
            record["time"], timezone.utc
        ).isoformat(timespec="seconds")
    elif record.get("time_iso"):
        result["time_iso"] = record["time_iso"]
    quote = record.get("reply_to")
    if isinstance(quote, dict) and quote:
        result["reply_to"] = message_view(quote)
    return result


def reply_messages(records, settings):
    """Bound the optional reply window by its actual compact wire representation."""
    count, budget = settings["reply_context_messages"], settings["reply_context_budget"]
    selected = []
    if not count or not budget:
        return selected
    for record in reversed(records[-count:]):
        candidate = [record, *selected]
        if len(dumps([message_view(item) for item in candidate])) > budget:
            break
        selected = candidate
    return selected


def _identity(record):
    identifier = record.get("message_id") or record.get("source")
    return (identifier, record.get("sender_id"), bool(record.get("self")))


def payload_view(payload):
    """Project reply-only data and reference identical focus excerpts once."""
    result = copy.deepcopy(payload)
    for key in ("messages", "reply_context"):
        if key in result:
            result[key] = [message_view(record) for record in result[key]]
    if result.get("reply_message"):
        result["reply_message"] = message_view(result["reply_message"])
    focus = result.get("reply_focus")
    if isinstance(focus, dict):
        projected = {}
        known = {_identity(record): record for record in result.get("messages", [])}
        target = result.get("reply_message")
        if target:
            projected["target"] = {"ref": "reply_message"}
        elif focus.get("target"):
            projected["target"] = message_view(focus["target"])
        for key in ("recent_self", "same_sender_before"):
            rows = []
            for item in focus.get(key, []):
                view = message_view(item)
                identity = _identity(view)
                existing = known.get(identity) if identity[0] else None
                # Reference only a matching/superset excerpt. A newly resolved
                # caption or different quote must remain explicit, never borrowed.
                if existing and all(
                    existing.get(k) == v
                    or k == "text"
                    and str(existing.get(k, "")).startswith(v)
                    for k, v in view.items()
                ):
                    view = {"ref": "messages"} | {
                        k: view[k]
                        for k in ("message_id", "source", "sender_id", "self")
                        if k in view
                    }
                rows.append(view)
            if rows:
                projected[key] = rows
        result["reply_focus"] = projected
    return result
