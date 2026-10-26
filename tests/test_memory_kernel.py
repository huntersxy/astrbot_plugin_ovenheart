"""The original SDK kernel must actually own initialization, services and data."""

import ast
import asyncio
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrbot_plugin_ovenheart.memory_embedding import EmbeddingAPIAdapter
from astrbot_plugin_ovenheart.native_memory_kernel import NativeMemoryKernel
from astrbot_plugin_ovenheart.vendor.maibot.amemorix import host
from astrbot_plugin_ovenheart.vendor.maibot.amemorix.runtime.models import (
    KernelSearchRequest,
)
from astrbot_plugin_ovenheart.vendor.maibot.amemorix.runtime.sdk_memory_kernel import (
    SDKMemoryKernel,
)
from astrbot_plugin_ovenheart.vendor.maibot.amemorix.sdk_host import bind_native
from tests.test_room_memory import SyntheticEmbedding


@pytest.fixture
async def native_kernel(tmp_path):
    provider = SyntheticEmbedding()
    adapter = EmbeddingAPIAdapter(
        SimpleNamespace(get_provider_by_id=lambda name: provider),
        "semantic",
        asyncio.get_running_loop(),
    )
    await adapter.encode("SDK provider dimension")
    with host.bind(None), bind_native(data_root=tmp_path, embedding=adapter):
        kernel = NativeMemoryKernel(
            plugin_root=tmp_path,
            config={
                "storage": {"data_dir": str(tmp_path / "memory")},
                "embedding": {"dimension": adapter.dimension},
                "image_memory": {"enabled": False},
                "retrieval": {
                    "vector_pools": {"mode": "dual"},
                    "relation_vectorization": {"enabled": True},
                },
            },
        )
        await kernel.initialize()
        try:
            yield kernel
        finally:
            await kernel.shutdown()


async def seed(kernel):
    written = await kernel.ingest_text(
        external_id="native-note",
        source_type="chat_summary",
        chat_id="native-room",
        text="Neko周六去书店看小猫。",
        entities=["Neko", "书店", "小猫"],
        relations=[
            {
                "subject": "Neko",
                "predicate": "喜欢",
                "object": "小猫",
                "confidence": 1.0,
            }
        ],
        metadata={
            "evidence": [
                {"source_id": "native-note", "sender": "alice", "quote": "去书店看小猫"}
            ]
        },
    )
    # Initialization starts a real model-space synchronization task. Await
    # that live writer before requesting another explicit rebuild in a test.
    async with kernel._vector_rebuild_lock:
        pass
    return written


async def test_original_kernel_dual_retrieval_and_chat_scope(native_kernel):
    kernel = native_kernel
    written = await seed(kernel)
    assert kernel._current_embedding_fingerprint() is not None
    rebuilt = await kernel.memory_runtime_admin(action="rebuild_all_vectors")
    assert rebuilt["success"], rebuilt
    assert kernel._dual_vector_pools_enabled()
    assert kernel.paragraph_vector_store.num_vectors == 1
    assert kernel.graph_vector_store.num_vectors >= 4
    found = await kernel.search_memory(
        KernelSearchRequest(query="GATTO", chat_id="native-room", limit=5)
    )
    assert any(item["hash"] == written["stored_ids"][0] for item in found["hits"])
    denied = await kernel.search_memory(
        KernelSearchRequest(query="GATTO", chat_id="other-room", limit=5)
    )
    assert not denied["hits"]


async def test_dense_scope_links_keep_retrieval_within_sql_work_budget(native_kernel):
    kernel = native_kernel
    with kernel.metadata_store.transaction() as db:
        db.executemany(
            "INSERT INTO paragraphs(hash,content,source,metadata) VALUES (?,?,?,?)",
            [
                (f"p{i}", "scope fixture", "chat_summary:dense-room", "{}")
                for i in range(1800)
            ]
            + [("foreign-p", "foreign fixture", "chat_summary:foreign-room", "{}")],
        )
        db.executemany(
            "INSERT INTO entities(hash,name,is_deleted) VALUES (?,?,?)",
            [(f"e{i}", f"entity-{i}", int(i == 2113)) for i in range(2115)],
        )
        db.executemany(
            "INSERT INTO paragraph_entities(paragraph_hash,entity_hash) VALUES (?,?)",
            [
                (f"p{i}", f"e{(i * 13 + j) % 2114}")
                for i in range(1800)
                for j in range(13)
            ]
            + [("foreign-p", "e2114")],
        )
    await kernel.shutdown()
    await kernel.initialize()
    steps = 0

    def work_budget():
        nonlocal steps
        steps += 1000
        return steps > 1_000_000

    db = kernel.metadata_store._conn
    db.set_progress_handler(work_budget, 1000)
    try:
        scope = kernel._get_search_hit_service()._resolve_retrieval_scope("dense-room")
    finally:
        db.set_progress_handler(None, 0)
    assert scope.paragraph_ids == {f"p{i}" for i in range(1800)}
    assert scope.entity_ids == {f"e{i}" for i in range(2113)}


async def test_original_kernel_delete_restore_and_relation_graph(native_kernel):
    kernel = native_kernel
    written = await seed(kernel)
    paragraph = written["stored_ids"][0]
    preview = await kernel.memory_delete_admin(
        action="preview", mode="paragraph", selector={"hashes": [paragraph]}
    )
    assert preview["success"]
    deleted = await kernel.memory_delete_admin(
        action="execute",
        mode="paragraph",
        selector={"hashes": [paragraph]},
        requested_by="native-test",
    )
    assert deleted["success"]
    assert not (
        await kernel.search_memory(
            KernelSearchRequest(query="小猫", chat_id="native-room")
        )
    )["hits"]
    restored = await kernel.memory_delete_admin(
        action="restore",
        operation_id=deleted["operation_id"],
        requested_by="native-test",
    )
    assert restored["success"]
    assert (
        await kernel.search_memory(
            KernelSearchRequest(query="小猫", chat_id="native-room")
        )
    )["hits"]


