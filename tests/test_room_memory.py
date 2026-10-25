"""Exercise original A_memorix retrieval, graph and Faiss against native sources."""

import asyncio

import pytest

from astrbot_plugin_ovenheart.room_memory import RoomMemory
from tests.test_runtime import event

pytest_plugins = ["tests.test_participation"]


class SyntheticEmbedding:
    provider_config = {"api_base": "http://embedding.invalid/v1"}

    def get_model(self):
        return "synthetic-semantic"

    async def get_embeddings(self, texts):
        # Only the external model transport is replaced; all ranking/indexing
        # and persistence are the original implementation.
        return [
            [1.0, 0.0, 0.0, 0.0]
            if any(w in t for w in ("GATTO", "小猫", "猫咪", "Neko"))
            else [0.0, 1.0, 0.0, 0.0]
            for t in texts
        ]


@pytest.fixture
async def memories(participant):
    participant.settings.update(native_memory_enabled=True, embedding_provider_id="")
    participant.context.get_provider_by_id = lambda name: None
    return participant


async def material(
    plugin,
    source_id="memory-source",
    text="Neko喜欢猫咪，周末去看小猫。",
    session="123",
):
    incoming = event(source_id, text=text, session=session)
    await plugin.capture(incoming)
    scope, persona, _ = await plugin.resolve_scope(incoming)
    plugin.participation.sources[scope, source_id] = incoming
    message = {
        "sequence": 1,
        "source": source_id,
        "sender_id": incoming.get_sender_id(),
    }
    return incoming, scope, persona, [message]


def note(quote="Neko喜欢猫咪", **patch):
    return {
        "content": "Neko喜欢猫咪，周末打算去看小猫。",
        "evidence": [{"msg_id": "1", "quote": quote}],
        "relations": [{"subject": "Neko", "predicate": "喜欢", "object": "猫咪"}],
        **patch,
    }


async def test_original_sparse_graph_search_retains_native_evidence(memories):
    incoming, scope, persona, messages = await material(memories)
    saved = await memories.room_memory.remember(
        incoming, scope, persona, note(), messages
    )
    assert saved["stored"] and not saved["vector_written"]
    result = await memories.room_memory.search(incoming, scope, persona, "Neko 猫咪")
    assert result and "猫咪" in result[0]["content"]
    assert result[0]["metadata"]["evidence"][0]["source_id"] == "memory-source"
    assert result[0]["metadata"]["participants"] == ["alice"]
    state = memories.room_memory.rooms[scope]
    assert state.graph.num_nodes >= 2


async def test_original_dual_vector_pools_find_nonlexical_query_and_reload(memories):
    memories.settings["embedding_provider_id"] = "semantic"
    memories.context.get_provider_by_id = lambda name: SyntheticEmbedding()
    incoming, scope, persona, messages = await material(memories)
    saved = await memories.room_memory.remember(
        incoming, scope, persona, note(), messages
    )
    assert saved["vector_written"]
    state = memories.room_memory.rooms[scope]
    assert state.paragraph_vectors.num_vectors == 1
    assert state.graph_vectors.num_vectors >= 3
    result = await memories.room_memory.search(incoming, scope, persona, "GATTO")
    assert any(r["id"] == saved["id"] for r in result)
    await memories.room_memory.close()
    memories.room_memory = RoomMemory(memories)
    reloaded = await memories.room_memory.search(incoming, scope, persona, "GATTO")
    assert any(r["id"] == saved["id"] for r in reloaded)


async def test_scope_isolation_and_missing_source_rejection(memories):
    incoming, scope, persona, messages = await material(memories)
    await memories.room_memory.remember(incoming, scope, persona, note(), messages)
    with pytest.raises(ValueError, match="原文"):
        await memories.room_memory.remember(
            incoming, scope, persona, note(quote="伪造的话"), messages
        )
    other, other_scope, other_persona, _ = await material(
        memories, "other", "其他群的话题", session="456"
    )
    assert not await memories.room_memory.search(
        other, other_scope, other_persona, "Neko 猫咪"
    )
    assert not await memories.room_memory.search(other, scope, persona, "Neko 猫咪")


