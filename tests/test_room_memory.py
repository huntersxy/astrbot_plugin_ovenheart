"""Exercise original A_memorix retrieval, graph and Faiss against native sources."""

import asyncio
import threading

import pytest

from astrbot_plugin_ovenheart.memory.room_memory import RoomMemory
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
    sender="alice",
):
    incoming = event(source_id, text=text, session=session, sender=sender)
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
    assert state.metadata.is_paragraph_ngram_ready(n=2)
    assert state.metadata.ngram_search_paragraphs(["ek", "ko", "o喜"], limit=5)
    assert state.metadata._conn.execute("PRAGMA cache_size").fetchone()[0] == -8192
    assert all(
        connection.execute("PRAGMA cache_size").fetchone()[0] == -8192
        for connection in state.metadata._connection_manager._connections.values()
    )


async def test_memory_scope_switch_shares_one_kernel_and_preserves_isolated_data(memories):
    original = await material(memories, source_id="same-id")
    incoming, scope, persona, messages = original
    isolated = await memories.room_memory.remember(incoming, scope, persona, note(), messages)
    memories.settings["memory_isolation"] = False
    shared = await memories.room_memory.remember(
        incoming, scope, persona, note(content="Neko与小猫有共同约定。"), messages
    )
    other, other_scope, other_persona, _ = await material(memories, source_id="same-id", session="456")
    storage = await memories.room_memory.storage_scope(scope)
    assert storage == await memories.room_memory.storage_scope(other_scope)
    found = await memories.room_memory.search(other, other_scope, other_persona, "Neko 小猫")
    assert any(row["id"] == shared["id"] for row in found)
    assert all(row["id"] != isolated["id"] for row in found)
    assert found[0]["metadata"]["evidence"][0]["source_id"] == scope + ":same-id"
    state = memories.room_memory.rooms[storage]
    with state.metadata.transaction():
        state.metadata._conn.executemany("INSERT INTO ovenheart_summary_processed(source_id,created) VALUES (?,1)", [
            (scope + ":same-id",), (other_scope + ":same-id",), (other_scope + ":stale",),
        ])
    status = await memories.room_memory.call(other_scope, "summary_status", [other_scope + ":same-id"], other_scope + ":")
    assert status["processed"] == {scope + ":same-id", other_scope + ":same-id"}
    from tests.test_runtime import api_call
    console = await api_call(memories.page_api.memory_panel.read, query=f"scope={other_scope}&view=records&kind=paragraph")
    assert any(row["id"] == shared["id"] for row in console["data"]["items"])
    await memories.store.call("touch_scope", "f" * 24, "another-room", "another-persona", "Another")
    assert await memories.room_memory.storage_scope("f" * 24) != storage
    memories.settings["memory_isolation"] = True
    assert not await memories.room_memory.search(other, other_scope, other_persona, "Neko 小猫")
    found = await memories.room_memory.search(incoming, scope, persona, "Neko 小猫")
    assert any(row["id"] == isolated["id"] for row in found)
    assert storage in memories.room_memory.rooms


async def test_first_empty_reads_do_not_start_memory_kernel_and_first_write_does(memories):
    incoming, scope, persona, messages = await material(memories)
    assert not await memories.room_memory.search(incoming, scope, persona, "猫咪")
    assert await memories.room_memory.person_context(incoming, scope, persona, "alice") == {
        "paragraphs": [], "relations": [], "episodes": [],
    }
    assert await memories.room_memory.call(scope, "midterm_records", "conversation", 4, event=incoming) == {
        "cutoff": 0, "records": [],
    }
    assert not memories.room_memory.rooms
    assert not (memories.room_memory.directory / scope).exists()
    saved = await memories.room_memory.remember(incoming, scope, persona, note(), messages)
    result = await memories.room_memory.search(incoming, scope, persona, "猫咪")
    assert any(row["id"] == saved["id"] for row in result)


