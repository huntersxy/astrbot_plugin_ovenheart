"""Request and media timings preserve native transport behavior and private data."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.provider.entities import LLMResponse

import core.auxiliary as auxiliary
from core.auxiliary import AuxiliaryUsage, MeteredProviderManager
from core.memory.emoji_actions import EmojiActions
from core.settings import DEFAULTS


@pytest.fixture
def trace():
    events = []
    return SimpleNamespace(
        events=events,
        record=lambda name, **fields: events.append({"name": name, **fields}),
    )


async def test_nonstream_trace_records_sizes_without_claiming_first_chunk(trace):
    response = LLMResponse(role="assistant", completion_text="answer")
    invoke = AsyncMock(return_value=response)
    provider = SimpleNamespace(provider_config={"id": "chat"}, text_chat=invoke)
    usage = AuxiliaryUsage(None, None)
    wrapped = usage.native_provider(provider, trace=trace)
    kwargs = {
        "prompt": "private prompt",
        "system_prompt": "system",
        "contexts": [{"role": "user", "content": "private history"}],
        "func_tool": ToolSet(
            [FunctionTool(name="search", description="search", parameters={})]
        ),
    }
    assert await wrapped.text_chat(**kwargs) is response
    invoke.assert_awaited_once_with(**kwargs)
    timing = trace.events[-1]
    assert timing["stage"] == "native_reply"
    assert timing["status"] == "success"
    assert timing["first_chunk_measured"] is False
    assert "first_chunk_seconds" not in timing
    assert timing["prompt_chars"] == len("private prompt")
    assert timing["history_chars"] == len("private history")
    assert timing["tool_count"] == 1
    assert timing["tool_schema_chars"] > 0
    assert "private" not in json.dumps(timing)


async def test_stream_trace_ignores_empty_chunks_and_preserves_parts(
    trace, monkeypatch
):
    now = [10.0]
    monkeypatch.setattr(
        auxiliary, "time", SimpleNamespace(monotonic=lambda: now[0], time=time.time)
    )
    parts = [
        None,
        LLMResponse(role="assistant", is_chunk=True),
        LLMResponse(role="assistant", is_chunk=True, completion_text="a"),
        LLMResponse(role="assistant", completion_text="answer"),
    ]

    async def invoke(**_):
        for elapsed, part in zip((1, 2, 3, 5), parts, strict=True):
            now[0] = 10 + elapsed
            yield part

    provider = SimpleNamespace(provider_config={"id": "chat"}, text_chat_stream=invoke)
    usage = AuxiliaryUsage(None, None)
    received = [
        part
        async for part in usage.native_provider(provider, trace=trace).text_chat_stream(
            prompt="question"
        )
    ]
    assert received == parts
    assert trace.events[-1]["first_chunk_seconds"] == 3
    assert trace.events[-1]["seconds"] == 5
    assert trace.events[-1]["status"] == "success"


@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
async def test_failed_model_calls_keep_their_error_and_timing(trace, error):
    usage = AuxiliaryUsage(None, None)
    with pytest.raises(error):
        await usage.measure("planner", AsyncMock(side_effect=error()), {}, trace=trace)
    assert trace.events[-1]["status"] == (
        "cancelled" if error is asyncio.CancelledError else "error"
    )


async def test_resumed_planner_requests_follow_the_current_message_trace(trace):
    response = LLMResponse(role="assistant", completion_text="done")
    provider = SimpleNamespace(text_chat=AsyncMock(return_value=response))
    current = [trace]
    manager = MeteredProviderManager(
        SimpleNamespace(get_provider_by_id=AsyncMock(return_value=provider)),
        AuxiliaryUsage(None, None),
        "planner",
        "chat",
        trace=lambda: current[0],
    )
    wrapped = await manager.get_provider_by_id("chat")
    await wrapped.text_chat(prompt="first")
    current[0] = SimpleNamespace(record=lambda *_, **__: None)
    await wrapped.text_chat(prompt="resumed")
    assert len(trace.events) == 1
    assert provider.text_chat.await_count == 2


@pytest.mark.parametrize("failed", [False, True])
async def test_emoji_trace_separates_asset_lookup_from_platform_send(
    trace, tmp_path, failed
):
    extra = {}
    event = SimpleNamespace(
        trace=trace,
        get_extra=lambda key, default=None: extra.get(key, default),
        set_extra=extra.__setitem__,
        send=AsyncMock(side_effect=RuntimeError("platform failed") if failed else None),
    )
    plugin = SimpleNamespace(
        settings=DEFAULTS,
        image_memory=SimpleNamespace(permitted=AsyncMock(return_value=True)),
        room_memory=SimpleNamespace(
            call=AsyncMock(
                return_value={
                    "success": True,
                    "path": str(tmp_path / "emoji.png"),
                    "asset": {"content_hash": "digest"},
                }
            )
        ),
    )
    session = SimpleNamespace(
        scope="scope",
        persona="persona",
        check_current=AsyncMock(),
        emojis={1: {"scope": "scope", "asset_id": "asset", "caption": "caption"}},
    )
    result = await EmojiActions(plugin).send(event, session, 1)
    assert result["sent"] is not failed
    event.send.assert_awaited_once()
    assert [item["name"] for item in trace.events] == [
        "ovenheart_emoji_asset",
        "ovenheart_emoji_send",
    ]
    assert trace.events[-1]["status"] == ("error" if failed else "success")