async def test_embedding_failure_preserves_metadata_and_sparse_retrieval(memories):
    class BrokenEmbedding(SyntheticEmbedding):
        async def get_embeddings(self, texts):
            raise RuntimeError("synthetic provider failure")

    memories.settings["embedding_provider_id"] = "broken"
    memories.context.get_provider_by_id = lambda name: BrokenEmbedding()
    incoming, scope, persona, messages = await material(memories)
    saved = await memories.room_memory.remember(
        incoming, scope, persona, note(), messages
    )
    assert saved["stored"] and not saved["vector_written"]
    result = await memories.room_memory.search(incoming, scope, persona, "Neko 猫咪")
    assert result


async def test_slow_embedding_falls_back_and_native_cancellation_releases_worker(
    memories,
):
    started, cancelled = asyncio.Event(), asyncio.Event()

    class SlowEmbedding(SyntheticEmbedding):
        async def get_embeddings(self, texts):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    incoming, scope, persona, messages = await material(memories)
    await memories.room_memory.remember(incoming, scope, persona, note(), messages)
    memories.settings["embedding_provider_id"] = "slow"
    memories.context.get_provider_by_id = lambda name: SlowEmbedding()
    query = asyncio.create_task(
        memories.room_memory.search(incoming, scope, persona, "Neko")
    )
    await asyncio.wait_for(started.wait(), 3)
    query.cancel()
    with pytest.raises(asyncio.CancelledError):
        await query
    await asyncio.wait_for(cancelled.wait(), 3)
    memories.settings["embedding_provider_id"] = ""
    assert await memories.room_memory.search(incoming, scope, persona, "Neko")


async def test_learning_revocation_prevents_write_but_allows_existing_recall(memories):
    incoming, scope, persona, messages = await material(memories)
    await memories.room_memory.remember(incoming, scope, persona, note(), messages)
    memories.settings["learning_enabled"] = False
    with pytest.raises(ValueError, match="学习权限"):
        await memories.room_memory.remember(incoming, scope, persona, note(), messages)
    assert await memories.room_memory.search(incoming, scope, persona, "Neko")


async def test_embedding_model_change_rebuilds_from_metadata_without_crossing_spaces(
    memories,
):
    memories.settings["embedding_provider_id"] = "semantic"
    memories.context.get_provider_by_id = lambda name: SyntheticEmbedding()
    incoming, scope, persona, messages = await material(memories)
    saved = await memories.room_memory.remember(
        incoming, scope, persona, note(), messages
    )
    state = memories.room_memory.rooms[scope]
    original_space = state.vector_space

    class OtherModel(SyntheticEmbedding):
        def get_model(self):
            return "other-semantic-model"

    memories.context.get_provider_by_id = lambda name: OtherModel()
    result = await memories.room_memory.search(incoming, scope, persona, "GATTO")
    assert any(r["id"] == saved["id"] for r in result)
    assert state.vector_space != original_space
    assert (state.path / "vectors" / original_space).is_dir()


async def test_sdk_takes_ownership_and_keeps_legacy_evidence_graph_and_vector_files(
    memories,
):
    import json
    import sqlite3

    import numpy as np

    from astrbot_plugin_ovenheart.native_memory_kernel import NativeMemoryKernel
    from astrbot_plugin_ovenheart.vendor.maibot.amemorix.storage import (
        GraphStore,
        MetadataStore,
        VectorStore,
    )

    incoming, scope, persona, _ = await material(memories)
    directory = memories.room_memory.directory / scope
    old = MetadataStore(directory / "metadata")
    old.connect()
    proofs = [
        {"source_id": "memory-source", "sender": "alice", "quote": "Neko喜欢猫咪"}
    ]
    paragraph = old.add_paragraph(
        "Neko喜欢猫咪。",
        source="chat_summary:astrbot",
        metadata={"evidence": proofs, "participants": ["alice"]},
    )
    entity = old.add_entity("猫咪", source_paragraph=paragraph)
    graph = GraphStore(data_dir=directory / "graph")
    graph.add_nodes([entity])
    graph.save()
    old.close()
    legacy = directory / "vectors" / "legacy-model" / "paragraphs"
    vectors = VectorStore(4, data_dir=legacy)
    vectors.add(np.asarray([[1.0, 0.0, 0.0, 0.0]], dtype=np.float32), [paragraph])
    vectors.save()
    previous = {
        p.relative_to(legacy): p.read_bytes() for p in legacy.rglob("*") if p.is_file()
    }
    assert previous
    found = await memories.room_memory.search(incoming, scope, persona, "猫咪")
    assert any(
        row["id"] == paragraph and row["metadata"]["evidence"] == proofs
        for row in found
    )
    state = memories.room_memory.rooms[scope]
    kernel = state.kernel
    assert isinstance(kernel, NativeMemoryKernel)
    assert kernel.metadata_store is state.metadata and kernel.graph_store is state.graph
    assert kernel._runtime_writer_lock.held and state.graph.num_nodes == 1
    backup = directory / "native-kernel-backup"
    with sqlite3.connect(backup / "metadata.db") as snapshot:
        source, metadata = snapshot.execute(
            "SELECT source,metadata FROM paragraphs WHERE hash=?", (paragraph,)
        ).fetchone()
    assert (
        source == "chat_summary:astrbot" and json.loads(metadata)["evidence"] == proofs
    )
    assert (backup / "graph").is_dir()
    assert {
        p.relative_to(legacy): p.read_bytes() for p in legacy.rglob("*") if p.is_file()
    } == previous
    await memories.room_memory.close()
    assert not kernel._runtime_writer_lock.held


