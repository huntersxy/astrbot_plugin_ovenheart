"""Native scope/source adapter for A_memorix's graph and dual-pool retriever."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
import time
import weakref
from collections import OrderedDict
from contextlib import asynccontextmanager, contextmanager, nullcontext
from contextvars import ContextVar
from functools import partial
from types import SimpleNamespace

import jsonschema
from astrbot.api import logger

from ..chat.native_context import conversation_metadata
from ..chat.participation import event_source
from ..message_source import own_message_text
from ..runtime_diagnostics import await_path
from ..settings import SHARED_SCOPE, learning_blocked

_memory_origin = ContextVar("ovenheart_memory_origin", default=None)

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
        "image_evidence": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "required": ["msg_id", "image_index"],
                "additionalProperties": False,
                "properties": {
                    "msg_id": {"type": "string", "minLength": 1},
                    "image_index": {"type": "integer", "minimum": 0, "maximum": 3},
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
        "properties": {key: value for key, value in MEMORY_SCHEMA["properties"].items() if key != "image_evidence"}
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


class MemoryIdentity:
    """Permission/session data for background work, never a received event."""

    def __init__(self, event, message):
        self.message_obj = message
        self.platform_meta = event.platform_meta
        self.unified_msg_origin = event.unified_msg_origin
        # A previous admin wake must not grant a permanent whitelist bypass.
        self.role = "member"

    def get_message_type(self):
        return self.message_obj.type

    def get_platform_name(self):
        return self.platform_meta.name

    def get_platform_id(self):
        return self.platform_meta.id

    def get_group_id(self):
        return self.message_obj.group_id

    def get_self_id(self):
        return self.message_obj.self_id

    def get_sender_id(self):
        return self.message_obj.sender.user_id

    def get_sender_name(self):
        return self.message_obj.sender.nickname

    def is_private_chat(self):
        from astrbot.api.platform import MessageType

        return self.get_message_type() == MessageType.FRIEND_MESSAGE

    def get_message_str(self):
        return ""

    def get_messages(self):
        return []


class RoomState:
    """One persona's SDK runtime with a separate identity for each invocation."""

    def __init__(self, path, lock):
        self.path = path
        self.lock = lock
        self.kernel = None
        self.metadata = self.graph = None
        self.embedding = None
        self.image_embedding = None
        self.image_enabled = False
        self.embedding_key = None
        self.initializing = None
        self.initializing_key = None
        self.identities = {}
        self._session = SimpleNamespace(
            session_id=path.name, group_id="", user_id="", self_id=""
        )
        self.operation = None
        self.revision = None
        self.foreground = set()
        self.active_foreground = set()
        self.foreground_lock = asyncio.Lock()
        self.foreground_ready = asyncio.Event()
        self.model_waiting = False
        self.image_recovered = False

    @property
    def scope(self):
        current = _memory_origin.get()
        return (
            current.scope
            if current is not None and current.storage == self.path.name
            else self.path.name
        )

    @property
    def shared(self):
        current = _memory_origin.get()
        return bool(current and current.storage == self.path.name and current.shared)

    @property
    def session(self):
        return (
            self.identities[self.scope].session
            if self.scope in self.identities
            else self._session
        )

    @property
    def identity(self):
        return self.identities[self.scope].identity if self.scope in self.identities else None

    def event_ref(self):
        return self.identities[self.scope].event_ref() if self.scope in self.identities else None

    @contextmanager
    def bind(self, scope, shared):
        token = _memory_origin.set(
            SimpleNamespace(storage=self.path.name, scope=scope, shared=shared)
        )
        try:
            yield
        finally:
            _memory_origin.reset(token)

    async def initialize(self):
        key = (
            self.embedding.get_embedding_configuration_key(),
            id(self.embedding.provider),
            self.image_enabled,
            self.image_embedding.key,
            id(self.image_embedding.provider) if self.image_enabled else None,
        )
        if self.initializing is not None and self.initializing_key != key:
            await self._stop_initializing()
        if self.kernel is not None and self.embedding_key == key:
            return
        if self.initializing is None:
            self.initializing_key = key
            self.initializing = asyncio.create_task(
                self._initialize(
                    key, self.embedding, self.image_embedding, self.image_enabled
                )
            )
            self.initializing.add_done_callback(self._initialization_finished)
        # A reply deadline cancels its query, not the room's reusable warmup.
        await asyncio.shield(self.initializing)

    def _initialization_finished(self, task):
        if self.initializing is task:
            self.initializing = self.initializing_key = None
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.warning(f"记忆后台预热未完成 ({type(error).__name__})")

    async def _stop_initializing(self):
        task = self.initializing
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _initialize(self, key, embedding, image_embedding, image_enabled):
        from .native_memory_kernel import NativeMemoryKernel

        if self.kernel is not None:
            previous = self.kernel
            self.kernel = None
            self.embedding_key = None
            await previous.shutdown()
        if embedding.available:
            try:
                await asyncio.wait_for(embedding._detect_dimension(), 1.5)
            except Exception:
                pass  # The SDK can still start with real metadata/sparse fallback.
        kernel = NativeMemoryKernel(
            plugin_root=self.path,
            config={
                "storage": {"data_dir": str(self.path)},
                "embedding": {"dimension": embedding.dimension or 1024},
                "image_memory": {
                    "enabled": image_enabled,
                    "recover_on_initialize": False,
                    "preprocess_version": "gif_first_frame_v1",
                },
                "retrieval": {
                    "vector_pools": {"mode": "dual"},
                    "relation_vectorization": {"enabled": True},
                },
            },
        )
        kernel.feedback_sources = self.identities
        kernel._runtime_writer_lock.acquire()
        try:
            await kernel.initialize()
            if kernel.image_memory_runtime is not None:
                from .image_memory import (
                    NativeImageAssetStore,
                    image_job,
                    publish_image,
                )

                kernel.image_memory_runtime.asset_store = NativeImageAssetStore(
                    self.path / "images/assets",
                    max_bytes=10 * 1024 * 1024,
                    max_pixels=40_000_000,
                )
                kernel.image_memory_runtime._publish_asset_only = partial(
                    publish_image, kernel.image_memory_runtime
                )
                process_jobs = kernel.image_memory_runtime.process_jobs_once

                async def native_image_jobs():
                    future = asyncio.run_coroutine_threadsafe(
                        image_embedding.allowed(True, chat_ids=list(self.identities)), image_embedding.loop
                    )
                    if not await asyncio.wrap_future(future):
                        return {"claimed": 0, "processed": 0, "status": "paused"}
                    token = image_job.set(True)
                    try:
                        return await process_jobs()
                    finally:
                        image_job.reset(token)

                kernel.image_memory_runtime.process_jobs_once = native_image_jobs
        except BaseException:
            try:
                await kernel.shutdown()
            finally:
                kernel._runtime_writer_lock.release()
            raise
        self.kernel = kernel
        self.embedding_key = key
        self.metadata = kernel.metadata_store
        self.graph = kernel.graph_store
        from .persona_store import PROGRESS_SCHEMA

        self.metadata._conn.executescript(PROGRESS_SCHEMA)
        if self.metadata._conn.execute("SELECT 1 FROM sqlite_master WHERE name='ovenheart_summary_processed'").fetchone():
            with self.metadata.transaction() as db:
                db.execute("INSERT OR IGNORE INTO ovenheart_summary_progress SELECT ?,source_id,created FROM ovenheart_summary_processed WHERE NOT EXISTS(SELECT 1 FROM ovenheart_summary_attempts WHERE scope=?)", (self.scope,self.scope))
                db.execute("INSERT OR IGNORE INTO ovenheart_summary_attempts SELECT ?,attempted FROM ovenheart_summary_runtime WHERE singleton=1", (self.scope,))

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

    async def search(
        self, query, top_k, feedback_source="", feedback_scope="", defer_feedback=False
    ):
        from .memory_visibility import scoped_metadata

        if not self.metadata.count_paragraphs():
            return []
        try:
            if await self._prepare_vectors(query) and not defer_feedback:
                await asyncio.wait_for(self._backfill(), 1.5)
        except Exception as exc:
            logger.warning(
                "记忆向量通道暂不可用，使用原版稀疏/图检索 (%s)", type(exc).__name__
            )
        from ..vendor.maibot.amemorix.runtime.models import KernelSearchRequest

        has_episodes = self.metadata._conn.execute(
            "SELECT EXISTS(SELECT 1 FROM episodes)"
        ).fetchone()[0]
        # One foreground episode uses the native service's 20 * 4 candidates.
        response, evidence = await self.kernel.search_with_evidence(
            KernelSearchRequest(query=query, chat_id="" if self.shared else self.scope, limit=top_k),
            evidence_limit=80 if has_episodes else 0,
        )
        found = response["hits"]
        if feedback_source and found and not defer_feedback:
            await self.queue_feedback(query, feedback_source, feedback_scope, response)
        result = []
        for item in found:
            row = (
                self.metadata.get_paragraph(item["hash"])
                if item["type"] == "paragraph"
                else self.metadata.get_relation(item["hash"])
            ) or {}
            source = scoped_metadata(row.get("metadata") or {}, None if self.shared else self.scope)
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
        # Project episodes from this query's candidates instead of searching
        # the same paragraph/relation stores again.
        episodes = (
            await self.episodes(query, min(1, top_k), evidence=evidence)
            if has_episodes
            else []
        )
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
        return (
            {"results": result[:top_k], "feedback": response}
            if defer_feedback
            else result[:top_k]
        )

    async def queue_feedback(self, query, feedback_source, feedback_scope, response):
        query_id = hashlib.sha256(
            json.dumps(
                [
                    self.scope,
                    feedback_source,
                    query,
                    [h["hash"] for h in response["hits"]],
                ]
            ).encode()
        ).hexdigest()
        await self.kernel.enqueue_feedback_task(
            query_tool_id="native:" + query_id,
            session_id=self.scope,
            structured_content=response
            | {
                "query": query,
                "chat_id": "" if self.shared else self.scope,
                "source_scope": feedback_scope,
            },
        )

    async def write(self, content, evidence, relations, image_evidence=()):
        from ..vendor.maibot.amemorix.utils.hash import compute_hash, normalize_text
        from .memory_visibility import add_scope

        paragraph = compute_hash(normalize_text(content))
        previous = self.metadata.get_paragraph(paragraph) or {}
        metadata = native_provenance(
            evidence, (previous.get("metadata") or {}).get("evidence", [])
        )
        metadata = add_scope(metadata, self.scope, previous.get("metadata"))
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
            chat_id=self.scope,
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
        for image in image_evidence:
            await self.kernel.image_memory(
                action="link",
                occurrence_id=image["occurrence_id"],
                target_type="paragraph",
                target_id=paragraph,
                link_kind="user_statement",
                evidence={"native_image": image, "statements": metadata["evidence"]},
            )
        return {
            "id": paragraph,
            "stored": True,
            "vector_written": self.paragraph_vectors is not None
            and paragraph in self.paragraph_vectors,
            "backfill": self.metadata.get_paragraph_vector_backfill_status_counts(),
            "images_linked": len(image_evidence),
        }

    async def person_context(self, sender):
        from .memory_visibility import scoped_metadata

        source = "chat_summary:" + self.scope
        hashes = [
            r[0]
            for r in self.metadata._conn.execute(
                """
            SELECT p.hash FROM paragraphs p
            WHERE p.is_deleted=0 AND (? OR p.source=?)
              AND (p.expires_at IS NULL OR p.expires_at>?)
              AND EXISTS (SELECT 1 FROM json_each(p.metadata,'$.evidence') e
                          WHERE json_extract(e.value,'$.sender')=?)
            ORDER BY p.created_at DESC LIMIT 24
        """,
                (self.shared, source, time.time(), sender),
            )
        ]
        rows = self.metadata.get_paragraphs_by_hashes(hashes)
        hits = [
            {
                "hash": h,
                "type": "paragraph",
                "content": rows[h]["content"][:800],
                "score": 0.0,
                "source": source,
                "metadata": scoped_metadata(rows[h]["metadata"], None if self.shared else self.scope),
            }
            for h in hashes
            if h in rows
        ]
        # These are real recent authored records, not nickname-based ownership.
        # Keep the same effective-version and feedback filters as SDK retrieval.
        hits = self.kernel._get_search_hit_service()._filter_user_visible_hits(hits)[:4]
        relations = {}
        for hit in hits:
            for relation in self.metadata.get_paragraph_relations(hit["hash"]):
                if sender in (relation["subject"], relation["object"]):
                    relations[relation["hash"]] = {
                        **relation,
                        "type": "relation",
                        "content": f"{relation['subject']} {relation['predicate']} {relation['object']}",
                    }
        edges = self.kernel._get_search_hit_service()._filter_user_visible_hits(
            list(relations.values())
        )
        episodes = []
        if hits and not self.metadata.is_episode_source_query_blocked(source):
            candidates = await self.kernel.episode_retriever.query(
                query="", person=sender, source=None if self.shared else source, top_k=4, include_paragraphs=True
            )
            selected = {h["hash"] for h in hits}
            episodes = [
                {
                    "hash": e["episode_id"],
                    "type": "episode",
                    "content": (e["title"] + ": " + e["summary"])[:800],
                    "score": e.get("score", 0),
                    "source": source,
                    "metadata": {
                        "source_type": "chat_summary",
                        "participants": e.get("participants", []),
                        "paragraph_ids": [p["hash"] for p in e["paragraphs"]],
                    },
                }
                for e in candidates
                if any(p["hash"] in selected for p in e["paragraphs"])
            ]
        return {"paragraphs": hits, "relations": edges, "episodes": episodes}

    async def midterm_records(self, cid, limit):
        from ..vendor.maibot.amemorix.utils.async_work import run_sync_work

        return await run_sync_work(self._midterm_records, cid, limit)

    def _midterm_records(self, cid, limit):
        source = "chat_summary:" + self.scope
        cutoff = self.metadata._conn.execute(
            "SELECT COALESCE(MAX(json_extract(metadata,'$.midterm.end')), 0) "
            "FROM paragraphs WHERE source=? AND json_extract(metadata,'$.midterm.cid')=?",
            (source, cid),
        ).fetchone()[0]
        rows = self.metadata._conn.execute(
            "SELECT hash FROM paragraphs WHERE source=? "
            "AND json_extract(metadata,'$.midterm.cid')=? ORDER BY created_at DESC LIMIT ?",
            (source, cid, limit),
        ).fetchall()
        found = self.metadata.get_paragraphs_by_hashes(
            [r["hash"] for r in rows]
        )
        hits = self.kernel._get_search_hit_service()._filter_user_visible_hits(
            [
                {
                    "hash": r["hash"],
                    "type": "paragraph",
                    "source": r["source"],
                    "content": r["content"],
                    "metadata": r["metadata"],
                }
                for r in found.values()
            ]
        )
        return {"cutoff": cutoff, "records": hits}

    async def midterm_store(self, payload, evidence):
        saved = await self.write(payload["summary"], evidence, [])
        self.metadata.update_paragraph_metadata(saved["id"], {"midterm": payload})
        return saved

    async def image_ingest(
        self, image_bytes, source, message_id, index, created, text, own=False
    ):
        return await self.kernel.image_memory(
            action="ingest",
            image_bytes=image_bytes,
            source_kind="bot" if own else "chat",
            external_ref=f"{self.scope}:{source}:{index}",
            scope_type="chat",
            chat_id=self.scope,
            message_id=message_id,
            component_path=str(index),
            occurred_at=created,
            user_statement=text,
        )

    async def image_description(self, occurrence_id, description, sender):
        return await self.kernel.image_memory(
            action="observe",
            occurrence_id=occurrence_id,
            text=description["description"],
            source_kind="model_description",
            confirm_status="inferred",
            evidence={"is_emoji": description["is_emoji"], "native_sender": sender},
        )

    async def image_content_description(self, content_hash, description):
        return await self.kernel.image_memory_runtime.apply_model_description(
            content_hash=content_hash, text=description
        )

    async def image_search(self, payload, source, index, current_ref=None):
        result = await self.kernel.image_memory(
            action="search",
            image_bytes=payload,
            chat_ids=None if self.shared else [self.scope],
            current_external_ref=current_ref or f"{self.scope}:{source}:{index}",
            session_id=self.scope,
        )
        return {
            key: result[key]
            for key in ("success", "status", "error", "hits")
            if key in result
        }

    async def image_candidates(self, query=""):
        from .emoji_retrieval import rank_candidates

        rows = self.metadata._conn.execute(
            """
            SELECT o.asset_id, v.text AS caption FROM image_occurrences o
            JOIN image_observations v ON v.occurrence_id=o.occurrence_id
            JOIN image_assets a ON a.asset_id=o.asset_id
            WHERE (? OR o.chat_id=?) AND o.status='active' AND a.status='active' AND v.superseded_by=''
              AND json_extract(v.evidence_json,'$.is_emoji')=1
            ORDER BY v.created_at DESC, v.observation_id DESC
        """,
            (self.shared, self.scope),
        ).fetchall()
        catalog = {}
        for row in rows:
            catalog.setdefault(row["asset_id"], dict(row))
        ranked = await rank_candidates(self, list(catalog.values()), query[:240], 12)
        return [
            result | {"score": row.get("score", 0), "caption": row["caption"]}
            for row in ranked
            if (result := await self.image_asset(row["asset_id"]))["success"]
        ]

    async def admin(self, operation, payload):
        from .memory_panel import execute_admin
        from .memory_visibility import assert_visible, record_operation

        assert_visible(self, operation, payload)
        result = await execute_admin(self, operation, payload)
        record_operation(self, result)
        return result

    async def image_asset(self, asset_id):
        result = await self.kernel.image_memory(
            action="get", asset_id=asset_id, chat_ids=None if self.shared else [self.scope]
        )
        if result["success"]:
            result["path"] = str(
                self.kernel.image_memory_runtime.asset_store.path_for_read(
                    result["asset"]["storage_key"]
                )
            )
        return result

    async def change(self, action, payload, evidence):
        from ..vendor.maibot.amemorix.utils.hash import normalize_text
        from .memory_visibility import assert_visible, record_operation, visible_clause
        from .native_memory_kernel import correction_evidence

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
        visibility, values = visible_clause("paragraph", None if self.shared else self.scope)
        if row is None or not row["source"].startswith("chat_summary:") or not self.metadata._conn.execute("SELECT 1 FROM paragraphs WHERE hash=? AND (" + visibility + ")", (memory_id, *values)).fetchone():
            return {
                "success": False,
                "error": "只能修改当前会话、人格中本插件学习的通用段落。",
            }
        # Use the SDK's existing deleted/inactive/protected-target checks.
        assert_visible(self, "delete_execute" if action == "forget_memory" else "edit", {"kind": "paragraph", "id": memory_id})
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
            record_operation(self, result)
            return {
                "success": result["success"],
                "change_id": result.get("operation_id", ""),
                "memory_id": memory_id,
                "error": result.get("error", ""),
            }
        if normalize_text(payload["content"]) == normalize_text(row["content"]):
            return {"success": False, "error": "新内容与原记录相同，无需更正。"}
        target_scope = row["source"].removeprefix("chat_summary:")
        plan = self.metadata.create_fuzzy_modify_plan(
            request_text=evidence[0]["quote"],
            scope="memory",
            target_chat_id=target_scope,
            requested_by=actor,
            reason=reason,
            confidence=1.0,
            preview={
                "native_action": action,
                "memory_id": memory_id,
                "evidence": evidence,
            },
            plan={
                "chat_id": target_scope,
                "operations": [
                    {
                        "action": "mark_superseded",
                        "target_type": "paragraph",
                        "hash": memory_id,
                    },
                    {
                        "action": "ingest_text",
                        "source_type": "chat_summary",
                        "chat_id": target_scope,
                        "text": payload["content"],
                        "relations": payload["relations"],
                    },
                ],
            },
        )
        record_operation(self, plan)
        token = correction_evidence.set((plan["plan_id"], target_scope, evidence))
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
        from .memory_visibility import visible_clause

        def visible(kind, identity):
            from .memory_panel import SPECS

            table, field, _, _ = SPECS[kind]
            clause, values = visible_clause(kind, None if self.shared else self.scope)
            return self.metadata._conn.execute(f'SELECT 1 FROM "{table}" WHERE "{field}"=? AND ({clause})', (identity, *values)).fetchone() is not None
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
                and visible("correction", plan["plan_id"])
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
                or not visible("delete", operation["operation_id"])
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
            "message": "文字记忆已恢复，派生索引尚未全部恢复；请用 search_memory 核对当前能否召回，不能仅凭此状态判断查不到。"
            if metadata_restored and not result["success"]
            else "",
            "error": result.get("error", ""),
        }

    async def summary_status(self, retained=None, namespace=""):
        if retained is not None:
            # Progress contains IDs only for the bounded native material store.
            with self.metadata.transaction():
                filters, values = ["scope=?"], [self.scope]
                if namespace:
                    filters.append("source_id LIKE ?")
                    values.append(namespace + "%")
                if retained:
                    filters.append("source_id NOT IN (" + ",".join("?" for _ in retained) + ")")
                    values.extend(retained)
                self.metadata._conn.execute(
                    "DELETE FROM ovenheart_summary_progress WHERE " + " AND ".join(filters),
                    values,
                )
        attempted = self.metadata._conn.execute("SELECT attempted FROM ovenheart_summary_attempts WHERE scope=?", (self.scope,)).fetchone()
        return {
            "attempted": attempted[0] if attempted else 0,
            "processed": {
                r[0]
                for r in self.metadata._conn.execute(
                    "SELECT source_id FROM ovenheart_summary_progress WHERE scope=?", (self.scope,)
                )
            },
            "episode_pending": self.metadata._conn.execute("SELECT 1 FROM episode_rebuild_sources WHERE source=? AND status IN ('pending','failed','running') LIMIT 1", ("chat_summary:" + self.scope,)).fetchone() is not None,
        }

    async def summary_attempt(self):
        self.metadata._conn.execute(
            "INSERT INTO ovenheart_summary_attempts VALUES (?,?) ON CONFLICT(scope) DO UPDATE SET attempted=excluded.attempted",
            (self.scope, time.time()),
        )
        self.metadata._conn.commit()

    async def review(self, current):
        from .memory_review import review_memory
        from .memory_visibility import record_operation

        result = await review_memory(self, current)
        record_operation(self, result)
        return result

    async def consolidate(self, scope, messages, generate, bot_name, model_name):
        from ..vendor.maibot.amemorix import host
        from ..vendor.maibot.amemorix.summary_host import bind_chat
        from ..vendor.maibot.amemorix.utils.relation_write_service import (
            RelationWriteService,
        )
        from .memory_summary import NativeSummaryImporter

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
                    chat_id=self.scope,
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
                        "INSERT OR IGNORE INTO ovenheart_summary_progress VALUES (?,?,?)",
                        [(self.scope, m["source_id"], m["created"]) for m in messages],
                    )
                result.update(
                    summary_id=next(iter(imported.get("stored_ids", [])), ""),
                    skipped=bool(imported.get("skipped_ids")),
                )
            episodes = (
                await self.kernel._ingest_service.process_episode_source_rebuild_batch(
                    sources=["chat_summary:" + self.scope],
                    limit=1,
                    max_wait_seconds=0,
                    lease_seconds=180,
                )
            )
            result.update(
                episodes=episodes["episode_count"], episode_failures=episodes["failed"]
            )
        return result

    async def episodes(self, query, top_k, *, evidence=None):
        service = self.kernel._get_search_hit_service()
        scope = None if self.shared else type(service)._resolve_retrieval_scope(service, self.scope)
        found = await self.kernel.episode_retriever.query(
            query=query, top_k=top_k, include_paragraphs=True, evidence=evidence, scope=scope, source=None if self.shared else "chat_summary:" + self.scope
        )
        return [
            row
            for row in found
            if not self.metadata.is_episode_source_query_blocked(row.get("source", ""))
        ]

    async def close(self):
        await self._stop_initializing()
        if self.kernel is not None:
            await self.kernel.shutdown()
            self.kernel = None


