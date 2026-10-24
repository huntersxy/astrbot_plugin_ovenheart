"""Keep evidence intact while shrinking requests and metering real helper usage."""

import asyncio
import json
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.core.provider.entities import TokenUsage

from astrbot_plugin_ovenheart.auxiliary import AuxiliaryUsage
from astrbot_plugin_ovenheart.participation import planner_message
from astrbot_plugin_ovenheart.prompts import auxiliary_payload
from astrbot_plugin_ovenheart.settings import DEFAULTS
from tests.test_participation import decision, requests
from tests.test_retrieval import TRAITS
from tests.test_runtime import event, sources

pytest_plugins = ["tests.test_participation", "tests.test_retrieval"]


def test_learning_passes_share_evidence_prefix_without_duplicating_system():
    system = "固定学习规则和输出格式。" * 100
    base = {
        "task": system,
        "identity_anchor": "固定身份",
        "observations": [
            {"id": i, "sender": "成员", "text": "原始证据，不裁剪。" * 30, "created": i}
            for i in range(10)
        ],
        "enabled_kinds": ["expression"],
        "known_traits": [],
    }
    inputs = []
    for kind in ["expression", "jargon"]:
        data = base | {"task": system + "\n只分析 " + kind, "enabled_kinds": [kind]}
        encoded = auxiliary_payload(
            system, data, prefix=("identity_anchor", "observations")
        )
        decoded = json.loads(encoded)
        assert decoded["observations"] == base["observations"]
        assert decoded["stage_instruction"] == "只分析 " + kind
        assert system not in encoded and "task" not in decoded
        assert len(encoded) < len(json.dumps(data, ensure_ascii=False))
        repaired = auxiliary_payload(
            system,
            data | {"format_error": "schema", "correction_task": "纠正格式"},
            prefix=("identity_anchor", "observations"),
        )
        assert repaired.startswith(encoded[:-1] + ",")
        inputs.append(encoded)
    assert len(os.path.commonprefix(inputs)) > len(inputs[0]) * 0.9


def test_planner_projection_preserves_quotes_media_and_target_identity():
    original = {
        "sequence": 7,
        "sender_id": "alice",
        "sender": "阿力",
        "message_id": "real-id",
        "source": "transport-hash",
        "arrival_order": 22,
        "self": False,
        "replyable": True,
        "text": "这张表情包真好看",
        "reply_to": {"sender_id": "bot", "text": "上一轮的话"},
        "media": [{"status": "ready", "text": "自己发的蓝色猫猫表情包"}],
        "image_refs": ["https://example.invalid/a-long-transport-url"],
        "mentions": [],
    }
    projected = planner_message(original)
    for key in [
        "sequence",
        "sender_id",
        "sender",
        "message_id",
        "self",
        "replyable",
        "text",
        "reply_to",
        "media",
    ]:
        assert projected[key] == original[key]
    assert "source" not in projected and "image_refs" not in projected
    assert (
        planner_message(original | {"message_id": ""})["source"] == original["source"]
    )
    assert original["image_refs"]


async def test_actual_usage_persists_without_prompt_or_secret_storage(plugin):
    plugin.auxiliary.on_record = None
    plugin.context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(
            completion_text="OK",
            usage=TokenUsage(input_cached=128, input_other=16, output=5),
        )
    )
    await plugin.auxiliary.generate(
        "planner",
        chat_provider_id="provider",
        prompt="PRIVATE_CHAT",
        system_prompt="SYSTEM",
    )
    assert not await plugin.store.call("auxiliary_usage")
    await plugin.auxiliary.flush()
    rows = await plugin.store.call("auxiliary_usage")
    row = rows[0]
    assert (
        row["calls"],
        row["reported"],
        row["input_cached"],
        row["input_other"],
        row["output"],
    ) == (1, 1, 128, 16, 5)
    assert row["prompt_chars"] == len("PRIVATE_CHAT")
    assert "PRIVATE_CHAT" not in json.dumps(rows)
    plugin.context.llm_generate.return_value = SimpleNamespace(
        completion_text="no usage"
    )
    await plugin.auxiliary.generate("planner", chat_provider_id="provider", prompt="x")
    await plugin.auxiliary.flush()
    row = (await plugin.store.call("auxiliary_usage"))[0]
    assert row["calls"] == 2 and row["reported"] == 1


