"""Native scope/source adapter for A_memorix's graph and dual-pool retriever."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from collections import OrderedDict

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


class RoomState:
    """Keep upstream async primitives on one loop, with database work off-host."""

    def __init__(self, path, lock):
        from .vendor.maibot.amemorix.retrieval import SparseBM25Index
        from .vendor.maibot.amemorix.storage import GraphStore, MetadataStore

        self.path = path
        self.loop = asyncio.new_event_loop()
        self.lock = lock
        self.metadata = MetadataStore(path / "metadata")
        self.metadata.connect()
        self.metadata._conn.executescript("""
            CREATE TABLE IF NOT EXISTS ovenheart_summary_processed (
                source_id TEXT PRIMARY KEY, created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ovenheart_summary_runtime (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), attempted REAL NOT NULL
            );
            INSERT OR IGNORE INTO ovenheart_summary_runtime VALUES (1,0);
        """)
        (path / "graph").mkdir(parents=True, exist_ok=True)
        self.graph = GraphStore(data_dir=path / "graph")
        if self.graph.has_data():
            self.graph.load()
        self.sparse = SparseBM25Index(self.metadata)
        self.paragraph_vectors = None
        self.graph_vectors = None
        self.vector_space = ""
        self.fingerprint = None
        self.embedding = None
        self.operation = None

    def _retriever(self):
        from .vendor.maibot.amemorix.retrieval import (
            DualPathRetriever,
            DualPathRetrieverConfig,
        )

        return DualPathRetriever(
            vector_store=self.paragraph_vectors,
            paragraph_vector_store=self.paragraph_vectors,
            graph_vector_store=self.graph_vectors,
            graph_store=self.graph,
            metadata_store=self.metadata,
            embedding_manager=self.embedding if self.paragraph_vectors else None,
            sparse_index=self.sparse,
            config=DualPathRetrieverConfig(),
        )

    async def _prepare_vectors(self, text):
        from .vendor.maibot.amemorix.storage import VectorStore

        if not self.embedding.available:
            self.paragraph_vectors = self.graph_vectors = None
            return False
        await self.embedding.encode(text)
        fingerprint = self.embedding.get_embedding_fingerprint(
            dimension=self.embedding.dimension
        )
        space = fingerprint["hash"].removeprefix("sha256:")
        if self.vector_space == space and self.paragraph_vectors is not None:
            return True
        self.fingerprint = fingerprint
        stores = []
        for pool in ("paragraphs", "graph"):
            directory = self.path / "vectors" / space / pool
            store = VectorStore(self.embedding.dimension, data_dir=directory)
            if store.has_data():
                store.load(expected_embedding_fingerprint=fingerprint)
                store.warmup_index()
            stores.append(store)
        self.paragraph_vectors, self.graph_vectors = stores
        self.vector_space = space
        # The metadata/evidence remain primary; only derived jobs are reset when
        # a provider space changes. Old vector directories remain recoverable.
        with self.metadata.transaction():
            for row in self.metadata._conn.execute(
                "SELECT hash FROM paragraphs WHERE is_deleted=0"
            ):
                if row[0] not in self.paragraph_vectors:
                    self.metadata.enqueue_paragraph_vector_backfill(row[0])
                    self.metadata._conn.execute(
                        "UPDATE paragraph_vector_backfill SET status='pending',retry_count=0 WHERE paragraph_hash=?",
                        (row[0],),
                    )
        return True

    async def _backfill(self):
        import numpy as np

        from .vendor.maibot.amemorix.utils.relation_write_service import (
            RelationWriteService,
        )

        batch = self.metadata.fetch_paragraph_vector_backfill_batch(limit=32)
        rows = [self.metadata.get_paragraph(item["paragraph_hash"]) for item in batch]
        rows = [r for r in rows if r and not r.get("is_deleted")]
        if rows:
            vectors = await self.embedding.encode_batch([r["content"] for r in rows])
            self.paragraph_vectors.add(np.asarray(vectors), [r["hash"] for r in rows])
            self.metadata.mark_paragraph_vector_backfill_done([r["hash"] for r in rows])
        writer = RelationWriteService(
            self.metadata,
            self.graph,
            self.paragraph_vectors,
            self.embedding,
            graph_vector_store=self.graph_vectors,
            use_typed_relation_ids=True,
        )
        relations = [
            r
            for r in self.metadata.get_relations(include_inactive=False)
            if "relation:" + r["hash"] not in self.graph_vectors
        ][:32]
        for relation in relations:
            await writer.ensure_relation_vector(
                hash_value=relation["hash"],
                subject=relation["subject"],
                predicate=relation["predicate"],
                obj=relation["object"],
                typed_id=True,
            )
        # Entity vectors are separate graph seeds, never mixed into paragraph IDs.
        entities = [
            dict(r)
            for r in self.metadata._conn.execute("SELECT hash,name FROM entities")
            if "entity:" + r["hash"] not in self.graph_vectors
        ][:64]
        if entities:
            vectors = await self.embedding.encode_batch([r["name"] for r in entities])
            self.graph_vectors.add(
                np.asarray(vectors), ["entity:" + r["hash"] for r in entities]
            )
        self.paragraph_vectors.save(embedding_fingerprint=self.fingerprint)
        self.graph_vectors.save(embedding_fingerprint=self.fingerprint)

    async def search(self, query, top_k):
        if not self.metadata.count_paragraphs():
            return []
        try:
            if await asyncio.wait_for(self._prepare_vectors(query), 1.5):
                await asyncio.wait_for(self._backfill(), 1.5)
        except Exception as exc:
            logger.warning(
                "记忆向量通道暂不可用，使用原版稀疏/图检索 (%s)", type(exc).__name__
            )
            self.paragraph_vectors = self.graph_vectors = None
        found = await self._retriever().retrieve(query, top_k=top_k)
        result = []
        for item in found:
            row = (
                self.metadata.get_paragraph(item.hash_value)
                if item.result_type == "paragraph"
                else self.metadata.get_relation(item.hash_value)
            ) or {}
            source = row.get("metadata") or {}
            result.append(
                {
                    "id": item.hash_value,
                    "content": item.content[:800],
                    "score": item.score,
                    "type": item.result_type,
                    "backend": "amemorix",
                    "metadata": {
                        "source_type": source.get("source_type", ""),
                        "participants": source.get("participants", []),
                        "evidence": [
                            {**entry, "quote": entry["quote"][:160]}
                            for entry in source.get("evidence", [])[:4]
                        ],
                        "time_meta": item.metadata.get("time_meta", {}),
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
        from .vendor.maibot.amemorix.utils.relation_write_service import (
            RelationWriteService,
        )

        # Participants are provenance, not claims that every fact belongs to
        # every participant. Only the person-fact lane assigns person_ids.
        previous = (
            self.metadata.get_paragraph(compute_hash(normalize_text(content))) or {}
        )
        previous_metadata = previous.get("metadata") or {}
        metadata = native_provenance(evidence, previous_metadata.get("evidence", []))
        paragraph = self.metadata.add_paragraph(
            content,
            source="chat_summary:astrbot",
            metadata=metadata,
            time_meta={
                "event_time_start": min(e["created"] for e in evidence),
                "event_time_end": max(e["created"] for e in evidence),
            },
        )
        self.metadata.enqueue_paragraph_vector_backfill(paragraph)
        writer = RelationWriteService(self.metadata, self.graph, None, None)
        for relation in relations:
            for name in (relation["subject"], relation["object"]):
                self.metadata.add_entity(name, source_paragraph=paragraph)
            await writer.upsert_relation_with_vector(
                relation["subject"],
                relation["predicate"],
                relation["object"],
                source_paragraph=paragraph,
                metadata=metadata,
                write_vector=False,
            )
        self.graph.save()
        vector_written = False
        try:
            if await asyncio.wait_for(self._prepare_vectors(content), 1.5):
                await asyncio.wait_for(self._backfill(), 1.5)
                vector_written = paragraph in self.paragraph_vectors
        except Exception as exc:
            self.metadata.mark_paragraph_vector_backfill_failed(
                paragraph, type(exc).__name__
            )
        return {
            "id": paragraph,
            "stored": True,
            "vector_written": vector_written,
            "backfill": self.metadata.get_paragraph_vector_backfill_status_counts(),
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
        from .memory_summary import NativeEpisodeRuntime, NativeSummaryImporter
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
                    self.paragraph_vectors = self.graph_vectors = None
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
                        "graph_vector_store": self.graph_vectors,
                        "relation_write_service": RelationWriteService(
                            self.metadata, self.graph, None, None
                        ),
                        "retrieval": {"vector_pools": {"mode": "dual"}},
                        "summarization": {"model_name": ["auto"]},
                    },
                )
                imported = await importer.import_from_stream(
                    scope,
                    context_length=len(messages),
                    include_personality=False,
                    time_end=max(m["created"] for m in messages),
                    metadata=metadata,
                )
                if not imported.success:
                    raise RuntimeError("原版摘要导入未完成：" + imported.detail[:200])
                with self.metadata.transaction():
                    self.metadata._conn.executemany(
                        "INSERT OR IGNORE INTO ovenheart_summary_processed VALUES (?,?)",
                        [(m["source_id"], m["created"]) for m in messages],
                    )
                result.update(
                    summary_id=imported.paragraph_hash, skipped=imported.skipped
                )
            episodes = await NativeEpisodeRuntime(self).rebuild()
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

    def close(self):
        self.sparse.unload()
        self.metadata.close()
        self.loop.close()


class RoomMemory:
    def __init__(self, plugin):
        self.plugin = plugin
        self.rooms = OrderedDict()
        self.locks = {}
        self.directory = plugin.store.path.parent / "amemorix" / "rooms"

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

    async def call(self, scope, operation, *args):
        from .memory_embedding import EmbeddingAPIAdapter

        revision = self.plugin.settings_revision
        lock = self.locks.setdefault(scope, asyncio.Lock())
        async with lock:
            if self.plugin.closing:
                raise ValueError("记忆运行时正在关闭。")
            state = self.rooms.get(scope)
            if state is None:
                if len(self.rooms) >= 64:
                    candidate = next(
                        (k for k, v in self.rooms.items() if not v.lock.locked()), None
                    )
                    if candidate is None:
                        raise RuntimeError("记忆工作队列繁忙。")
                    await asyncio.to_thread(self.rooms.pop(candidate).close)
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
            if self.plugin.closing or revision != self.plugin.settings_revision:
                raise ValueError("记忆配置已改变，取消排队中的操作。")
            embedding = EmbeddingAPIAdapter(
                self.plugin.context,
                self.plugin.settings["embedding_provider_id"],
                asyncio.get_running_loop(),
            )
            cancelled = threading.Event()

            def execute():
                if cancelled.is_set():
                    raise asyncio.CancelledError
                state.embedding = embedding
                state.operation = state.loop.create_task(
                    getattr(state, operation)(*args)
                )
                try:
                    if cancelled.is_set():
                        state.operation.cancel()
                    return state.loop.run_until_complete(state.operation)
                finally:
                    state.operation = None

            task = asyncio.create_task(asyncio.to_thread(execute))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled.set()
                if state.operation is not None:
                    state.loop.call_soon_threadsafe(state.operation.cancel)
                await asyncio.gather(task, return_exceptions=True)
                raise

    async def search(self, event, scope, persona, query):
        revision = self.plugin.settings_revision
        if not query.strip() or not await self.permitted(event, scope, persona):
            return []
        result = await self.call(
            scope, "search", query[:2400], self.plugin.settings["memory_top_k"]
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
        revision = self.plugin.settings_revision
        evidence = []
        for citation in payload["evidence"]:
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
                raise ValueError("记忆入库需要本轮真实作者的原文和学习权限。")
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
        return await self.call(
            scope, "write", payload["content"], evidence, payload["relations"]
        )

    async def close(self):
        for scope, lock in tuple(self.locks.items()):
            async with lock:
                state = self.rooms.pop(scope, None)
                if state is not None:
                    await asyncio.to_thread(state.close)
        self.rooms.clear()
        self.locks.clear()