async def test_search_skips_empty_episode_index_but_recalls_new_episode(memories, monkeypatch):
    incoming, scope, persona, messages = await material(memories)
    saved = await memories.room_memory.remember(incoming, scope, persona, note(), messages)
    state = memories.room_memory.rooms[scope]
    query = state.kernel.episode_retriever.query
    calls = []

    async def count_query(**kwargs):
        calls.append(kwargs)
        return await query(**kwargs)

    monkeypatch.setattr(state.kernel.episode_retriever, "query", count_query)
    assert await memories.room_memory.search(incoming, scope, persona, "小猫")
    assert not calls
    episode = state.metadata.upsert_episode({
        "source": "chat_summary:" + scope,
        "title": "小猫之约",
        "summary": "Neko周末去看小猫。",
        "evidence_ids": [saved["id"]],
        "participants": ["alice"],
    })
    state.metadata.bind_episode_paragraphs(episode["episode_id"], [saved["id"]])
    result = await memories.room_memory.search(incoming, scope, persona, "小猫")
    assert calls and any(row["id"] == episode["episode_id"] for row in result)


async def test_room_limit_closes_idle_runtime_and_reopens_persisted_memory(memories):
    memories.settings.update(
        memory_runtime_max_rooms=1,
        memory_summary_enabled=False,
        person_memory_enabled=False,
    )
    first, first_scope, first_persona, first_messages = await material(
        memories, "first-room", session="123"
    )
    saved = await memories.room_memory.remember(
        first, first_scope, first_persona, note(), first_messages
    )
    second, second_scope, second_persona, second_messages = await material(
        memories, "second-room", session="456"
    )
    await memories.room_memory.remember(
        second, second_scope, second_persona, note(), second_messages
    )
    assert list(memories.room_memory.rooms) == [second_scope]
    recovered = await memories.room_memory.search(first, first_scope, first_persona, "Neko 猫咪")
    assert any(row["id"] == saved["id"] for row in recovered)
    assert list(memories.room_memory.rooms) == [first_scope]


async def test_cold_room_warmup_does_not_block_existing_room_recall(
    memories, monkeypatch
):
    from astrbot_plugin_ovenheart.vendor.maibot.amemorix.retrieval.sparse_bm25 import (
        SparseBM25Index,
    )

    incoming, scope, persona, messages = await material(memories)
    await memories.room_memory.remember(incoming, scope, persona, note(), messages)
    other, other_scope, _, _ = await material(memories, session="cold-room")
    started, release = threading.Event(), threading.Event()
    warmup = SparseBM25Index.warmup

    def slow_warmup(index):
        started.set()
        release.wait(3)
        return warmup(index)

    monkeypatch.setattr(SparseBM25Index, "warmup", slow_warmup)
    cold = asyncio.create_task(
        memories.room_memory.call(other_scope, "summary_status", event=other)
    )
    assert await asyncio.to_thread(started.wait, 3)
    try:
        assert await asyncio.wait_for(
            memories.room_memory.search(incoming, scope, persona, "猫咪"), 1
        )
        assert not release.is_set()
    finally:
        release.set()
        await asyncio.wait_for(cold, 5)


@pytest.mark.parametrize("close_during_warmup", [False, True])
async def test_request_cancellation_keeps_one_warmup_until_reuse_or_close(
    memories, monkeypatch, close_during_warmup
):
    from astrbot_plugin_ovenheart.vendor.maibot.amemorix.retrieval.sparse_bm25 import (
        SparseBM25Index,
    )

    incoming, scope, _, _ = await material(memories)
    started, release = threading.Event(), threading.Event()
    warmup = SparseBM25Index.warmup
    warmed = []

    def slow_warmup(index):
        warmed.append(index)
        started.set()
        release.wait(5)
        return warmup(index)

    monkeypatch.setattr(SparseBM25Index, "warmup", slow_warmup)
    query = asyncio.create_task(
        memories.room_memory.call(scope, "summary_status", event=incoming)
    )
    closing = None
    try:
        assert await asyncio.to_thread(started.wait, 3)
        state = memories.room_memory.rooms[scope]
        query.cancel()
        done, _ = await asyncio.wait({query}, timeout=0.5)
        assert query in done, "reply cancellation still waits for cold initialization"
        with pytest.raises(asyncio.CancelledError):
            await query
        assert state.initializing is not None and not state.initializing.done()
        if close_during_warmup:
            closing = asyncio.create_task(memories.room_memory.close())
            await asyncio.sleep(0.03)
            assert not closing.done()
        release.set()
        if closing is not None:
            await asyncio.wait_for(closing, 5)
            assert state.kernel is None and state.initializing is None
            assert not memories.room_memory.rooms and memories.room_memory.loop is None
        else:
            await asyncio.wait_for(
                memories.room_memory.call(scope, "summary_status", event=incoming), 5
            )
            assert len(warmed) == 1
            assert state.kernel is not None and state.kernel.is_runtime_ready()
    finally:
        release.set()
        await asyncio.gather(query, return_exceptions=True)
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)


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