async def test_original_kernel_initializes_ingests_and_shuts_down(tmp_path):
    adapter = EmbeddingAPIAdapter(
        SimpleNamespace(get_provider_by_id=lambda name: None),
        "",
        asyncio.get_running_loop(),
    )
    with host.bind(None), bind_native(data_root=tmp_path, embedding=adapter):
        kernel = SDKMemoryKernel(
            plugin_root=tmp_path,
            config={
                "storage": {"data_dir": str(tmp_path / "memory")},
                "image_memory": {"enabled": False},
            },
        )
        try:
            await kernel.initialize()
            assert kernel.is_runtime_ready()
            assert kernel._runtime_writer_lock.held
            assert "memory_maintenance" in kernel._background_tasks
            written = await kernel.ingest_text(
                external_id="native-source",
                source_type="chat_summary",
                text="大家周六约好去书店。",
                chat_id="native-room",
                entities=["书店"],
            )
            assert written["stored_ids"]
            assert kernel.metadata_store.count_paragraphs() == 1
        finally:
            await kernel.shutdown()
        assert not kernel.is_runtime_ready()
        assert not kernel._runtime_writer_lock.held


async def test_original_correction_plan_execution_search_filter_and_rollback(
    native_kernel,
):
    kernel = native_kernel
    written = await seed(kernel)
    old = written["stored_ids"][0]
    calls = []

    async def generate(request):
        calls.append(request.request_type)
        return json.dumps(
            {
                "confidence": 1.0,
                "risk_level": "low",
                "reason": "明确更正出行日期",
                "operations": [
                    {"action": "mark_superseded", "hash": old},
                    {
                        "action": "ingest_text",
                        "source_type": "chat_summary",
                        "chat_id": "native-room",
                        "text": "Neko周日去书店看小猫。",
                    },
                ],
            },
            ensure_ascii=False,
        )

    with host.bind(None, generate):
        planned = await kernel.memory_correction_admin(
            action="preview",
            scope="memory",
            request_text="把周六改为周日",
            targets=[{"type": "paragraph", "id": old}],
        )
        assert planned["success"], planned
        plan_id = planned["plan_id"]
        assert kernel.metadata_store.count_paragraphs() == 1
        executed = await kernel.memory_correction_admin(
            action="execute",
            plan_id=plan_id,
            confirmed=True,
            requested_by="native-test",
        )
        assert executed["success"], executed
    assert calls == ["A_Memorix.fuzzy_modify_plan"]
    found = await kernel.search_memory(
        KernelSearchRequest(query="小猫", chat_id="native-room", limit=10)
    )
    assert any("周日" in hit["content"] for hit in found["hits"])
    assert all(hit["hash"] != old for hit in found["hits"])
    rolled_back = await kernel.memory_correction_admin(
        action="rollback", plan_id=plan_id, requested_by="native-test"
    )
    assert rolled_back["success"], rolled_back
    restored = await kernel.search_memory(
        KernelSearchRequest(query="小猫", chat_id="native-room", limit=10)
    )
    assert any(hit["hash"] == old for hit in restored["hits"])


def test_full_kernel_sources_match_the_pinned_algorithms():
    root = Path(__file__).resolve().parents[1]
    port = root / "vendor/maibot/amemorix"
    manifest = json.loads((port / "source_manifest.json").read_text(encoding="utf-8"))
    for name, entry in manifest["files"].items():
        assert (
            hashlib.sha256((port / name).read_bytes()).hexdigest()
            == entry["ported_sha256"]
        )
    upstream = root.parent / "MaiBot"
    if not (upstream / ".git").exists():
        pytest.skip("pinned upstream checkout unavailable")

    class Algorithms(ast.NodeTransformer):
        def visit_ImportFrom(self, node):
            return None

        def visit_Import(self, node):
            return None

        def visit_FunctionDef(self, node):
            if (
                self.filename == "utils/person_profile_service.py"
                and node.name == "_build_retriever"
            ):
                return None
            return self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node):
            if (
                self.filename == "utils/summary_importer.py"
                and node.name == "_ensure_runtime_self_check"
            ):
                return None
            return self.generic_visit(node)

    for name, entry in manifest["files"].items():
        original = subprocess.check_output(
            ["git", "show", manifest["commit"] + ":" + entry["source"]], cwd=upstream
        )
        assert hashlib.sha256(original).hexdigest() == entry["upstream_sha256"]
        if not name.endswith(".py"):
            assert (port / name).read_bytes() == original
            continue
        normalizer = Algorithms()
        normalizer.filename = name
        left = ast.dump(normalizer.visit(ast.parse(original)))
        right = ast.dump(normalizer.visit(ast.parse((port / name).read_bytes())))
        matches = left == right
        assert matches, "ported algorithm changed: " + name


async def test_original_relation_weakening_and_maintenance_persist_graph(native_kernel):
    kernel = native_kernel
    written = await seed(kernel)
    relation = written["stored_ids"][1]
    assert kernel.graph_store.num_edges == 1
    for _ in range(10):
        result = await kernel.memory_v5_admin(
            action="weaken", target=relation, strength=1.0, requested_by="native-test"
        )
        assert result["success"], result
    assert kernel.metadata_store.get_relation(relation)["is_inactive"]
    await kernel._run_memory_maintenance_cycle(interval_hours=1)
    assert kernel._last_maintenance_at is not None
    assert kernel.graph_store.num_edges == 0
    assert not kernel.metadata_store.get_paragraph(written["stored_ids"][0])[
        "is_deleted"
    ]