def read_summary_status(path, retained, scope=None):
    """Inspect an unloaded room's committed progress without starting its SDK."""
    empty = {"attempted": 0, "processed": set(), "episode_pending": False}
    if not path.is_file():
        return empty
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.2)
    try:
        db.execute("BEGIN")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "episode_rebuild_sources" not in tables:
            return empty
        scope = scope or path.parent.parent.name
        if "ovenheart_summary_progress" in tables:
            row = db.execute("SELECT attempted FROM ovenheart_summary_attempts WHERE scope=?", (scope,)).fetchone()
            processed = {row[0] for row in db.execute("SELECT source_id FROM ovenheart_summary_progress WHERE scope=?", (scope,))}
        elif "ovenheart_summary_runtime" in tables:
            row = db.execute("SELECT attempted FROM ovenheart_summary_runtime WHERE singleton=1").fetchone()
            processed = {row[0] for row in db.execute("SELECT source_id FROM ovenheart_summary_processed")}
        else:
            return empty
        return {
            "attempted": row[0] if row else 0,
            "processed": processed if retained is None else processed.intersection(retained),
            "episode_pending": db.execute("SELECT 1 FROM episode_rebuild_sources WHERE source=? AND status IN ('pending','failed','running') LIMIT 1", ("chat_summary:" + scope,)).fetchone() is not None,
        }
    finally:
        db.close()


