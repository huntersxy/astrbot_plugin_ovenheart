"""Use the original summary/episode algorithms and real SQLite/native events."""

import ast
import asyncio
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_ovenheart.memory.room_memory import RoomMemory
from tests.test_room_memory import SyntheticEmbedding
from tests.test_runtime import event

pytest_plugins = ["tests.test_participation"]


async def test_new_small_session_does_not_open_memory_but_existing_session_is_checked(tmp_path):
    from astrbot_plugin_ovenheart.memory.memory_consolidation import MemoryConsolidation

    rooms = SimpleNamespace(
        directory=tmp_path,
        rooms={},
        storage_scope=AsyncMock(side_effect=lambda scope: scope),
        call=AsyncMock(return_value={"attempted": 0, "processed": [], "episode_pending": False}),
    )
    plugin = SimpleNamespace(
        room_memory=rooms,
        settings={"memory_isolation": True, "memory_summary_min_messages": 12, "memory_summary_interval": 600},
    )
    consolidation = MemoryConsolidation(plugin)
    consolidation.permitted = AsyncMock(return_value=True)
    consolidation.inputs = AsyncMock(return_value=[{"source_id": "source", "text": "实际正文"}])
    await consolidation.observe(object(), "scope", "persona")
    rooms.call.assert_not_awaited()
    metadata = tmp_path / "scope" / "metadata" / "metadata.db"
    metadata.parent.mkdir(parents=True)
    metadata.touch()
    await consolidation.observe(object(), "scope", "persona")
    rooms.call.assert_awaited_once()


def summary(
    source="s1", quote="大家周六一起去书店", text="群友约好周六一起去书店。", **patch
):
    return SimpleNamespace(
        completion_text=json.dumps(
            {
                "summary": text,
                "entities": ["书店"],
                "relations": [],
                "facts": [],
                "evidence": [{"source_id": source, "quote": quote}],
                **patch,
            },
            ensure_ascii=False,
        )
    )


def episode():
    return SimpleNamespace(
        completion_text=json.dumps(
            {
                "episodes": [
                    {
                        "title": "周六书店之约",
                        "summary": "群友约好周六一起去书店。",
                        "paragraph_indices": [1],
                        "participants": ["alice"],
                        "keywords": ["书店"],
                        "time_confidence": 0.95,
                        "llm_confidence": 0.9,
                    }
                ]
            },
            ensure_ascii=False,
        )
    )


def test_summary_batches_bound_input_without_losing_minimum_or_source_text():
    from astrbot_plugin_ovenheart.memory.memory_consolidation import summary_batch

    rows = [{"source_id": str(i), "text": "原" * 1000} for i in range(50)]
    assert summary_batch(rows, set(), 12) == rows[:12]
    assert summary_batch(rows, {"0"}, 12) == rows[1:13]
    assert summary_batch(rows, set(), 30) == rows[:30]
    quoted = [row | {"text": "正文" * 50, "reply_to": {"text": "引" * 1000}} for row in rows]
    assert summary_batch(quoted, set(), 12) == quoted[:12]


async def test_summary_model_timeout_retains_sources_without_import_error_trace(consolidator, caplog):
    p = consolidator
    p.settings["embedding_provider_id"] = "semantic"
    p.context.get_provider_by_id = lambda name: SyntheticEmbedding() if name == "semantic" else None
    p.context.llm_generate.side_effect = TimeoutError()
    incoming, scope, persona = await capture(p, "timed-out-source")
    assert p.room_memory.rooms[scope].metadata.count_paragraphs() == 0
    assert not (await p.room_memory.call(scope, "summary_status"))["processed"]
    assert len(await p.store.call("room_memory_inputs", scope)) == 1
    assert "等待预算" in caplog.text
    assert "总结导入过程中出错" not in caplog.text
    assert "RuntimeError, phase=summary_model" not in caplog.text


@pytest.fixture
async def consolidator(participant):
    participant.settings.update(
        native_memory_enabled=True,
        memory_summary_enabled=True,
        memory_summary_min_messages=1,
        memory_summary_interval=0,
    )
    participant.context.get_provider_by_id = lambda name: None
    return participant