async def test_usage_background_write_never_delays_model_response(plugin):
    entered, release = asyncio.Event(), asyncio.Event()
    call = plugin.store.call

    async def delayed(operation, *args):
        if operation == "auxiliary_usage" and args:
            entered.set()
            await release.wait()
        return await call(operation, *args)

    plugin.auxiliary.store = SimpleNamespace(call=delayed)
    plugin.context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(completion_text="OK")
    )
    response = await asyncio.wait_for(
        plugin.auxiliary.generate("planner", prompt="x"), 0.5
    )
    assert response.completion_text == "OK"
    await entered.wait()
    release.set()
    await plugin.auxiliary_flush_task
    assert (await plugin.store.call("auxiliary_usage"))[0]["calls"] == 1


async def test_cancellation_does_not_duplicate_flushed_usage(plugin):
    usage = AuxiliaryUsage(plugin.context, plugin.store)
    plugin.context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(completion_text="OK")
    )
    await usage.generate("selector", chat_provider_id="provider", prompt="x")
    entered, release = asyncio.Event(), asyncio.Event()
    call = plugin.store.call

    async def delayed(operation, *args):
        if operation == "auxiliary_usage" and args:
            entered.set()
            await release.wait()
        return await call(operation, *args)

    usage.store = SimpleNamespace(call=delayed)
    flushing = asyncio.create_task(usage.flush())
    await entered.wait()
    flushing.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await flushing
    await usage.flush()
    assert (await plugin.store.call("auxiliary_usage"))[0]["calls"] == 1


async def test_selector_backoff_skips_failures_but_preserves_exact_cache_hits(
    retrieval,
):
    retriever, provider = retrieval
    retriever.context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(completion_text='{"selected_ids":[1]}')
    )
    await retriever.select("scope-a", TRAITS, "GOOD", DEFAULTS, provider_id="chat")
    retriever.context.llm_generate.side_effect = TimeoutError()
    for query in ["FAIL1", "FAIL2"]:
        result = await retriever.select(
            "scope-a", TRAITS, query, DEFAULTS, provider_id="chat"
        )
        assert result["mode"] == "contextual_fallback"
    skipped = await retriever.select(
        "scope-a", TRAITS, "NEW_QUERY", DEFAULTS, provider_id="chat"
    )
    assert "秒后重试" in skipped["message"]
    assert retriever.context.llm_generate.await_count == 3
    cached = await retriever.select(
        "scope-a", TRAITS, "GOOD", DEFAULTS, provider_id="chat"
    )
    assert cached["selector_cache_hit"]
    for key, value in list(retriever.selector_failures.items()):
        retriever.selector_failures[key] = (value[0], 0)
    retriever.context.llm_generate.side_effect = None
    recovered = await retriever.select(
        "scope-a", TRAITS, "NEW_QUERY", DEFAULTS, provider_id="chat"
    )
    assert recovered["mode"] == "contextual" and not retriever.selector_failures


async def test_regular_planner_interval_reduces_checks_without_changing_evidence(
    participant,
):
    participant.settings.update(
        participation_mode="observe", planner_interval_seconds=60
    )
    participant.context.llm_generate.return_value = decision("observe")
    first = event("first")
    assert not await requests(participant, first)
    scope = (await participant.resolve_scope(first))[0]
    participant.participation.windows[scope]["last_check"] = time.monotonic() - 40
    assert not await requests(participant, event("second"))
    assert participant.context.llm_generate.await_count == 1
    participant.participation.windows[scope]["last_check"] = time.monotonic() - 61
    assert not await requests(participant, event("third"))
    assert participant.context.llm_generate.await_count == 2


async def test_real_learning_requests_keep_both_passes_and_same_system_prefix(plugin):
    scope = await sources(plugin)
    assert (await plugin.learn(scope))["status"] == "success"
    calls = plugin.context.llm_generate.await_args_list
    assert len(calls) == 2
    assert calls[0].kwargs["system_prompt"] == calls[1].kwargs["system_prompt"]
    a, b = [json.loads(c.kwargs["prompt"]) for c in calls]
    assert a["observations"] == b["observations"]
    assert a["stage_instruction"] != b["stage_instruction"]
    assert all("task" not in p for p in [a, b])
