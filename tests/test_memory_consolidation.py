"""Use the original summary/episode algorithms and real SQLite/native events."""

import ast
import asyncio
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrbot_plugin_ovenheart.room_memory import RoomMemory
from tests.test_room_memory import SyntheticEmbedding
from tests.test_runtime import event

pytest_plugins = ["tests.test_participation"]


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
    p.context.get_provider_by_id = lambda key: (
        SyntheticEmbedding() if key == "semantic" else None
    )
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
        and state.graph_vectors.num_vectors == 2
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
        assert ast.dump(original) == ast.dump(native)
    for name, entry in manifest["files"].items():
        assert (
            hashlib.sha256((port / name).read_bytes()).hexdigest()
            == entry["ported_sha256"]
        )