async def drain(plugin):
    await asyncio.gather(*tuple(plugin.memory_consolidation.busy.values()))


async def capture(plugin, source="s1", text="大家周六一起去书店", **kwargs):
    incoming = event(source, text=text, **kwargs)
    await plugin.capture(incoming)
    scope, persona, _ = await plugin.resolve_scope(incoming)
    await drain(plugin)
    return incoming, scope, persona


async def test_background_summary_episode_shared_retrieval_and_restart(consolidator):
    p = consolidator
    p.context.llm_generate.side_effect = [summary(), episode()]
    incoming, scope, persona = await capture(p)
    state = p.room_memory.rooms[scope]
    assert state.metadata.count_paragraphs() == 1
    found = await p.room_memory.search(incoming, scope, persona, "书店")
    assert found[0]["type"] == "episode"
    assert found[0]["metadata"]["evidence"][0]["source_id"] == "s1"
    assert p.context.llm_generate.call_count == 2
    await p.auxiliary.flush()
    assert {u["stage"] for u in await p.store.call("auxiliary_usage")} == {
        "memory_summary",
        "memory_episode",
    }
    status = await p.room_memory.call(scope, "summary_status")
    assert status["processed"] == {"s1"} and not status["episode_pending"]
    await p.room_memory.close()
    p.room_memory = RoomMemory(p)
    await p.memory_consolidation.observe(incoming, scope, persona)
    await drain(p)
    assert p.context.llm_generate.call_count == 2
    assert (await p.room_memory.search(incoming, scope, persona, "书店"))[0][
        "type"
    ] == "episode"


async def test_incremental_review_empty_delta_and_no_repeated_model_call(consolidator):
    p = consolidator
    p.context.llm_generate.side_effect = [
        summary(),
        episode(),
        summary(
            text="",
            entities=[],
            evidence=[],
        ),
    ]
    await capture(p)
    incoming, scope, persona = await capture(p, "s2", "嗯嗯，知道啦")
    prompt = p.context.llm_generate.call_args.kwargs["prompt"]
    assert "历史净化摘要回顾" in prompt and "群友约好周六" in prompt
    assert "[s2]" in prompt and "[s1]" not in prompt
    assert p.room_memory.rooms[scope].metadata.count_paragraphs() == 1
    assert (await p.room_memory.call(scope, "summary_status"))["processed"] == {
        "s1",
        "s2",
    }
    await p.memory_consolidation.observe(incoming, scope, persona)
    await drain(p)
    assert p.context.llm_generate.call_count == 3


async def test_existing_memory_is_readable_while_summary_model_is_waiting(consolidator):
    p = consolidator
    p.context.llm_generate.side_effect = [summary(), episode()]
    incoming, scope, persona = await capture(p)
    assert await p.room_memory.search(incoming, scope, persona, "书店")
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed(**kwargs):
        started.set()
        await release.wait()
        return summary(text="", entities=[], evidence=[])

    p.context.llm_generate.side_effect = delayed
    new_source = event("s2", text="嗯嗯，知道啦")
    await p.capture(new_source)
    await asyncio.wait_for(started.wait(), 5)
    try:
        found = await asyncio.wait_for(
            p.room_memory.search(new_source, scope, persona, "书店"), 1.5
        )
        assert found and "书店" in found[0]["content"]
        assert not release.is_set()
    finally:
        release.set()
        await drain(p)


