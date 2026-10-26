"""AstrBot lifecycle boundaries around the original A_memorix SDK kernel."""

import time
from contextvars import ContextVar

from .vendor.maibot.amemorix.runtime.sdk_memory_kernel import SDKMemoryKernel
from .vendor.maibot.amemorix.runtime.services.correction_admin_service import (
    MemoryCorrectionAdminService,
)
from .vendor.maibot.amemorix.utils.async_work import run_sync_work
from .vendor.maibot.amemorix.utils.episode_retrieval_service import (
    EpisodeRetrievalService,
)
from .vendor.maibot.amemorix.utils.episode_service import EpisodeService

correction_evidence = ContextVar("ovenheart_memory_correction_evidence", default=None)


def effective_paragraphs(kernel, paragraphs):
    now = time.time()
    return kernel._get_search_hit_service()._filter_user_visible_hits(
        [
            paragraph | {"type": "paragraph"}
            for paragraph in paragraphs
            if not paragraph.get("is_deleted")
            and (paragraph.get("expires_at") is None or paragraph["expires_at"] > now)
        ]
    )


class NativeEpisodeService(EpisodeService):
    def __init__(self, kernel, **kwargs):
        super().__init__(**kwargs)
        self.kernel = kernel

    def group_paragraphs(self, paragraphs):
        # Upstream's live-source query excludes deletion/stale relation marks,
        # but includes superseded versions. Filter before grouping/fingerprinting.
        return super().group_paragraphs(effective_paragraphs(self.kernel, paragraphs))


class NativeEpisodeRetrievalService(EpisodeRetrievalService):
    def __init__(self, kernel):
        super().__init__(
            metadata_store=kernel.metadata_store, retriever=kernel.retriever
        )
        self.kernel = kernel

    async def query(self, *, include_paragraphs=False, **kwargs):
        limit = kwargs.get("top_k", 5)
        rows = await super().query(
            include_paragraphs=True, **(kwargs | {"top_k": max(20, limit * 4)})
        )
        result = []
        for row in rows:
            paragraphs = effective_paragraphs(self.kernel, row["paragraphs"])
            # A mixed summary can repeat invalid content even when one of its
            # other paragraphs is still valid. Await the original rebuild.
            if not paragraphs or len(paragraphs) != row["paragraph_count"]:
                continue
            if not include_paragraphs:
                row.pop("paragraphs")
            result.append(row)
            if len(result) == limit:
                break
        return result


class NativeCorrectionService(MemoryCorrectionAdminService):
    def _mark_fuzzy_modify_target_superseded(self, **kwargs):
        # Image-link/fact invalidation leaves transaction ownership to its
        # caller. Commit the whole target before graph.save opens a connection
        # to the same database; otherwise that connection blocks on our write.
        with self.metadata_store.transaction(immediate=True):
            return super()._mark_fuzzy_modify_target_superseded(**kwargs)


class NativeMemoryKernel(SDKMemoryKernel):
    def __init__(self, *, plugin_root, config=None):
        super().__init__(plugin_root=plugin_root, config=config)
        self._correction_admin_service = NativeCorrectionService(self)
        self._scope_statistics_ready = False

    async def initialize(self):
        await super().initialize()
        manager = self.metadata_store._connection_manager
        if not getattr(manager, "_ovenheart_cache_limited", False):
            create = manager._create_connection

            def create_connection():
                connection = create()
                connection.execute("PRAGMA cache_size=-8192")
                return connection

            # The SDK defaults to 64 MB per thread, multiplied by every room.
            # Keep this instance's transaction/connection ownership unchanged.
            with manager._lock:
                manager._create_connection = create_connection
                for connection in manager._connections.values():
                    connection.execute("PRAGMA cache_size=-8192")
                    connection.execute("PRAGMA shrink_memory")
                manager._ovenheart_cache_limited = True
        if not self._scope_statistics_ready:
            await run_sync_work(self._refresh_scope_statistics)
            # ANALYZE ran on the worker's connection. Reload its statistics on
            # the owning read connection without repeating table analysis.
            self.metadata_store._conn.execute("ANALYZE sqlite_schema")
            self._scope_statistics_ready = True
        if self.sparse_index is None:
            return
        config = self.sparse_index.config
        if (
            config.enable_ngram_fallback_index
            and not self.metadata_store.is_paragraph_ngram_ready(n=config.char_ngram_n)
        ):
            # Upstream warmup only checks readiness. Initialize existing rooms
            # once; its normal paragraph writes maintain this index thereafter.
            self.metadata_store.ensure_paragraph_ngram_schema()
            await run_sync_work(
                self.metadata_store.ensure_paragraph_ngram_backfilled,
                n=config.char_ngram_n,
            )

    def _refresh_scope_statistics(self):
        # These two tables need cardinality statistics to avoid a Cartesian
        # expansion. Disk work must not stall other rooms on the memory loop.
        with self.metadata_store.transaction() as db:
            previous_limit = db.execute("PRAGMA analysis_limit").fetchone()[0]
            try:
                db.execute("PRAGMA analysis_limit=400")
                db.execute("ANALYZE entities")
                db.execute("ANALYZE paragraph_entities")
            finally:
                db.execute(f"PRAGMA analysis_limit={previous_limit}")

    def _refresh_runtime_dependents(self, *, preserve_managers=True):
        super()._refresh_runtime_dependents(preserve_managers=preserve_managers)
        if self.metadata_store is not None:
            self.episode_retriever = NativeEpisodeRetrievalService(self)
            self.episode_service = NativeEpisodeService(
                self,
                metadata_store=self.metadata_store,
                plugin_config=self._build_runtime_config(),
                segmentation_service=self.episode_segmentation_service,
            )

    async def shutdown(self):
        # Upstream drops its sparse index reference without unloading its
        # independent SQLite connection. Release it after all workers stop.
        sparse = self.sparse_index
        await super().shutdown()
        self._scope_statistics_ready = False
        if sparse is not None:
            sparse.unload()

    def close(self):
        sparse = self.sparse_index
        super().close()
        self._scope_statistics_ready = False
        if sparse is not None:
            sparse.unload()

    async def ingest_text(self, **values):
        metadata = values.get("metadata") or {}
        correction = correction_evidence.get()
        if (
            correction is not None
            and (metadata.get("memory_change") or {}).get("change_id") == correction[0]
            and values.get("chat_id") == correction[1]
        ):
            from .room_memory import native_provenance
            from .vendor.maibot.amemorix.utils.hash import compute_hash, normalize_text

            previous = (
                self.metadata_store.get_paragraph(
                    compute_hash(normalize_text(values["text"]))
                )
                or {}
            )
            metadata = metadata | native_provenance(
                correction[2], (previous.get("metadata") or {}).get("evidence", [])
            )
            values["metadata"] = metadata
            values["participants"] = metadata["participants"]
            values["entities"] = list(
                dict.fromkeys(
                    [
                        *(values.get("entities") or []),
                        *(
                            name
                            for relation in values.get("relations") or []
                            for name in (relation["subject"], relation["object"])
                        ),
                    ]
                )
            )
        if values["source_type"] == "chat_summary" and metadata.get("evidence"):
            # The original summary facade passes participants inside metadata;
            # the SDK normalizes that field from its explicit argument instead.
            if values.get("participants") is None:
                values["participants"] = metadata.get("participants", [])
            values["relations"] = [
                row | {"metadata": metadata | (row.get("metadata") or {})}
                for row in values.get("relations") or []
            ]
        return await super().ingest_text(**values)