async def test_host_provider_reload_rebinds_client_without_losing_memories(memories):
    class ReloadableEmbedding(SyntheticEmbedding):
        closed = False
        calls = 0
        provider_config = {
            "type": "openai_embedding",
            "embedding_api_base": "https://first.invalid/v1",
        }

        async def get_embeddings(self, texts):
            assert not self.closed, "closed provider was reused"
            self.calls += 1
            return await super().get_embeddings(texts)

    providers = [ReloadableEmbedding()]
    memories.settings["embedding_provider_id"] = "semantic"
    memories.context.get_provider_by_id = lambda name: providers[0]
    incoming, scope, persona, messages = await material(memories)
    saved = await memories.room_memory.remember(
        incoming, scope, persona, note(), messages
    )
    state = memories.room_memory.rooms[scope]
    original = state.kernel
    fingerprint = state.embedding.get_embedding_configuration_key()
    providers[0].closed = True
    providers[0] = ReloadableEmbedding()
    result = await memories.room_memory.search(incoming, scope, persona, "GATTO")
    assert any(row["id"] == saved["id"] for row in result)
    assert state.kernel is not original
    assert state.embedding.provider is providers[0]
    assert state.embedding.get_embedding_configuration_key() == fingerprint
    assert providers[0].calls > 0
    providers[0].provider_config = providers[0].provider_config | {
        "embedding_api_base": "https://second.invalid/v1"
    }
    await memories.room_memory.search(incoming, scope, persona, "Neko")
    assert state.embedding.get_embedding_configuration_key() != fingerprint
    assert state.metadata.get_paragraph(saved["id"])


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


async def test_ready_vectors_with_slow_provider_still_return_sparse_memory(memories):
    class IntermittentEmbedding(SyntheticEmbedding):
        slow = False
        cancelled = False

        async def get_embeddings(self, texts):
            if not self.slow:
                return await super().get_embeddings(texts)
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

    embedding = IntermittentEmbedding()
    memories.settings["embedding_provider_id"] = "semantic"
    memories.context.get_provider_by_id = lambda _: embedding
    incoming, scope, persona, messages = await material(memories)
    saved = await memories.room_memory.remember(
        incoming, scope, persona, note(), messages
    )
    state = memories.room_memory.rooms[scope]
    space = state.vector_space
    embedding.slow = True
    found = await asyncio.wait_for(
        memories.room_memory.search(incoming, scope, persona, "周末去看小猫"), 3.5
    )
    assert any(row["id"] == saved["id"] for row in found)
    assert embedding.cancelled
    assert state.vector_space == space and state.paragraph_vectors.num_vectors == 1


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


async def test_openai_embedding_config_model_identifies_vector_space():
    from types import SimpleNamespace

    from astrbot_plugin_ovenheart.memory.memory_embedding import EmbeddingAPIAdapter

    class Provider(SyntheticEmbedding):
        provider_config = {"embedding_model": "first-model"}

        def get_model(self):
            return ""

    provider = Provider()
    context = SimpleNamespace(get_provider_by_id=lambda _: provider)
    before = EmbeddingAPIAdapter(context, "native", asyncio.get_running_loop())
    provider.provider_config = {"embedding_model": "second-model"}
    after = EmbeddingAPIAdapter(context, "native", asyncio.get_running_loop())
    assert before.fingerprint["model_name"] == "first-model"
    assert before.get_embedding_fingerprint(dimension=4)["model"] == "first-model"
    assert before.get_embedding_configuration_key() != after.get_embedding_configuration_key()




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