async def test_existing_memory_is_readable_while_summary_embedding_is_waiting(
    consolidator,
):
    p = consolidator
    started, release = asyncio.Event(), asyncio.Event()

    class DelayedSummaryEmbedding(SyntheticEmbedding):
        async def get_embeddings(self, texts):
            if "群友周日一起去图书馆。" in texts:
                started.set()
                await release.wait()
            return await super().get_embeddings(texts)

    embedding = DelayedSummaryEmbedding()
    p.settings["embedding_provider_id"] = "semantic"
    p.context.get_provider_by_id = lambda _: embedding
    p.context.llm_generate.side_effect = [summary(), episode()]
    _, scope, persona = await capture(p)
    p.context.llm_generate.side_effect = [
        summary(
            source="s2",
            quote="周日一起去图书馆",
            text="群友周日一起去图书馆。",
            entities=["图书馆"],
        ),
        episode(),
    ]
    incoming = event("s2", text="周日一起去图书馆")
    await p.capture(incoming)
    await asyncio.wait_for(started.wait(), 5)
    try:
        found = await asyncio.wait_for(
            p.room_memory.search(incoming, scope, persona, "书店"), 4.5
        )
        assert any("书店" in row["content"] for row in found)
        assert not release.is_set()
    finally:
        release.set()
        await drain(p)
    state = p.room_memory.rooms[scope]
    assert state.metadata.count_paragraphs() == 2
    assert (await p.room_memory.call(scope, "summary_status"))["processed"] == {
        "s1",
        "s2",
    }


async def test_foreground_memory_and_image_ingest_during_sdk_episode_model(
    consolidator,
):
    from tests.test_image_memory import picture

    p = consolidator
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed(**kwargs):
        if p.context.llm_generate.call_count == 1:
            return summary()
        started.set()
        await release.wait()
        return episode()

    p.context.llm_generate.side_effect = delayed
    incoming = event("s1", text="大家周六一起去书店")
    await p.capture(incoming)
    scope, persona, _ = await p.resolve_scope(incoming)
    await asyncio.wait_for(started.wait(), 5)
    try:
        assert await asyncio.wait_for(
            p.room_memory.search(incoming, scope, persona, "书店"), 1.5
        )
        saved = await asyncio.wait_for(
            p.room_memory.call(
                scope,
                "image_ingest",
                picture(),
                "s1",
                "s1",
                0,
                incoming.created_at,
                "",
                event=incoming,
            ),
            1.5,
        )
        assert saved["success"]
        assert not release.is_set()
    finally:
        release.set()
        await drain(p)


async def test_cancelled_concurrent_read_does_not_cancel_the_background_model(
    consolidator,
):
    p = consolidator
    query_started, query_cancelled = asyncio.Event(), asyncio.Event()

    class DelayedQueryEmbedding(SyntheticEmbedding):
        async def get_embeddings(self, texts):
            if "SLOW_QUERY" in texts:
                query_started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    query_cancelled.set()
            return await super().get_embeddings(texts)

    p.settings["embedding_provider_id"] = "semantic"
    embedding = DelayedQueryEmbedding()
    p.context.get_provider_by_id = lambda key: embedding if key == "semantic" else None
    p.context.llm_generate.side_effect = [summary(), episode()]
    _, scope, persona = await capture(p)
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed(**kwargs):
        started.set()
        await release.wait()
        return summary(text="", entities=[], evidence=[])

    p.context.llm_generate.side_effect = delayed
    incoming = event("s2", text="嗯嗯，知道啦")
    await p.capture(incoming)
    await asyncio.wait_for(started.wait(), 5)
    read = asyncio.create_task(
        p.room_memory.search(incoming, scope, persona, "SLOW_QUERY")
    )
    try:
        await asyncio.wait_for(query_started.wait(), 5)
        read.cancel()
        with pytest.raises(asyncio.CancelledError):
            await read
        await asyncio.wait_for(query_cancelled.wait(), 5)
        assert not p.memory_consolidation.busy[scope].done()
        assert await asyncio.wait_for(
            p.room_memory.search(incoming, scope, persona, "书店"), 1.5
        )
    finally:
        read.cancel()
        await asyncio.gather(read, return_exceptions=True)
        release.set()
        await drain(p)


