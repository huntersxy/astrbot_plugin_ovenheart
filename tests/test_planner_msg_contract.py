"""Planner msg_id and expression_ids contracts."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from astrbot_plugin_ovenheart.core.chat.participation import planner_message  # noqa: E402
from astrbot_plugin_ovenheart.core.chat.planner import (  # noqa: E402
    ACTION_SCHEMAS,
    PlannerSession,
    PlannerTool,
)
from astrbot_plugin_ovenheart.core.settings import DEFAULTS  # noqa: E402


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


@pytest.mark.parametrize(
    "invalid",
    [
        {"expression_style": "简短表达"},
        {"attachment": "959"},
        {"reply_style": "brief"},
    ],
)
async def test_invalid_reply_returns_correction_then_accepts_repaired_action(invalid):
    session = PlannerSession.__new__(PlannerSession)
    session.id = "test-planner"
    session.scope = "test-room"
    session.plugin = SimpleNamespace(debug=lambda *_, **__: None)
    session.settings = DEFAULTS
    session.messages = [{"sequence": 7, "source": "source-7", "self": False}]
    session.expressions = {}
    session.emojis = {959: {"caption": "真实候选"}}
    session.direct = False
    session.replied = set()
    session.running_tools = 0
    session.tool_changed = asyncio.Event()
    session.actions = asyncio.Queue()
    session.action = None
    session.pending = None
    session.check_current = AsyncMock()
    tool = PlannerTool(
        name="reply", description="Reply", parameters=ACTION_SCHEMAS["reply"]
    )
    tool.session = session
    arguments = {
        "msg_id": "7",
        "reply_reference": "回应当前约定，简短确认。",
        "expression_ids": [],
        "reply_style": "简短表达",
        "expression_intent": {"reply_act": "简短确认"},
    }

    error = json.loads(await tool.call(None, **(arguments | invalid)))

    assert error["success"] is False
    assert "schema" in error["retry"]
    assert "reply_style" in error["allowed_parameters"]
    assert "attach_emoji" in error["allowed_parameters"]
    assert "expression_style" not in error["allowed_parameters"]
    assert "attachment" not in error["allowed_parameters"]
    assert "Failed validating" not in json.dumps(error)
    assert len(json.dumps(error, ensure_ascii=False)) < 1000
    assert session.actions.empty()
    assert session.pending is None
    assert session.running_tools == 0
    assert not session.tool_changed.is_set()
    session.check_current.assert_not_awaited()

    task = asyncio.create_task(tool.call(None, **(arguments | {"attach_emoji": 959})))
    try:
        plan = await asyncio.wait_for(session.actions.get(), 1)
        assert plan["reply_style"] == "简短表达"
        assert plan["expression_intent"] == {"reply_act": "简短确认"}
        assert plan["attach_emoji"] == 959
        assert plan["target_sequence"] == 7
        assert session.running_tools == 1
        session.pending.set_result({"send_hook": True})
        assert json.loads(await task) == {"send_hook": True}
        assert session.running_tools == 0
        assert session.actions.empty()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
