"""Planner msg_id and expression_ids contracts."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from astrbot_plugin_ovenheart.core.chat.participation import planner_message  # noqa: E402
from astrbot_plugin_ovenheart.core.chat.planner import PlannerSession  # noqa: E402


def test_planner_message_exposes_msg_id_hides_transport_ids():
    row = planner_message(
        {
            "sequence": 7,
            "message_id": "platform-abc",
            "source": "qq:GroupMessage:1:platform-abc",
            "text": "hi",
            "self": False,
            "image_refs": [],
            "arrival_order": 3,
            "answered_source": None,
            "reply_to": {},
            "media": [],
            "mentions": [],
        }
    )
    assert row["msg_id"] == "7"
    assert row["sequence"] == 7
    assert "message_id" not in row
    assert "source" not in row
    assert "image_refs" not in row


def test_resolve_msg_id_accepts_sequence_and_unique_platform_id():
    session = PlannerSession.__new__(PlannerSession)
    session.messages = [
        {
            "sequence": 3,
            "message_id": "p-3",
            "source": "src-3",
            "self": False,
        },
        {
            "sequence": 4,
            "message_id": "p-4",
            "source": "src-4",
            "self": False,
        },
        {"sequence": 5, "message_id": "bot", "source": "bot-src", "self": True},
    ]
    assert session.resolve_msg_id("3") == "3"
    assert session.resolve_msg_id("p-4") == "4"
    assert session.resolve_msg_id("src-3") == "3"
    assert session.resolve_msg_id("missing") is None
    assert session.resolve_msg_id("bot") is None