async def test_foreground_write_and_read_keep_both_sets_of_graph_data(
    consolidator,
):
    p = consolidator
    p.context.llm_generate.side_effect = [summary(), episode()]
    _, scope, persona = await capture(p)
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed(**kwargs):
        started.set()
        await release.wait()
        return summary(text="", entities=[], evidence=[])

    p.context.llm_generate.side_effect = delayed
    incoming = event("s2", text="我想去图书馆读书")
    await p.capture(incoming)
    await asyncio.wait_for(started.wait(), 5)
    p.participation.sources[scope, "s2"] = incoming
    writer = asyncio.create_task(
        p.room_memory.remember(
            incoming,
            scope,
            persona,
            {
                "content": "群友想去图书馆读书。",
                "evidence": [{"msg_id": "1", "quote": "去图书馆读书"}],
                "relations": [
                    {"subject": "群友", "predicate": "想去", "object": "图书馆"}
                ],
            },
            [{"sequence": 1, "source": "s2", "sender_id": incoming.get_sender_id()}],
        )
    )
    try:
        assert await asyncio.wait_for(
            p.room_memory.search(incoming, scope, persona, "书店"), 1.5
        )
        assert (await asyncio.wait_for(writer, 1.5))["stored"]
        assert not release.is_set()
    finally:
        release.set()
        await drain(p)
    assert (await writer)["stored"]
    state = p.room_memory.rooms[scope]
    assert state.metadata.count_paragraphs() == 2
    assert state.graph.num_nodes == 2 and state.graph.num_edges == 1
    # SDK graph projection contains relations; standalone summary entities
    # remain stored and retrievable without requiring an isolated graph node.
    assert state.metadata._conn.execute(
        "SELECT 1 FROM entities WHERE name='书店'"
    ).fetchone()
    assert any(
        "图书馆" in m["content"]
        for m in await p.room_memory.search(incoming, scope, persona, "图书馆")
    )


