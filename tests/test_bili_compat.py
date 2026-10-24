"""Frozen context and native SDK headers coexist with optional bili transport."""

import json
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
from astrbot.core.star.context import Context
from openai import AsyncOpenAI

from tests.test_participation import decision, requests
from tests.test_structured_conversation import incoming

pytest_plugins = ["tests.test_structured_conversation"]
bili = pytest.importorskip("astrbot_plugin_oven_multi.features.bili_requests")


async def test_planner_and_native_reply_keep_distinct_transport_and_same_ledger(
    structured,
):
    captured = []

    async def transport(request):
        captured.append((dict(request.headers), json.loads(request.content)))
        return httpx.Response(
            200,
            json={
                "id": "chat-test",
                "object": "chat.completion",
                "created": 1,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": decision(target_sequence=3).completion_text,
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    prov = ProviderOpenAIOfficial(
        {
            "id": "main",
            "type": "openai_chat_completion",
            "model": "test",
            "key": ["synthetic-key"],
            "api_base": "http://127.0.0.1:8787/bili/https://upstream.invalid/v1",
        },
        {},
    )
    await prov.client.close()
    prov.client = AsyncOpenAI(
        api_key="synthetic-key",
        base_url=prov.provider_config["api_base"],
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
    )
    manager = SimpleNamespace(
        provider_insts=[prov], get_provider_by_id=AsyncMock(return_value=prov)
    )
    structured.context.provider_manager = manager
    structured.context.get_provider_by_id = lambda _: prov
    structured.context.get_current_chat_provider_id.return_value = "main"
    structured.context.llm_generate = MethodType(
        Context.llm_generate, structured.context
    )
    companion = SimpleNamespace(
        context=structured.context,
        closing=False,
        bili_proxy=SimpleNamespace(url="http://127.0.0.1:8787"),
    )
    bridge = bili.BiliRequestBridge(companion)
    bridge.install()
    try:
        structured.settings.update(
            participation_mode="auto",
            participation_min_messages=1,
            participation_cooldown=0,
        )
        anchor = incoming("earlier", "EARLIER_MEDIA_TURN", sender="alice")
        await structured.capture(anchor)
        scope = (await structured.resolve_scope(anchor))[0]
        await structured.conversation.delivered(
            anchor,
            scope,
            None,
            "",
            True,
            media=[{"kind": "image", "status": "ready", "text": "SELF_BLUE_CAT_MEME"}],
        )
        await structured.capture(
            incoming("question", "QUESTION_FROM_ALICE", sender="alice")
        )
        trigger = incoming("trigger", "OTHERS_NOISE", sender="bob")
        yielded = await requests(structured, trigger)
        assert len(yielded) == 1
        headers, planner_body = captured[0]
        assert headers["x-bili-passthrough"] == "1"
        assert "x-astrbot-conversation-id" not in headers
        assert "SELF_BLUE_CAT_MEME" in json.dumps(planner_body)
        plan = trigger.get_extra("ovenheart_decision")
        assert plan["target"]["message_id"] == "question"
        epoch = plan["context_epoch"]
        request = yielded[0]
        await structured.apply_persona(trigger, request)
        await structured.apply_participation(trigger, request)
        native_context = await request.assemble_context()
        await prov.text_chat(contexts=[native_context], conversation_id="dialog-a")
        headers, body = captured[-1]
        assert headers["x-astrbot-conversation-id"] == "dialog-a"
        assert "x-bili-passthrough" not in headers
        assert "SELF_BLUE_CAT_MEME" in json.dumps(body)
        assert trigger.get_extra("ovenheart_decision")["target"]["sender_id"] == "alice"
        assert structured.participation.windows[scope]["epoch"] == epoch
        native = structured.context.conversation_manager.get_conversation.return_value
        assert native.history == "[]"
        stored = await structured.store.call("chat_context", scope, "dialog-a")
        assert any(m.get("self") and m.get("media") for m in stored["messages"])
    finally:
        bridge.disconnect()
        await prov.client.close()