class RoomMemory:
    def __init__(self, plugin):
        self.plugin = plugin
        self.rooms = OrderedDict()
        self.locks = {}
        self.directory = plugin.store.path.parent / "amemorix" / "rooms"
        self.loop = None
        self.thread = None
        self.closing = False
        self.embedding = None
        self.image_embeddings = {}
        self.recovered_images = set()
        self.room_pool_lock = asyncio.Lock()
        self.migrations = []

    async def prepare(self):
        from .persona_store import migrate_persona

        catalog = await self.plugin.store.read("memory_catalog")
        ownership = self.directory.parent / "persona_sources.json"
        previous = (
            json.loads(ownership.read_text(encoding="utf-8"))
            if ownership.exists()
            else []
        )
        owners = {record["id"]: record["persona"] for record in previous}
        for record in catalog:
            if record["id"] in owners and owners[record["id"]] != record["persona"]:
                raise ValueError("旧记忆的人格归属与当前配置冲突，未自动混合。")
            owners[record["id"]] = record["persona"]
        groups = {}
        for scope, persona in owners.items():
            groups.setdefault(persona, []).append(scope)
        for persona, scopes in groups.items():
            worker = asyncio.create_task(
                asyncio.to_thread(migrate_persona, self.directory, persona, scopes)
            )
            try:
                self.migrations.append(await asyncio.shield(worker))
            except asyncio.CancelledError:
                await worker
                raise
        ownership.parent.mkdir(parents=True, exist_ok=True)
        temporary = ownership.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                [{"id": scope, "persona": persona} for scope, persona in owners.items()],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temporary.replace(ownership)

    async def storage_scope(self, scope):
        from .persona_store import persona_key

        record = (
            await self.plugin.store.read("scope_profile", scope)
            if scope != SHARED_SCOPE
            else None
        )
        return persona_key(record["persona"]) if record else scope

    async def scope_keys(self, scope):
        return [await self.storage_scope(scope)]

    def source_id(self, scope, source):
        return str(source)

    async def observe(self, event, scope, *, state=None):
        """Retain native authorization fields, without message/media/history data."""
        from astrbot.core.platform.astrbot_message import (
            AstrBotMessage,
            Group,
            MessageMember,
        )

        state = state or self.rooms.get(await self.storage_scope(scope))
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
        # AstrMessageEvent's constructor starts a persisted message trace even
        # when it is never dispatched. A plain identity must not use it.
        state.identities[scope] = SimpleNamespace(
            identity=MemoryIdentity(event, message), event_ref=weakref.ref(event),
            session=SimpleNamespace(session_id=scope, group_id=event.get_group_id(), user_id=event.get_sender_id(), self_id=event.get_self_id()),
        )

    async def execute(self, state, operation, args, source_scope=None):
        """Run upstream async work off-host and acknowledge cancellation cleanup."""
        if self.loop is None:
            self.loop = asyncio.new_event_loop()
            self.thread = threading.Thread(
                target=self.loop.run_forever, name="ovenheart-memory", daemon=True
            )
            self.thread.start()
        host = asyncio.get_running_loop()
        people_engine = (
            self.plugin.person_memory.engine
            or await self.plugin.person_memory.call("initialize")
        )
        source_scope = source_scope or state.path.name
        shared = (
            not self.plugin.settings["memory_isolation"]
            and source_scope != SHARED_SCOPE
        )
        result, started = host.create_future(), host.create_future()

        def finish(task):
            if task.cancelled():
                result.cancel()
            elif task.exception() is not None:
                result.set_exception(task.exception())
            else:
                result.set_result(task.result())

        async def run():
            from ..vendor.maibot.amemorix import host as memory_host
            from ..vendor.maibot.amemorix.sdk_host import bind_native
            from ..vendor.maibot.amemorix.summary_host import bind_chat_reader
            from .memory_embedding import embedding_budget

            async def native_generate(request):
                async def generate():
                    if state.operation == "review":
                        async with self.model_window(source_scope):
                            return await self.generate(state, request)
                    return await self.generate(state, request)

                future = asyncio.run_coroutine_threadsafe(generate(), host)
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
                state.bind(source_scope, shared),
                bind_native(
                    data_root=state.path,
                    embedding=state.embedding,
                    session=state.session,
                    generate=native_generate,
                    image_embedding=state.image_embedding,
                ),
                memory_host.bind(
                    people_engine,
                    native_generate,
                    integration=NativeMemoryIntegration(self.plugin),
                ),
                bind_chat_reader(native_messages, lambda: state.session.self_id),
            ):
                if operation != "close":
                    await state.initialize()
                    # Text recall must not scan image files or acquire one
                    # write transaction per asset before searching paragraphs.
                    if (
                        (operation.startswith("image_") or operation == "admin")
                        and not state.image_recovered
                        and state.kernel.image_memory_runtime is not None
                    ):
                        await state.kernel.image_memory_runtime.recover_assets()
                        state.image_recovered = True
                budget = (
                    self.plugin.settings["retrieval_timeout"]
                    if operation in {"search", "image_search"}
                    else None
                )
                with embedding_budget(budget) if budget is not None else nullcontext():
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
        from .native_memory_kernel import feedback_origin

        origin = feedback_origin.get()
        event = (
            self.plugin.events.get(origin)
            or (
                state.identities[origin].identity
                if origin in state.identities
                else None
            )
            if origin
            else state.identity or state.event_ref()
        )
        revision = self.plugin.settings_revision
        if event is None:
            raise ValueError("该记忆会话没有可用的原生消息来源。")
        scope, persona, _ = await self.plugin.resolve_scope(event)
        if state.path.name not in await self.scope_keys(
            scope
        ) or not await self.permitted(event, scope, persona, learning=True):
            raise ValueError("当前记忆来源或学习权限已撤销。")
        provider = (
            self.plugin.settings["memory_summary_provider_id"]
            or self.plugin.settings["provider_id"]
            or await self.plugin.context.get_current_chat_provider_id(
                event.unified_msg_origin
            )
        )
        stage = (
            "memory_feedback"
            if "feedback" in request.request_type.lower()
            else "memory_episode"
            if "episode" in request.request_type.lower()
            else "memory_review"
            if state.operation == "review"
            else "memory_maintenance"
        )
        try:
            response = await self.plugin.auxiliary.generate(
                stage,
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
        except TimeoutError as exc:
            from ..auxiliary import BACKGROUND_MODEL_TIMEOUTS

            raise TimeoutError(
                f"记忆后台模型请求超过 {BACKGROUND_MODEL_TIMEOUTS[stage]} 秒 ({stage})，保留现有记忆。"
            ) from exc
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
        event = self.plugin.events.get(chat_id) or (
            state.identities[chat_id].identity if chat_id in state.identities else None
        )
        revision = self.plugin.settings_revision
        if event is None:
            return []
        scope, persona, _ = await self.plugin.resolve_scope(event)
        if (
            scope != chat_id
            or state.path.name not in await self.scope_keys(scope)
            or not await self.permitted(event, scope, persona, learning=True)
        ):
            return []
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
                await conversation_metadata(
                    self.plugin.context.conversation_manager,
                    event.unified_msg_origin,
                    cid,
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
                "source_id": self.source_id(scope, r["source"]),
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
        profile = await self.plugin.store.read(
            "scope_profile", scope if s["session_isolation"] else SHARED_SCOPE
        )
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
        state = self.rooms[await self.storage_scope(scope)]
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

    async def execute_foreground(self, state, operation, args, revision, source_scope):
        async with state.foreground_lock:
            while state.operation in {"consolidate", "review"} and not state.model_waiting:
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
                return await self.execute(state, operation, args, source_scope)
            finally:
                state.active_foreground.discard(task)

    async def call(self, scope, operation, *args, event=None):
        revision = self.plugin.settings_revision
        result = await self.call_storage(await self.storage_scope(scope), operation, *args, event=event, source_scope=scope)
        if revision != self.plugin.settings_revision:
            raise ValueError("记忆范围已改变，取消之前范围的检索。")
        return result

    async def call_storage(self, scope, operation, *args, event=None, source_scope=None):
        from .memory_embedding import EmbeddingAPIAdapter

        revision = self.plugin.settings_revision
        source_scope = source_scope or scope
        if self.closing or self.plugin.closing:
            raise ValueError("记忆运行时正在关闭。")
        if operation == "summary_status" and scope not in self.rooms:
            status = await asyncio.to_thread(
                read_summary_status,
                self.directory / scope / "metadata" / "metadata.db",
                args[0] if args else None,
                source_scope,
            )
            if (
                revision != self.plugin.settings_revision
                or self.closing
                or self.plugin.closing
            ):
                raise ValueError("读取摘要进度期间配置或运行状态已改变。")
            return status
        empty_reads = {
            "search": [],
            "image_candidates": [],
            "person_context": {"paragraphs": [], "relations": [], "episodes": []},
            "midterm_records": {"cutoff": 0, "records": []},
        }
        if (
            operation in empty_reads
            and scope not in self.rooms
            and not (self.directory / scope / "metadata" / "metadata.db").exists()
        ):
            return empty_reads[operation]
        embedding = EmbeddingAPIAdapter(
            self.plugin.context,
            self.plugin.settings["embedding_provider_id"],
            asyncio.get_running_loop(),
        )
        if (
            self.embedding is not None
            and self.embedding.provider is embedding.provider
            and self.embedding.get_embedding_configuration_key()
            == embedding.get_embedding_configuration_key()
        ):
            embedding = self.embedding
        else:
            self.embedding = embedding
        state = self.rooms.get(scope)
        if (
            state is not None
            and operation
            in (
                "search",
                "write",
                "change",
                "person_context",
                "midterm_records",
                "image_ingest",
                "image_description",
                "image_content_description",
                "image_search",
                "image_asset",
                "image_candidates",
                "queue_feedback",
            )
            and state.operation in {"consolidate", "review"}
            and state.revision == revision
            and state.embedding.provider is embedding.provider
            and state.embedding.get_embedding_configuration_key()
            == embedding.get_embedding_configuration_key()
        ):
            # Foreground memory tools must finish before the planner replies.
            # Lend the worker at the importer's model boundary, retaining the
            # same authoritative stores and serializing foreground writes.
            if event is not None:
                await self.observe(event, source_scope, state=state)
            task = asyncio.create_task(
                self.execute_foreground(state, operation, args, revision, source_scope)
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
                async with self.room_pool_lock:
                    # Parallel cold reads must reserve capacity before loading.
                    while (
                        len(self.rooms)
                        >= self.plugin.settings["memory_runtime_max_rooms"]
                    ):
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
                    state.image_recovered = scope in self.recovered_images
            self.rooms.move_to_end(scope)
            if state.foreground:
                await asyncio.gather(*tuple(state.foreground), return_exceptions=True)
            if (
                self.closing
                or self.plugin.closing
                or revision != self.plugin.settings_revision
            ):
                raise ValueError("记忆配置已改变，取消排队中的操作。")
            if (
                state.embedding is None
                or state.embedding.provider is not embedding.provider
                or state.embedding.get_embedding_configuration_key()
                != embedding.get_embedding_configuration_key()
            ):
                state.embedding = embedding
            state.revision = revision
            from .image_memory import NativeImageEmbedding

            state.image_embedding = NativeImageEmbedding(
                self.plugin,
                state,
                self.plugin.settings["image_embedding_provider_id"],
                asyncio.get_running_loop(),
                cache=self.image_embeddings,
            )
            # Create the image runtime before lending the kernel to foreground
            # work; its first image must not restart an active consolidation.
            state.image_enabled = self.plugin.settings["image_memory_enabled"]
            if event is not None:
                await self.observe(event, source_scope, state=state)
            state.operation = operation
            if operation in {"consolidate", "review"}:
                state.foreground_ready.clear()
            try:
                return await self.execute(state, operation, args, source_scope)
            finally:
                if state.image_recovered:
                    self.recovered_images.add(scope)
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
            f"{scope}:{event_source(event)}",
            scope,
            event=event,
        )
        if revision != self.plugin.settings_revision or not await self.permitted(
            event, scope, persona
        ):
            return []
        return result

    async def person_context(self, event, scope, persona, sender):
        empty = {"paragraphs": [], "relations": [], "episodes": []}
        revision = self.plugin.settings_revision
        if sender in self.plugin.settings[
            "blocked_senders"
        ] or not await self.permitted(event, scope, persona):
            return empty
        result = await self.call(scope, "person_context", sender, event=event)
        if revision != self.plugin.settings_revision or not await self.permitted(
            event, scope, persona
        ):
            return empty
        return result

    async def context(self, event, scope, persona, query):
        if (
            not self.plugin.settings["native_memory_enabled"]
            or not self.plugin.settings["memory_enabled"]
        ):
            return []
        task = asyncio.create_task(self.search(event, scope, persona, query))
        try:
            done, _ = await asyncio.wait(
                [task], timeout=self.plugin.settings["retrieval_timeout"]
            )
            if not done:
                state = self.rooms.get(await self.storage_scope(scope))
                logger.warning(
                    "通用记忆检索超时：等待位置=%s，房间操作=%s，后台模型等待=%s",
                    " -> ".join(await_path(task.get_coro())),
                    state.operation if state else "initializing",
                    state.model_waiting if state else False,
                )
                raise TimeoutError
            return task.result()
        except Exception as exc:
            logger.warning(
                "本轮通用记忆暂不可用，保留原生回复与现有回忆 (%s)", type(exc).__name__
            )
            return []
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def remember(self, event, scope, persona, payload, messages):
        jsonschema.validate(payload, MEMORY_SCHEMA)
        revision = self.plugin.settings_revision
        evidence = await self.verified_evidence(
            event, scope, persona, payload["evidence"], messages
        )
        images = []
        for reference in payload.get("image_evidence", []):
            message = next(
                (
                    m
                    for m in messages
                    if str(m["sequence"]) == reference["msg_id"] and not m.get("self")
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
                or source.unified_msg_origin != event.unified_msg_origin
                or source.get_sender_id() != message["sender_id"]
                or (await self.plugin.resolve_scope(source))[:2] != (scope, persona)
                or not await self.plugin.image_memory.permitted(
                    source, scope, persona, True
                )
            ):
                raise ValueError("图片证据必须来自本轮获准的真实图片消息。")
            pending = self.plugin.image_memory.busy.get((scope, event_source(source)))
            if pending is not None:
                await asyncio.shield(pending)
            asset = source.get_extra("ovenheart_image_assets", {}).get(
                reference["image_index"]
            )
            if not asset or not asset.get("occurrence_id"):
                raise ValueError("图片证据尚未入库，不能声称已关联记忆。")
            images.append(
                {
                    "occurrence_id": asset["occurrence_id"],
                    "source_id": self.source_id(scope, message["source"]),
                    "image_index": reference["image_index"],
                }
            )
        if revision != self.plugin.settings_revision or not await self.permitted(
            event, scope, persona, learning=True
        ):
            raise ValueError("记忆写入权限已撤销。")
        return await self.call(
            scope,
            "write",
            payload["content"],
            evidence,
            payload["relations"],
            images,
            event=event,
        )

    async def change(self, event, scope, persona, action, payload, messages):
        jsonschema.validate(payload, CHANGE_SCHEMAS[action])
        evidence = await self.verified_evidence(
            event, scope, persona, payload["evidence"], messages
        )
        if evidence[0]["source_id"] != event_source(event):
            raise ValueError(
                "更正、遗忘和撤销需要引用当前消息的请求，不能沿用之前轮次的原话。"
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
                or citation["quote"] not in own_message_text(source)
                or not await self.permitted(source, scope, persona, learning=True)
            ):
                raise ValueError("记忆操作需要本轮真实作者的原文和学习权限。")
            evidence.append(
                {
                    "source_id": self.source_id(scope, event_source(source)),
                    "source_scope": scope,
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