async def test_configuration_change_cancels_old_summary_and_unblocks_new_runtime(
    consolidator,
):
    p = consolidator
    p.context.llm_generate.side_effect = [summary(), episode()]
    _, scope, persona = await capture(p)
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def delayed(**kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    p.context.llm_generate.side_effect = delayed
    incoming = event("s2", text="嗯嗯，知道啦")
    await p.capture(incoming)
    await asyncio.wait_for(started.wait(), 5)
    await p.update_settings({"memory_summary_enabled": False})
    await asyncio.wait_for(cancelled.wait(), 5)
    assert await asyncio.wait_for(
        p.room_memory.search(incoming, scope, persona, "书店"), 1.5
    )
    await drain(p)
    assert p.room_memory.rooms[scope].metadata.count_paragraphs() == 1


async def test_summary_uses_recovered_vector_pools_after_foreground_writes(
    consolidator,
):
    p = consolidator

    class RecoveringEmbedding(SyntheticEmbedding):
        async def get_embeddings(self, texts):
            if "TRANSIENT" in texts:
                raise RuntimeError("temporary embedding transport failure")
            return await super().get_embeddings(texts)

    embedding = RecoveringEmbedding()
    p.settings["embedding_provider_id"] = "semantic"
    p.context.get_provider_by_id = lambda key: embedding if key == "semantic" else None
    p.context.llm_generate.side_effect = [
        summary(quote="Neko喜欢猫咪", text="Neko喜欢猫咪。", entities=["Neko", "猫咪"]),
        episode(),
    ]
    _, scope, persona = await capture(p, text="Neko喜欢猫咪")
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed(**kwargs):
        if "AstrBot原文来源校验" in kwargs["prompt"]:
            started.set()
            await release.wait()
            return summary(
                source="s2",
                quote="Neko周末去看小猫",
                text="Neko周末想去看小猫。",
                entities=["Neko", "小猫"],
            )
        return episode()

    p.context.llm_generate.side_effect = delayed
    incoming = event("s2", text="Neko周末去看小猫")
    await p.capture(incoming)
    await asyncio.wait_for(started.wait(), 5)
    state = p.room_memory.rooms[scope]
    assert state.paragraph_vectors is not None
    try:
        await p.room_memory.search(incoming, scope, persona, "TRANSIENT")
        assert state.paragraph_vectors is None
        p.participation.sources[scope, "s2"] = incoming
        saved = await asyncio.wait_for(
            p.room_memory.remember(
                incoming,
                scope,
                persona,
                {
                    "content": "Neko说过周末去看小猫。",
                    "evidence": [{"msg_id": "1", "quote": "Neko周末去看小猫"}],
                    "relations": [],
                },
                [
                    {
                        "sequence": 1,
                        "source": "s2",
                        "sender_id": incoming.get_sender_id(),
                    }
                ],
            ),
            2,
        )
        assert saved["stored"]
        assert state.paragraph_vectors is state.kernel.paragraph_vector_store
    finally:
        release.set()
        await drain(p)
    assert state.metadata.count_paragraphs() == state.paragraph_vectors.num_vectors == 3
    await p.room_memory.close()
    p.room_memory = RoomMemory(p)
    await p.room_memory.search(incoming, scope, persona, "GATTO")
    assert p.room_memory.rooms[scope].paragraph_vectors.num_vectors == 3


async def test_foreground_write_invalidates_inflight_episode_and_rebuilds_current_source(
    consolidator,
):
    p = consolidator
    p.settings.update(memory_summary_enabled=False, memory_summary_min_messages=50)
    incoming, scope, persona = await capture(p)
    p.participation.sources[scope, "s1"] = incoming
    await p.room_memory.remember(
        incoming,
        scope,
        persona,
        {
            "content": "周六一起去书店。",
            "evidence": [{"msg_id": "1", "quote": "大家周六一起去书店"}],
            "relations": [],
        },
        [{"sequence": 1, "source": "s1", "sender_id": incoming.get_sender_id()}],
    )
    p.settings["memory_summary_enabled"] = True
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed(**kwargs):
        started.set()
        await release.wait()
        return episode()

    p.context.llm_generate.side_effect = delayed
    await p.memory_consolidation.observe(incoming, scope, persona)
    await asyncio.wait_for(started.wait(), 5)
    later = event("s2", text="周日再去图书馆")
    await p.capture(later)
    p.participation.sources[scope, "s2"] = later
    try:
        await asyncio.wait_for(
            p.room_memory.remember(
                later,
                scope,
                persona,
                {
                    "content": "周日再去图书馆。",
                    "evidence": [{"msg_id": "1", "quote": "周日再去图书馆"}],
                    "relations": [],
                },
                [{"sequence": 1, "source": "s2", "sender_id": later.get_sender_id()}],
            ),
            1.5,
        )
    finally:
        release.set()
        await drain(p)
    state = p.room_memory.rooms[scope]
    assert not state.metadata.get_episodes_by_source("chat_summary:" + scope)
    assert state.metadata.get_episode_source_rebuild_summary()["counts"]["pending"] == 1
    payload = json.loads(episode().completion_text)
    payload["episodes"][0].update(
        summary="周六去书店，周日去图书馆。", paragraph_indices=[1, 2]
    )
    p.context.llm_generate.side_effect = [
        SimpleNamespace(completion_text=json.dumps(payload, ensure_ascii=False))
    ]
    await p.memory_consolidation.observe(later, scope, persona)
    await drain(p)
    found = state.metadata.get_episodes_by_source("chat_summary:" + scope)
    assert len(found) == 1 and found[0]["paragraph_count"] == 2


@pytest.mark.parametrize(
    "bad",
    [
        summary(quote="不存在的原话"),
        summary(source="outside"),
        summary(relations=[{"subject": "书店", "predicate": " ", "object": "周六"}]),
        summary(evidence=[]),
    ],
)
async def test_invalid_batch_preserves_pending_and_retries_without_partial_writes(
    consolidator, bad
):
    p = consolidator
    p.context.llm_generate.side_effect = [bad]
    incoming, scope, persona = await capture(p)
    assert p.room_memory.rooms[scope].metadata.count_paragraphs() == 0
    assert not (await p.room_memory.call(scope, "summary_status"))["processed"]
    p.context.llm_generate.side_effect = [summary(), episode()]
    await p.memory_consolidation.observe(incoming, scope, persona)
    await drain(p)
    assert p.room_memory.rooms[scope].metadata.count_paragraphs() == 1


@pytest.mark.parametrize("revoke", ["learning", "clear", "persona"])
async def test_generation_rechecks_current_permission_and_retained_sources(
    consolidator, revoke
):
    p = consolidator
    started, release = asyncio.Event(), asyncio.Event()

    async def generate(**kwargs):
        started.set()
        await release.wait()
        return summary()

    p.context.llm_generate.side_effect = generate
    incoming = event("s1", text="大家周六一起去书店")
    await p.capture(incoming)
    scope, _, _ = await p.resolve_scope(incoming)
    await asyncio.wait_for(started.wait(), 5)
    if revoke == "learning":
        p.settings["learning_enabled"] = False
    elif revoke == "clear":
        import sqlite3

        with sqlite3.connect(p.store.path) as db:
            db.execute("DELETE FROM messages WHERE scope=?", (scope,))
    else:
        p.context.persona_manager.resolve_selected_persona.return_value = (
            "changed",
            {"prompt": "别的人格"},
            None,
            False,
        )
    release.set()
    await drain(p)
    assert p.room_memory.rooms[scope].metadata.count_paragraphs() == 0
    assert not (await p.room_memory.call(scope, "summary_status"))["processed"]


async def test_episode_failure_keeps_last_published_snapshot(consolidator):
    p = consolidator
    p.context.llm_generate.side_effect = [
        summary(),
        episode(),
        summary(
            source="s2",
            quote="后来我们改约周日去书店",
            text="后来群友把书店之约改到周日。",
        ),
        RuntimeError("segmentation provider unavailable"),
    ]
    _, scope, _ = await capture(p)
    state = p.room_memory.rooms[scope]
    previous = state.metadata.get_episodes_by_source("chat_summary:" + scope)
    assert len(previous) == 1
    await capture(p, "s2", "后来我们改约周日去书店")
    assert state.metadata.count_paragraphs() == 2
    assert state.metadata.get_episodes_by_source("chat_summary:" + scope) == previous
    assert state.metadata.get_episode_source_rebuild_summary()["counts"]["failed"] == 1
    assert (await p.room_memory.call(scope, "summary_status"))["processed"] == {
        "s1",
        "s2",
    }


async def test_other_scope_does_not_receive_episodes_and_learning_pause_still_reads(
    consolidator,
):
    p = consolidator
    p.context.llm_generate.side_effect = [summary(), episode()]
    incoming, scope, persona = await capture(p)
    p.settings["learning_enabled"] = False
    assert await p.room_memory.search(incoming, scope, persona, "书店")
    other = event("s2", text="书店", session="other")
    other_scope, other_persona, _ = await p.resolve_scope(other)
    assert not await p.room_memory.search(other, other_scope, other_persona, "书店")
    assert not await p.room_memory.search(other, scope, persona, "书店")


async def test_summary_dual_vectors_persist_and_find_nonlexical_memory(consolidator):
    p = consolidator
    p.settings["embedding_provider_id"] = "semantic"
    embedding = SyntheticEmbedding()
    p.context.get_provider_by_id = lambda key: embedding if key == "semantic" else None
    p.context.llm_generate.side_effect = [
        summary(
            quote="Neko喜欢猫咪",
            text="Neko喜欢猫咪，周末打算去看小猫。",
            entities=["Neko", "猫咪"],
        ),
        episode(),
    ]
    incoming, scope, persona = await capture(p, text="Neko喜欢猫咪")
    state = p.room_memory.rooms[scope]
    assert state.paragraph_vectors.has_data() and state.graph_vectors.has_data()
    assert (
        state.paragraph_vectors.num_vectors == 1
        # The SDK also embeds the evidence's real participant (alice).
        and state.graph_vectors.num_vectors == 3
    )
    await p.room_memory.close()
    p.room_memory = RoomMemory(p)
    found = await p.room_memory.search(incoming, scope, persona, "GATTO")
    assert any("Neko喜欢猫咪" in m["content"] for m in found)


async def test_repeated_summary_retains_both_native_sources(consolidator):
    p = consolidator
    p.context.llm_generate.side_effect = [summary(), episode(), summary(source="s2")]
    await capture(p)
    incoming, scope, persona = await capture(p, "s2")
    found = await p.room_memory.search(incoming, scope, persona, "书店")
    paragraph = next(r for r in found if r["type"] == "paragraph")
    assert {e["source_id"] for e in paragraph["metadata"]["evidence"]} == {"s1", "s2"}


async def test_cancelled_background_model_releases_room_and_can_retry(consolidator):
    p = consolidator
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def generate(**kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    p.context.llm_generate.side_effect = generate
    incoming = event("s1", text="大家周六一起去书店")
    await p.capture(incoming)
    scope, persona, _ = await p.resolve_scope(incoming)
    await asyncio.wait_for(started.wait(), 5)
    task = p.memory_consolidation.busy[scope]
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.wait_for(cancelled.wait(), 5)
    assert not (await p.room_memory.call(scope, "summary_status"))["processed"]
    p.context.llm_generate.side_effect = [summary(), episode()]
    await p.memory_consolidation.observe(incoming, scope, persona)
    await drain(p)
    assert await p.room_memory.search(incoming, scope, persona, "书店")


def test_original_summary_episode_algorithms_and_prompts_match_pinned_upstream():
    root = Path(__file__).resolve().parents[1]
    port = root / "vendor/maibot/amemorix"
    upstream = root.parent / "MaiBot"
    if not (upstream / ".git").exists():
        pytest.skip("upstream checkout unavailable")
    manifest = json.loads((port / "source_manifest.json").read_text(encoding="utf-8"))
    names = {
        "utils/summary_importer.py": "SummaryImporter",
        "utils/episode_service.py": "EpisodeService",
        "utils/episode_segmentation_service.py": "EpisodeSegmentationService",
        "utils/episode_retrieval_service.py": "EpisodeRetrievalService",
        "runtime/services/ingest_service.py": "MemoryIngestService",
    }
    for name, class_name in names.items():
        entry = manifest["files"][name]
        blob = subprocess.check_output(
            ["git", "show", manifest["commit"] + ":" + entry["source"]], cwd=upstream
        )
        assert hashlib.sha256(blob).hexdigest() == entry["upstream_sha256"]
        original = next(
            c
            for c in ast.parse(blob).body
            if isinstance(c, ast.ClassDef) and c.name == class_name
        )
        native = next(
            c
            for c in ast.parse((port / name).read_bytes()).body
            if isinstance(c, ast.ClassDef) and c.name == class_name
        )
        if name.endswith("summary_importer.py"):
            for cls in (original, native):
                cls.body = [
                    m
                    for m in cls.body
                    if getattr(m, "name", "") != "_ensure_runtime_self_check"
                ]
                for method in cls.body:
                    if getattr(method, "name", "") == "_import_from_stream_unlocked":
                        for node in ast.walk(method):
                            if isinstance(node, ast.Try):
                                node.handlers = [h for h in node.handlers if not (isinstance(h.type, ast.Name) and h.type.id == "TimeoutError" and len(h.body) == 1 and isinstance(h.body[0], ast.Raise) and h.body[0].exc is None)]
        assert ast.dump(original) == ast.dump(native)
    for name, entry in manifest["files"].items():
        assert (
            hashlib.sha256((port / name).read_bytes()).hexdigest()
            == entry["ported_sha256"]
        )


async def test_shared_summary_keeps_real_source_and_cross_room_retrieval(consolidator):
    p = consolidator
    p.settings["memory_isolation"] = False
    incoming = event("shared-source")
    scope, persona, _ = await p.resolve_scope(incoming)
    p.context.llm_generate.side_effect = [summary(source=scope + ":shared-source"), episode()]
    await p.capture(incoming := event("shared-source", text="大家周六一起去书店"))
    await drain(p)
    storage = await p.room_memory.storage_scope(scope)
    assert (await p.room_memory.call(scope, "summary_status"))["processed"] == {scope + ":shared-source"}
    other = event("other-room", session="456")
    other_scope, other_persona, _ = await p.resolve_scope(other)
    await p.store.call("touch_scope", other_scope, other.unified_msg_origin, other_persona, "Other")
    found = await p.room_memory.search(other, other_scope, other_persona, "书店")
    assert any(proof["source_id"] == scope + ":shared-source" for row in found for proof in row["metadata"].get("evidence", []))
    state = p.room_memory.rooms[storage]
    rows = await p.room_memory.messages(state, chat_id=scope, start_time=0, end_time=9999999999, limit=10, limit_mode="latest", filter_mai=False, filter_command=False)
    assert any(row["source_id"] == scope + ":shared-source" for row in rows)
