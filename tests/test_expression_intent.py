"""Check bounded planner style intent across native recall and reply hooks."""

import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.core.message.components import Reply

from astrbot_plugin_ovenheart.participation import ParticipationFormatError, parse_plan
from astrbot_plugin_ovenheart.settings import DEFAULTS
from tests.test_participation import decision, requests
from tests.test_retrieval import TRAITS
from tests.test_runtime import event

pytest_plugins = [
    "tests.test_runtime",
    "tests.test_participation",
    "tests.test_retrieval",
]


def test_style_intent_is_optional_bounded_and_cleared_without_reply():
    assert parse_plan(decision().completion_text, DEFAULTS)["expression_intent"] == {}
    style = {"tone": " 温和、简短 ", "avoid": "未经邀请给方案", "scene": " "}
    assert parse_plan(decision(expression_intent=style).completion_text, DEFAULTS)[
        "expression_intent"
    ] == {"tone": "温和、简短", "avoid": "未经邀请给方案"}
    for patch in (
        {"action": "observe"},
        {"action": "wait", "wait_seconds": 1},
        {"confidence": 0.1},
    ):
        assert (
            parse_plan(
                decision(expression_intent=style, **patch).completion_text, DEFAULTS
            )["expression_intent"]
            == {}
        )
    for invalid in (
        None,
        "温和",
        {"tools": "execute"},
        {"tone": ["温和"]},
        {"tone": True},
        {"tone": "x" * 81},
        {"tone": "x\x00"},
        {key: "x" * 80 for key in ("focus", "scene", "prefer", "avoid")},
    ):
        with pytest.raises(ParticipationFormatError):
            parse_plan(decision(expression_intent=invalid).completion_text, DEFAULTS)


async def test_native_style_intent_reaches_recall_selector_and_cached_reply(
    participant,
):
    participant.settings.update(selection_mode="contextual", guidance_delivery="cached")
    incoming = event("intent", text="我只想聊聊今天的事。" * 180)
    incoming.message_obj.message = [
        Reply(id="old", sender_id="bob", message_str="对话前提。" * 240)
    ]
    await participant.capture(incoming)
    scope = (await participant.store.call("state"))["scopes"][0]["id"]
    with sqlite3.connect(participant.store.path) as db:
        for identifier, kind, content in (
            (101, "expression", "温和地倾听"),
            (102, "jargon", "LUX：平安到家"),
        ):
            db.execute(
                "INSERT INTO traits(id,scope,kind,situation,content,confidence,evidence,status,fingerprint,created,updated) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    scope,
                    kind,
                    "聊今天的事时",
                    content,
                    0.95,
                    "[]",
                    "active",
                    str(identifier),
                    1,
                    1,
                ),
            )
    style = {"tone": "温和、简短", "avoid": "不要强行提及 LUX", "focus": "今天的感受"}

    async def completion(**kwargs):
        """Return a planner decision or selection using the real request shape.

        Args:
            **kwargs: Native model request arguments.

        Returns:
            Controlled completion without an external provider.
        """
        if "recent_messages" in json.loads(kwargs["prompt"]):
            return decision(expression_intent=style)
        return SimpleNamespace(completion_text='{"selected_ids":[101]}')

    participant.context.llm_generate.side_effect = completion
    participant.retriever.select = AsyncMock(wraps=participant.retriever.select)
    request = (await requests(participant, incoming))[0]
    request.system_prompt = "稳定宿主前缀"
    await participant.apply_persona(incoming, request)
    selected = json.loads(participant.context.llm_generate.await_args.kwargs["prompt"])
    assert selected["expression_intent"] == style
    recall = participant.retriever.select.await_args
    assert len(recall.args[2]) <= 2400 and "本轮表达意图" in recall.args[2]
    assert "LUX" not in recall.kwargs["literal_query"]
    assert recall.kwargs["expression_intent"] == style
    await participant.apply_participation(incoming, request)
    assert request.system_prompt == "稳定宿主前缀"
    temporary = request.extra_user_content_parts[-1]
    assert json.dumps(style, ensure_ascii=False) in temporary.text
    assert temporary.model_dump_for_context()["_no_save"]
    assert participant.context.llm_generate.await_count == 2
    assert not (await participant.store.call("state", scope))["applications"]


async def test_selector_intent_placeholder_preserves_ordinary_empty_intent(retrieval):
    retriever, _ = retrieval
    retriever.templates["selector"] = "本轮表达意图：{{expression_intent}}"
    retriever.context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(completion_text='{"selected_ids":[]}')
    )
    style = {"tone": "简洁", "avoid": "调侃"}
    await retriever.contextual_ids(
        TRAITS, "不要开玩笑", DEFAULTS, "chat", "身份", expression_intent=style
    )
    payload = retriever.context.llm_generate.await_args.kwargs
    assert json.loads(payload["prompt"])["expression_intent"] == style
    assert json.dumps(style, ensure_ascii=False) in payload["system_prompt"]
    await retriever.contextual_ids(TRAITS, "普通聊天", DEFAULTS, "chat", "身份")
    assert (
        json.loads(retriever.context.llm_generate.await_args.kwargs["prompt"])[
            "expression_intent"
        ]
        == {}
    )
