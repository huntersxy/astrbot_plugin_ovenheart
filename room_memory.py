"""Native scope/source adapter for A_memorix's graph and dual-pool retriever."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
import weakref
from collections import OrderedDict
from contextlib import asynccontextmanager
from types import SimpleNamespace

import jsonschema
from astrbot.api import logger

from .participation import event_source
from .settings import learning_blocked

MEMORY_SCHEMA = {
    "type": "object",
    "required": ["content", "evidence", "relations"],
    "additionalProperties": False,
    "properties": {
        "content": {
            "type": "string",
            "minLength": 1,
            "maxLength": 2400,
            "pattern": r"\S",
        },
        "evidence": {
            "type": "array",
            "minItems": 1,
            "maxItems": 12,
            "items": {
                "type": "object",
                "required": ["msg_id", "quote"],
                "additionalProperties": False,
                "properties": {
                    "msg_id": {"type": "string", "pattern": "^[0-9]+$"},
                    "quote": {"type": "string", "minLength": 1, "maxLength": 500},
                },
            },
        },
        "relations": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "required": ["subject", "predicate", "object"],
                "additionalProperties": False,
                "properties": {
                    key: {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 120,
                        "pattern": r"\S",
                    }
                    for key in ("subject", "predicate", "object")
                },
            },
        },
    },
}

REQUEST_EVIDENCE = MEMORY_SCHEMA["properties"]["evidence"] | {"maxItems": 1}
CHANGE_SCHEMAS = {
    "correct_memory": MEMORY_SCHEMA
    | {
        "required": ["memory_id", "content", "evidence", "relations"],
        "properties": MEMORY_SCHEMA["properties"]
        | {
            "memory_id": {"type": "string", "minLength": 1, "maxLength": 80},
            "evidence": REQUEST_EVIDENCE,
        },
    },
    "forget_memory": {
        "type": "object",
        "required": ["memory_id", "evidence"],
        "additionalProperties": False,
        "properties": {
            "memory_id": {"type": "string", "minLength": 1, "maxLength": 80},
            "evidence": REQUEST_EVIDENCE,
        },
    },
    "undo_memory_change": {
        "type": "object",
        "required": ["evidence"],
        "additionalProperties": False,
        "properties": {
            "change_id": {"type": "string", "maxLength": 80},
            "evidence": REQUEST_EVIDENCE,
        },
    },
}


def native_provenance(evidence, previous=()):
    proofs = list(
        {
            (entry["source_id"], entry["sender"], entry["quote"]): entry
            for entry in [*previous, *evidence]
        }.values()
    )
    if len(proofs) > 64:
        proofs = [proofs[0], *proofs[-63:]]
    return {
        "source_type": "chat_summary",
        "evidence": proofs,
        "participants": sorted({e["sender"] for e in proofs}),
    }


class NativeMemoryIntegration:
    feedback_correction_paragraph_hard_filter_enabled = True

    def __init__(self, plugin):
        self.plugin = plugin

    @property
    def feedback_correction_enabled(self):
        settings = self.plugin.settings
        return all(
            settings[k]
            for k in (
                "enabled",
                "apply_enabled",
                "learning_enabled",
                "memory_enabled",
                "native_memory_enabled",
                "memory_feedback_enabled",
            )
        )


class RoomState:
    """Bind one native scope to one active SDK-owned storage/runtime."""

    def __init__(self, path, lock):
        self.path = path
        self.lock = lock
        self.kernel = None
        self.metadata = self.graph = None
        self.embedding = None
        self.embedding_key = None
        self.event_ref = lambda: None
        self.identity = None
        self.session = SimpleNamespace(
            session_id=path.name, group_id="", user_id="", self_id=""
        )
        self.operation = None
        self.revision = None
        self.foreground = set()
        self.active_foreground = set()
        self.foreground_lock = asyncio.Lock()
        self.foreground_ready = asyncio.Event()
        self.model_waiting = False

    async def initialize(self):
        from .native_memory_kernel import NativeMemoryKernel

        key = self.embedding.get_embedding_configuration_key()
        if self.kernel is not None and self.embedding_key == key:
            return
        if self.kernel is not None:
            await self.kernel.shutdown()
        if self.embedding.available:
            try:
                await asyncio.wait_for(self.embedding._detect_dimension(), 1.5)
            except Exception:
                pass  # The SDK can still start with real metadata/sparse fallback.
        kernel = NativeMemoryKernel(
            plugin_root=self.path,
            config={
                "storage": {"data_dir": str(self.path)},
                "embedding": {"dimension": self.embedding.dimension or 1024},
                "image_memory": {"enabled": False},
                "retrieval": {
                    "vector_pools": {"mode": "dual"},
                    "relation_vectorization": {"enabled": True},
                },
            },
        )
        kernel._runtime_writer_lock.acquire()
        try:
            self._backup_legacy_storage()
            await kernel.initialize()
        except BaseException:
            kernel._runtime_writer_lock.release()
            raise
        self.kernel = kernel
        self.embedding_key = key
        self.metadata = kernel.metadata_store
        self.graph = kernel.graph_store
        self.metadata._conn.executescript("""
            CREATE TABLE IF NOT EXISTS ovenheart_summary_processed (
                source_id TEXT PRIMARY KEY, created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ovenheart_summary_runtime (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), attempted REAL NOT NULL
            );
            INSERT OR IGNORE INTO ovenheart_summary_runtime VALUES (1,0);
        """)
        # These databases already belong to exactly one native room/persona.
        # Earlier development notes used a generic source; retain all evidence
        # while bringing that source into the SDK's actual chat filter.
        with self.metadata.transaction():
            self.metadata._conn.execute(
                "UPDATE paragraphs SET source=? WHERE source='chat_summary:astrbot'",
                ("chat_summary:" + self.path.name,),
            )
        (self.path / ".native_sdk_owner").write_text(
            "SDKMemoryKernel\n", encoding="utf-8"
        )

    def _backup_legacy_storage(self):
        import shutil
        import sqlite3

        source = self.path / "metadata" / "metadata.db"
        backup = self.path / "native-kernel-backup" / "metadata.db"
        if (
            not source.exists()
            or backup.exists()
            or (self.path / ".native_sdk_owner").exists()
        ):
            return
        backup.parent.mkdir(parents=True, exist_ok=True)
        original = sqlite3.connect(source)
        snapshot = sqlite3.connect(backup)
        try:
            original.backup(snapshot)
        finally:
            snapshot.close()
            original.close()
        graph = self.path / "graph"
        if graph.exists():
            shutil.copytree(graph, backup.parent / "graph", dirs_exist_ok=True)

    @property
    def paragraph_vectors(self):
        if self.kernel is None or self.kernel._is_embedding_degraded():
            return None
        return self.kernel.paragraph_vector_store

    @property
    def graph_vectors(self):
        if self.kernel is None or self.kernel._is_embedding_degraded():
            return None
        return self.kernel.graph_vector_store

    @property
    def vector_space(self):
        space = self.kernel._active_vector_space_id
        return "spaces/" + space if space else ""

    def _retriever(self):
        return self.kernel.retriever

    async def _prepare_vectors(self, text):
        if not self.embedding.available:
            return False
        await self.kernel._recover_embedding_once(sample_text=text)
        return (
            self.kernel._dual_vector_pools_enabled()
            and not self.kernel._is_embedding_degraded()
        )

    async def _backfill(self):
        await self.kernel._run_paragraph_backfill_once(limit=32, trigger="native_chat")

    async def search(self, query, top_k, feedback_source=""):
        if not self.metadata.count_paragraphs():
            return []
        try:
            if await asyncio.wait_for(self._prepare_vectors(query), 1.5):
                await asyncio.wait_for(self._backfill(), 1.5)
        except Exception as exc:
            logger.warning(
                "记忆向量通道暂不可用，使用原版稀疏/图检索 (%s)", type(exc).__name__
            )
        from .vendor.maibot.amemorix.runtime.models import KernelSearchRequest

        response = await self.kernel.search_memory(
            KernelSearchRequest(query=query, chat_id=self.path.name, limit=top_k)
        )
        found = response["hits"]
        if feedback_source and found:
            query_id = hashlib.sha256(
                json.dumps(
                    [self.path.name, feedback_source, query, [h["hash"] for h in found]]
                ).encode()
            ).hexdigest()
            await self.kernel.enqueue_feedback_task(
                query_tool_id="native:" + query_id,
                session_id=self.path.name,
                structured_content=response
                | {"query": query, "chat_id": self.path.name},
            )
        result = []
        for item in found:
            row = (
                self.metadata.get_paragraph(item["hash"])
                if item["type"] == "paragraph"
                else self.metadata.get_relation(item["hash"])
            ) or {}
            source = row.get("metadata") or {}
            result.append(
                {
                    "id": item["hash"],
                    "content": item["content"][:800],
                    "score": item.get("score", 0),
                    "type": item["type"],
                    "backend": "amemorix",
                    "metadata": {
                        "source_type": source.get("source_type", ""),
                        "participants": source.get("participants", []),
                        "evidence": [
                            {**entry, "quote": entry["quote"][:160]}
                            for entry in source.get("evidence", [])[:4]
                        ],
                        "time_meta": item.get("metadata", {}).get("time_meta", {}),
                    },
                }
            )
        episodes = await self.episodes(query, min(1, top_k))
        if episodes:
            episode = episodes[0]
            paragraphs = episode.get("paragraphs", [])
            proofs = [
                e
                for p in paragraphs
                for e in (p.get("metadata") or {}).get("evidence", [])
            ]
            result.insert(
                0,
                {
                    "id": episode["episode_id"],
                    "content": (episode["title"] + ": " + episode["summary"])[:800],
                    "score": episode.get("score", 0),
                    "type": "episode",
                    "backend": "amemorix",
                    "metadata": {
                        "source_type": "episode",
                        "paragraphs": [
                            {"id": p["hash"], "content": p["content"][:800]}
                            for p in paragraphs[:3]
                        ],
                        "participants": episode.get("participants", []),
                        "evidence": [
                            {**e, "quote": e["quote"][:160]} for e in proofs[:4]
                        ],
                        "time_meta": {
                            k: episode.get(k)
                            for k in (
                                "event_time_start",
                                "event_time_end",
                                "time_granularity",
                            )
                        },
                    },
                },
            )
        return result[:top_k]

    async def write(self, content, evidence, relations):
        from .vendor.maibot.amemorix.utils.hash import compute_hash, normalize_text

        paragraph = compute_hash(normalize_text(content))
        previous = self.metadata.get_paragraph(paragraph) or {}
        metadata = native_provenance(
            evidence, (previous.get("metadata") or {}).get("evidence", [])
        )
        try:
            await asyncio.wait_for(self._prepare_vectors(content), 1.5)
        except Exception:
            pass  # The original SDK preserves metadata and queues vector recovery.
        await self.kernel.ingest_text(
            external_id="note:"
            + compute_hash(
                json.dumps(
                    [paragraph, metadata["evidence"], relations],
                    sort_keys=True,
                    ensure_ascii=False,
                )
            ),
            source_type="chat_summary",
            chat_id=self.path.name,
            text=content,
            participants=metadata["participants"],
            metadata=metadata,
            time_start=min(e["created"] for e in evidence),
            time_end=max(e["created"] for e in evidence),
            entities=list(
                dict.fromkeys(
                    name for r in relations for name in (r["subject"], r["object"])
                )
            ),
            relations=[r | {"metadata": metadata} for r in relations],
        )
        return {
            "id": paragraph,
            "stored": True,
            "vector_written": self.paragraph_vectors is not None
            and paragraph in self.paragraph_vectors,
            "backfill": self.metadata.get_paragraph_vector_backfill_status_counts(),
        }

    async def change(self, action, payload, evidence):
        from .native_memory_kernel import correction_evidence
        from .vendor.maibot.amemorix.utils.hash import normalize_text

        actor = "ovenheart:" + evidence[0]["sender"]
        reason = json.dumps(
            {"native_action": action, "evidence": evidence}, ensure_ascii=False
        )
        if self.embedding.available:
            try:
                await asyncio.wait_for(self._prepare_vectors(evidence[0]["quote"]), 1.5)
            except Exception:
                pass  # Keep the SDK's authoritative metadata/deferred-index state.
        if action == "undo_memory_change":
            return await self._undo_change(payload.get("change_id", ""), actor, reason)
        memory_id = payload["memory_id"]
        row = self.metadata.get_paragraph(memory_id)
        if row is None or row["source"] != "chat_summary:" + self.path.name:
            return {
                "success": False,
                "error": "只能修改当前会话、人格中本插件学习的通用段落。",
            }
        # Use the SDK's existing deleted/inactive/protected-target checks.
        self.kernel._correction_admin_service._collect_selected_fuzzy_modify_candidates(
            [{"type": "paragraph", "id": memory_id}]
        )
        if action == "forget_memory":
            result = await self.kernel.memory_delete_admin(
                action="execute",
                mode="paragraph",
                selector={"hashes": [memory_id]},
                requested_by=actor,
                reason=reason,
            )
            return {
                "success": result["success"],
                "change_id": result.get("operation_id", ""),
                "memory_id": memory_id,
                "error": result.get("error", ""),
            }
        if normalize_text(payload["content"]) == normalize_text(row["content"]):
            return {"success": False, "error": "新内容与原记录相同，无需更正。"}
        plan = self.metadata.create_fuzzy_modify_plan(
            request_text=evidence[0]["quote"],
            scope="memory",
            target_chat_id=self.path.name,
            requested_by=actor,
            reason=reason,
            confidence=1.0,
            preview={
                "native_action": action,
                "memory_id": memory_id,
                "evidence": evidence,
            },
            plan={
                "chat_id": self.path.name,
                "operations": [
                    {
                        "action": "mark_superseded",
                        "target_type": "paragraph",
                        "hash": memory_id,
                    },
                    {
                        "action": "ingest_text",
                        "source_type": "chat_summary",
                        "chat_id": self.path.name,
                        "text": payload["content"],
                        "relations": payload["relations"],
                    },
                ],
            },
        )
        token = correction_evidence.set((plan["plan_id"], self.path.name, evidence))
        try:
            result = await self.kernel.memory_correction_admin(
                action="execute",
                plan_id=plan["plan_id"],
                confirmed=True,
                requested_by=actor,
                reason=reason,
            )
        finally:
            correction_evidence.reset(token)
        execution = result.get("execution") or {}
        return {
            "success": result["success"],
            "change_id": plan["plan_id"],
            "memory_id": memory_id,
            "new_ids": [
                i
                for i in execution.get("stored_ids", [])
                if self.metadata.get_paragraph(i)
            ],
            "error": execution.get("error", result.get("error", "")),
        }

    async def _undo_change(self, change_id, actor, reason):
        # The original journal also contains SDK-internal cleanup/rollback
        # deletes. Only actions issued by this native author are undo targets.
        changes = []
        plans = (
            [self.metadata.get_fuzzy_modify_plan(change_id)]
            if change_id
            else self.metadata.list_fuzzy_modify_plans(
                limit=50, statuses=["executed"], scope="memory"
            )
        )
        for plan in plans:
            if (
                plan
                and plan["requested_by"] == actor
                and plan["preview"].get("native_action") == "correct_memory"
                and plan["status"] == "executed"
            ):
                changes.append(
                    (
                        plan["created_at"],
                        "correct",
                        plan["plan_id"],
                        plan["preview"]["memory_id"],
                    )
                )
        operations = (
            [self.metadata.get_delete_operation(change_id)]
            if change_id
            else self.metadata.list_delete_operations(limit=50, mode="paragraph")
        )
        for operation in operations:
            if (
                not operation
                or operation["requested_by"] != actor
                or operation["status"]
                not in {"pending_cleanup", "completed", "restore_pending"}
            ):
                continue
            try:
                provenance = json.loads(operation.get("reason") or "")
            except ValueError:
                continue
            if provenance.get("native_action") == "forget_memory":
                changes.append(
                    (
                        operation["created_at"],
                        "forget",
                        operation["operation_id"],
                        operation["selector"]["hashes"][0],
                    )
                )
        if not changes:
            return {
                "success": False,
                "error": "没有找到你在当前会话、人格中可撤销的更正或遗忘。",
            }
        _, kind, identifier, memory_id = max(changes)
        if kind == "correct":
            result = await self.kernel.memory_correction_admin(
                action="rollback", plan_id=identifier, requested_by=actor, reason=reason
            )
        else:
            result = await self.kernel.memory_delete_admin(
                action="restore",
                operation_id=identifier,
                requested_by=actor,
                reason=reason,
            )
        row = self.metadata.get_paragraph(memory_id)
        status = (
            self.metadata.get_delete_operation(identifier)["status"]
            if kind == "forget"
            else result.get("plan", {}).get("status", "")
        )
        metadata_restored = bool(result["success"]) or (
            kind == "forget"
            and status == "restore_pending"
            and row is not None
            and not row["is_deleted"]
        )
        return {
            "success": result["success"],
            "change_id": identifier,
            "status": status,
            "restored_ids": [memory_id] if metadata_restored else [],
            "metadata_restored": metadata_restored,
            "pending_derived_indexes": metadata_restored and not result["success"],
            "message": "文字记忆已恢复，派生索引仍在恢复中，尚未全部完成。"
            if metadata_restored and not result["success"]
            else "",
            "error": result.get("error", ""),
        }

    async def summary_status(self, retained=None):
        if retained is not None:
            # Progress contains IDs only for the bounded native material store.
            with self.metadata.transaction():
                if retained:
                    self.metadata._conn.execute(
                        "DELETE FROM ovenheart_summary_processed WHERE source_id NOT IN ("
                        + ",".join("?" for _ in retained)
                        + ")",
                        retained,
                    )
                else:
                    self.metadata._conn.execute(
                        "DELETE FROM ovenheart_summary_processed"
                    )
        summary = self.metadata.get_episode_source_rebuild_summary()["counts"]
        return {
            "attempted": self.metadata._conn.execute(
                "SELECT attempted FROM ovenheart_summary_runtime WHERE singleton=1"
            ).fetchone()[0],
            "processed": {
                r[0]
                for r in self.metadata._conn.execute(
                    "SELECT source_id FROM ovenheart_summary_processed"
                )
            },
            "episode_pending": summary["pending"] > 0
            or summary["failed"] > 0
            or summary["running"] > 0,
        }

    async def summary_attempt(self):
        self.metadata._conn.execute(
            "UPDATE ovenheart_summary_runtime SET attempted=? WHERE singleton=1",
            (time.time(),),
        )
        self.metadata._conn.commit()

    async def consolidate(self, scope, messages, generate, bot_name, model_name):
        from .memory_summary import NativeSummaryImporter
        from .vendor.maibot.amemorix import host
        from .vendor.maibot.amemorix.summary_host import bind_chat
        from .vendor.maibot.amemorix.utils.relation_write_service import (
            RelationWriteService,
        )

        metadata = {"source_type": "chat_summary"}

        async def generate_bound(request):
            return await generate(request, metadata)

        async def preflight():
            return self.metadata._conn is not None, ""

        result = {}
        with host.bind(None, generate_bound, model_name), bind_chat(messages, bot_name):
            if messages:
                try:
                    await asyncio.wait_for(
                        self._prepare_vectors(messages[-1]["text"]), 1.5
                    )
                except Exception:
                    pass
                metadata["external_id"] = (
                    "summary:"
                    + hashlib.sha256(
                        json.dumps([scope, [m["source_id"] for m in messages]]).encode()
                    ).hexdigest()
                )
                importer = NativeSummaryImporter(
                    self.paragraph_vectors,
                    self.graph,
                    self.metadata,
                    self.embedding,
                    {
                        "native_preflight": preflight,
                        "native_state": self,
                        "plugin_instance": self.kernel._runtime_facade,
                        "graph_vector_store": self.graph_vectors,
                        "relation_write_service": RelationWriteService(
                            self.metadata, self.graph, None, None
                        ),
                        "retrieval": {"vector_pools": {"mode": "dual"}},
                        "summarization": {"model_name": ["auto"]},
                    },
                )
                self.kernel.summary_importer = importer
                imported = await self.kernel.summarize_chat_stream(
                    chat_id=scope,
                    context_length=len(messages),
                    include_personality=False,
                    time_end=max(m["created"] for m in messages),
                    metadata=metadata,
                )
                if not imported["success"]:
                    raise RuntimeError(
                        "原版摘要导入未完成：" + imported["detail"][:200]
                    )
                with self.metadata.transaction():
                    self.metadata._conn.executemany(
                        "INSERT OR IGNORE INTO ovenheart_summary_processed VALUES (?,?)",
                        [(m["source_id"], m["created"]) for m in messages],
                    )
                result.update(
                    summary_id=next(iter(imported.get("stored_ids", [])), ""),
                    skipped=bool(imported.get("skipped_ids")),
                )
            episodes = (
                await self.kernel._ingest_service.process_episode_source_rebuild_batch(
                    limit=1,
                    max_wait_seconds=0,
                    lease_seconds=180,
                )
            )
            result.update(
                episodes=episodes["episode_count"], episode_failures=episodes["failed"]
            )
        return result

    async def episodes(self, query, top_k):
        from .vendor.maibot.amemorix.utils.episode_retrieval_service import (
            EpisodeRetrievalService,
        )

        service = EpisodeRetrievalService(
            metadata_store=self.metadata, retriever=self._retriever()
        )
        found = await service.query(query=query, top_k=top_k, include_paragraphs=True)
        return [
            row
            for row in found
            if not self.metadata.is_episode_source_query_blocked(row.get("source", ""))
        ]

    async def close(self):
        if self.kernel is not None:
            await self.kernel.shutdown()
            self.kernel = None


class RoomMemory:
    def __init__(self, plugin):
        self.plugin = plugin
        self.rooms = OrderedDict()
        self.locks = {}
        self.directory = plugin.store.path.parent / "amemorix" / "rooms"
        self.loop = None
        self.thread = None
        self.closing = False

    def observe(self, event, scope):
        """Retain native authorization fields, without message/media/history data."""
        from astrbot.core.platform.astr_message_event import AstrMessageEvent
        from astrbot.core.platform.astrbot_message import (
            AstrBotMessage,
            Group,
            MessageMember,
        )

        state = self.rooms.get(scope)
        settings = self.plugin.settings
        if (
            state is None
            or event.get_sender_id() in settings["blocked_senders"]
            or (
                settings["target_senders"]
                and event.get_sender_id() not in settings["target_senders"]
            )
        ):
            return
        message = AstrBotMessage()
        message.type = event.get_message_type()
        message.self_id = event.get_self_id()
        message.sender = MessageMember(event.get_sender_id(), event.get_sender_name())
        message.group = (
            Group(event.get_group_id(), event.message_obj.group.group_name)
            if event.message_obj.group
            else None
        )
        message.message_id = ""
        message.message = []
        message.message_str = ""
        message.raw_message = None
        identity = AstrMessageEvent("", message, event.platform_meta, event.session_id)
        identity.unified_msg_origin = event.unified_msg_origin
        # Background work uses current room permissions, never an old one-off
        # admin wake's whitelist bypass. This object is not dispatched as input.
        state.identity = identity
        state.session.group_id = event.get_group_id()
        state.session.user_id = event.get_sender_id()
        state.session.self_id = event.get_self_id()

    async def execute(self, state, operation, args):
        """Run upstream async work off-host and acknowledge cancellation cleanup."""
        if self.loop is None:
            self.loop = asyncio.new_event_loop()
            self.thread = threading.Thread(
                target=self.loop.run_forever, name="ovenheart-memory", daemon=True
            )
            self.thread.start()
        host = asyncio.get_running_loop()
        result, started = host.create_future(), host.create_future()

        def finish(task):
            if task.cancelled():
                result.cancel()
            elif task.exception() is not None:
                result.set_exception(task.exception())
            else:
                result.set_result(task.result())

        async def run():
            from .vendor.maibot.amemorix import host as memory_host
            from .vendor.maibot.amemorix.sdk_host import bind_native
            from .vendor.maibot.amemorix.summary_host import bind_chat_reader

            async def native_generate(request):
                future = asyncio.run_coroutine_threadsafe(
                    self.generate(state, request), host
                )
                return await asyncio.wrap_future(future)

            def native_messages(**options):
                future = asyncio.run_coroutine_threadsafe(
                    self.messages(state, **options), host
                )
                try:
                    return future.result(timeout=10)
                except BaseException:
                    future.cancel()
                    raise

            with (
                bind_native(
                    data_root=state.path,
                    embedding=state.embedding,
                    session=state.session,
                    generate=native_generate,
                ),
                memory_host.bind(
                    None,
                    native_generate,
                    integration=NativeMemoryIntegration(self.plugin),
                ),
                bind_chat_reader(native_messages, lambda: state.session.self_id),
            ):
                if operation != "close":
                    await state.initialize()
                return await getattr(state, operation)(*args)

        def start():
            task = self.loop.create_task(run())
            task.add_done_callback(lambda done: host.call_soon_threadsafe(finish, done))
            host.call_soon_threadsafe(started.set_result, task)

        self.loop.call_soon_threadsafe(start)
        try:
            return await asyncio.shield(result)
        except asyncio.CancelledError:
            task = await asyncio.shield(started)
            self.loop.call_soon_threadsafe(task.cancel)
            await asyncio.gather(result, return_exceptions=True)
            raise

    async def generate(self, state, request):
        """Use current native permissions and accounting for SDK background models."""
        event = state.identity or state.event_ref()
        revision = self.plugin.settings_revision
        if event is None:
            raise ValueError("该记忆会话没有可用的原生消息来源。")
        scope, persona, _ = await self.plugin.resolve_scope(event)
        if scope != state.path.name or not await self.permitted(
            event, scope, persona, learning=True
        ):
            raise ValueError("当前记忆来源或学习权限已撤销。")
        provider = (
            self.plugin.settings["memory_summary_provider_id"]
            or self.plugin.settings["provider_id"]
            or await self.plugin.context.get_current_chat_provider_id(
                event.unified_msg_origin
            )
        )
        response = await self.plugin.auxiliary.generate(
            "memory_feedback"
            if "feedback" in request.request_type.lower()
            else "memory_episode"
            if "episode" in request.request_type.lower()
            else "memory_maintenance",
            chat_provider_id=provider,
            prompt=request.prompt,
            **(
                {"temperature": request.temperature}
                if request.temperature is not None
                else {}
            ),
            **(
                {"max_tokens": request.max_tokens}
                if request.max_tokens is not None
                else {}
            ),
        )
        if revision != self.plugin.settings_revision or not await self.permitted(
            event, scope, persona, learning=True
        ):
            raise ValueError("模型生成期间记忆来源或学习权限已改变。")
        return response.completion_text

    async def messages(
        self,
        state,
        *,
        chat_id,
        start_time,
        end_time,
        limit,
        limit_mode,
        filter_mai,
        filter_command,
    ):
        event = state.identity or state.event_ref()
        revision = self.plugin.settings_revision
        if event is None or chat_id != state.path.name:
            raise ValueError("后台记忆消息缺少真实会话身份或请求了其他会话。")
        scope, persona, _ = await self.plugin.resolve_scope(event)
        if scope != chat_id or not await self.permitted(
            event, scope, persona, learning=True
        ):
            raise ValueError("后台记忆消息来源或学习权限已撤销。")
        limit = min(1000, max(1, int(limit)))
        if self.plugin.conversation.active(event):
            window = self.plugin.participation.windows.get(scope)
            rows = (
                list(window["messages"])
                if window
                and await self.plugin.conversation.valid(event, scope, window["epoch"])
                else []
            )
        elif getattr(self.plugin.context, "message_history_manager", None) is not None:
            cid = (
                await self.plugin.context.conversation_manager.get_curr_conversation_id(
                    event.unified_msg_origin
                )
            )
            conversation = (
                await self.plugin.context.conversation_manager.get_conversation(
                    event.unified_msg_origin, cid
                )
                if cid
                else None
            )
            lower = max(
                start_time, float(conversation.created_at or 0) if conversation else 0
            )
            rows = await self.plugin.conversation.history(
                event, lower, end_time, limit=limit, limit_mode=limit_mode
            )
        else:
            rows = [
                {
                    "source": r["source_id"],
                    "sender_id": r["sender"],
                    "text": r["text"],
                    "time": r["created"],
                    "self": False,
                }
                for r in await self.plugin.store.call("room_memory_inputs", scope)
            ]
        settings = self.plugin.settings
        result = [
            {
                "source_id": r["source"],
                "sender": r["sender_id"],
                "text": r["text"],
                "created": r["time"],
                "self": r.get("self", False),
                "command": r["text"].lstrip().startswith("/"),
            }
            for r in rows
            if start_time <= r["time"] <= end_time
            and not (filter_mai and r.get("self"))
            and not (filter_command and r["text"].lstrip().startswith("/"))
            and (
                r.get("self")
                or r["sender_id"] not in settings["blocked_senders"]
                and (
                    not settings["target_senders"]
                    or r["sender_id"] in settings["target_senders"]
                )
            )
        ]
        if revision != self.plugin.settings_revision or not await self.permitted(
            event, scope, persona, learning=True
        ):
            raise ValueError("读取后台记忆消息期间权限已改变。")
        return result[-limit:] if limit_mode == "latest" else result[:limit]

    async def permitted(self, event, scope, persona, learning=False):
        s = self.plugin.settings
        if (
            not s["memory_enabled"]
            or not s["native_memory_enabled"]
            or not s["apply_enabled"]
            or not await self.plugin.permitted(event)
        ):
            return False
        if (await self.plugin.resolve_scope(event))[:2] != (scope, persona):
            return False
        state = await self.plugin.store.call(
            "guidance", scope, not s["session_isolation"]
        )
        profile = state.get("scope")
        if not profile or not profile["profile"]["apply_enabled"]:
            return False
        if learning and (
            not s["learning_enabled"]
            or not profile["profile"]["learning_enabled"]
            or learning_blocked(event.unified_msg_origin, s)
            or event.get_sender_id() in s["blocked_senders"]
            or s["target_senders"]
            and event.get_sender_id() not in s["target_senders"]
        ):
            return False
        return True

    @asynccontextmanager
    async def model_window(self, scope):
        """Lend the serialized importer while its native model is on the wire."""
        state = self.rooms[scope]
        state.model_waiting = True
        state.foreground_ready.set()
        try:
            yield
        finally:
            state.model_waiting = False
            state.foreground_ready.clear()
            if state.active_foreground:
                await asyncio.gather(
                    *tuple(state.active_foreground), return_exceptions=True
                )

    async def execute_foreground(self, state, operation, args, revision):
        async with state.foreground_lock:
            while state.operation == "consolidate" and not state.model_waiting:
                await state.foreground_ready.wait()
            if (
                self.closing
                or self.plugin.closing
                or revision != self.plugin.settings_revision
            ):
                raise ValueError("记忆配置已改变或正在关闭，取消排队中的操作。")
            task = asyncio.current_task()
            state.active_foreground.add(task)
            try:
                return await self.execute(state, operation, args)
            finally:
                state.active_foreground.discard(task)

    async def call(self, scope, operation, *args, event=None):
        from .memory_embedding import EmbeddingAPIAdapter

        revision = self.plugin.settings_revision
        if self.closing or self.plugin.closing:
            raise ValueError("记忆运行时正在关闭。")
        state = self.rooms.get(scope)
        if (
            state is not None
            and operation in ("search", "write", "change")
            and state.operation == "consolidate"
            and state.revision == revision
        ):
            # Foreground memory tools must finish before the planner replies.
            # Lend the worker at the importer's model boundary, retaining the
            # same authoritative stores and serializing foreground writes.
            task = asyncio.create_task(
                self.execute_foreground(state, operation, args, revision)
            )
            state.foreground.add(task)
            try:
                return await task
            finally:
                state.foreground.discard(task)
        lock = self.locks.setdefault(scope, asyncio.Lock())
        async with lock:
            if self.closing or self.plugin.closing:
                raise ValueError("记忆运行时正在关闭。")
            state = self.rooms.get(scope)
            if state is None:
                if len(self.rooms) >= 64:
                    candidate = next(
                        (
                            k
                            for k, v in self.rooms.items()
                            if not v.lock.locked() and not v.foreground
                        ),
                        None,
                    )
                    if candidate is None:
                        raise RuntimeError("记忆工作队列繁忙。")
                    await self.execute(self.rooms.pop(candidate), "close", ())
                    self.locks.pop(candidate, None)
                creating = asyncio.create_task(
                    asyncio.to_thread(RoomState, self.directory / scope, lock)
                )
                try:
                    state = await asyncio.shield(creating)
                except asyncio.CancelledError:
                    self.rooms[scope] = await creating
                    raise
                self.rooms[scope] = state
            self.rooms.move_to_end(scope)
            if state.foreground:
                await asyncio.gather(*tuple(state.foreground), return_exceptions=True)
            if (
                self.closing
                or self.plugin.closing
                or revision != self.plugin.settings_revision
            ):
                raise ValueError("记忆配置已改变，取消排队中的操作。")
            embedding = EmbeddingAPIAdapter(
                self.plugin.context,
                self.plugin.settings["embedding_provider_id"],
                asyncio.get_running_loop(),
            )
            if (
                state.embedding is None
                or state.embedding.get_embedding_configuration_key()
                != embedding.get_embedding_configuration_key()
            ):
                state.embedding = embedding
            state.revision = revision
            if event is not None:
                state.event_ref = weakref.ref(event)
                self.observe(event, scope)
            state.operation = operation
            if operation == "consolidate":
                state.foreground_ready.clear()
            try:
                return await self.execute(state, operation, args)
            finally:
                state.operation = None
                state.foreground_ready.set()

    async def search(self, event, scope, persona, query):
        revision = self.plugin.settings_revision
        if not query.strip() or not await self.permitted(event, scope, persona):
            return []
        result = await self.call(
            scope,
            "search",
            query[:2400],
            self.plugin.settings["memory_top_k"],
            event_source(event),
            event=event,
        )
        if revision != self.plugin.settings_revision or not await self.permitted(
            event, scope, persona
        ):
            return []
        return result

    async def context(self, event, scope, persona, query):
        if (
            not self.plugin.settings["native_memory_enabled"]
            or not self.plugin.settings["memory_enabled"]
        ):
            return []
        try:
            return await asyncio.wait_for(
                self.search(event, scope, persona, query),
                self.plugin.settings["retrieval_timeout"],
            )
        except Exception as exc:
            logger.warning(
                "本轮通用记忆暂不可用，保留原生回复与现有回忆 (%s)", type(exc).__name__
            )
            return []

    async def remember(self, event, scope, persona, payload, messages):
        jsonschema.validate(payload, MEMORY_SCHEMA)
        evidence = await self.verified_evidence(
            event, scope, persona, payload["evidence"], messages
        )
        return await self.call(
            scope,
            "write",
            payload["content"],
            evidence,
            payload["relations"],
            event=event,
        )

    async def change(self, event, scope, persona, action, payload, messages):
        jsonschema.validate(payload, CHANGE_SCHEMAS[action])
        evidence = await self.verified_evidence(
            event, scope, persona, payload["evidence"], messages
        )
        return await self.call(scope, "change", action, payload, evidence, event=event)

    async def verified_evidence(self, event, scope, persona, citations, messages):
        revision = self.plugin.settings_revision
        evidence = []
        for citation in citations:
            message = next(
                (
                    m
                    for m in messages
                    if str(m["sequence"]) == citation["msg_id"] and not m.get("self")
                ),
                None,
            )
            source = (
                self.plugin.participation.sources.get((scope, message["source"]))
                if message
                else None
            )
            if (
                source is None
                or source.get_sender_id() != message["sender_id"]
                or event_source(source) != message["source"]
                or source.unified_msg_origin != event.unified_msg_origin
                or not citation["quote"].strip()
                or citation["quote"] not in source.get_message_str()
                or not await self.permitted(source, scope, persona, learning=True)
            ):
                raise ValueError("记忆操作需要本轮真实作者的原文和学习权限。")
            evidence.append(
                {
                    "source_id": event_source(source),
                    "sender": source.get_sender_id(),
                    "quote": citation["quote"],
                    "created": source.created_at,
                }
            )
        if revision != self.plugin.settings_revision or not await self.permitted(
            event, scope, persona, learning=True
        ):
            raise ValueError("记忆来源、配置或权限已经改变。")
        return evidence

    async def close(self):
        self.closing = True
        for scope, lock in tuple(self.locks.items()):
            async with lock:
                state = self.rooms.pop(scope, None)
                if state is not None:
                    if state.foreground:
                        await asyncio.gather(
                            *tuple(state.foreground), return_exceptions=True
                        )
                    await self.execute(state, "close", ())
        self.rooms.clear()
        self.locks.clear()
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.loop.stop)
            await asyncio.to_thread(self.thread.join)
            self.loop.close()
            self.loop = self.thread = None