async def test_concurrent_first_writes_share_one_original_graph(memories):
    incoming, scope, persona, messages = await material(
        memories, text="Neko喜欢猫咪，也喜欢读书。"
    )
    first = note(content="Neko喜欢猫咪。")
    second = note(
        quote="也喜欢读书",
        content="Neko喜欢读书。",
        relations=[{"subject": "Neko", "predicate": "喜欢", "object": "读书"}],
    )
    saved = await asyncio.gather(
        memories.room_memory.remember(incoming, scope, persona, first, messages),
        memories.room_memory.remember(incoming, scope, persona, second, messages),
    )
    assert all(r["stored"] for r in saved)
    assert memories.room_memory.rooms[scope].graph.num_nodes == 3


async def test_invalid_relation_is_rejected_before_any_metadata_write(memories):
    import jsonschema

    incoming, scope, persona, messages = await material(memories)
    invalid = note(
        relations=[{"subject": "Neko", "predicate": "   ", "object": "猫咪"}]
    )
    with pytest.raises(jsonschema.ValidationError):
        await memories.room_memory.remember(incoming, scope, persona, invalid, messages)
    assert scope not in memories.room_memory.rooms


async def test_repeated_note_keeps_original_and_new_source_proofs(memories):
    incoming, scope, persona, messages = await material(memories)
    first = await memories.room_memory.remember(
        incoming, scope, persona, note(), messages
    )
    again, _, _, next_messages = await material(memories, source_id="source-again")
    second = await memories.room_memory.remember(
        again, scope, persona, note(), next_messages
    )
    assert second["id"] == first["id"]
    found = await memories.room_memory.search(again, scope, persona, "Neko 猫咪")
    proof_ids = {e["source_id"] for e in found[0]["metadata"]["evidence"]}
    assert proof_ids == {"memory-source", "source-again"}


def test_retrieval_and_vector_graph_algorithms_are_the_pinned_source():
    import ast
    import hashlib
    import json
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    upstream = root.parent / "MaiBot"
    if not (upstream / ".git").exists():
        pytest.skip("pinned upstream checkout unavailable")
    port = root / "vendor/maibot/amemorix"
    manifest = json.loads((port / "source_manifest.json").read_text(encoding="utf-8"))
    for name, entry in manifest["files"].items():
        assert (
            hashlib.sha256((port / name).read_bytes()).hexdigest()
            == entry["ported_sha256"]
        )
    for name, cls in (
        ("retrieval/dual_path.py", "DualPathRetriever"),
        ("storage/vector_store.py", "VectorStore"),
        ("storage/graph_store.py", "GraphStore"),
    ):
        entry = manifest["files"][name]
        original = subprocess.check_output(
            ["git", "show", manifest["commit"] + ":" + entry["source"]], cwd=upstream
        )
        assert hashlib.sha256(original).hexdigest() == entry["upstream_sha256"]

        def body(blob, class_name):
            return ast.dump(
                next(
                    n
                    for n in ast.parse(blob).body
                    if isinstance(n, ast.ClassDef) and n.name == class_name
                )
            )

        assert body(original, cls) == body((port / name).read_bytes(), cls)
