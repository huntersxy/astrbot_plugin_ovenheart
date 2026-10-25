"""Run the original summary and episode services inside an initialized room."""

from .room_memory import native_provenance
from .vendor.maibot.amemorix.runtime.services.ingest_service import MemoryIngestService
from .vendor.maibot.amemorix.utils.episode_service import EpisodeService
from .vendor.maibot.amemorix.utils.hash import compute_hash, normalize_text
from .vendor.maibot.amemorix.utils.summary_importer import SummaryImporter


class NativeSummaryImporter(SummaryImporter):
    def _persist_vector_store(self, store):
        # None is a real metadata-only runtime, never a fabricated vector index.
        if store is not None:
            super()._persist_vector_store(store)
        graph = self.plugin_config["graph_vector_store"]
        if graph is not None and graph is not store:
            super()._persist_vector_store(graph)

    async def _execute_import(
        self, summary, entities, relations, stream_id, time_meta=None, metadata=None
    ):
        previous = (
            self.metadata_store.get_paragraph(compute_hash(normalize_text(summary)))
            or {}
        )
        metadata = dict(metadata or {})
        metadata.update(
            native_provenance(
                metadata["evidence"],
                (previous.get("metadata") or {}).get("evidence", []),
            )
        )
        return await super()._execute_import(
            summary,
            entities,
            relations,
            stream_id,
            time_meta=time_meta,
            metadata=metadata,
        )


class NativeEpisodeRuntime:
    """Storage is already connected by RoomState; use upstream job publication."""

    def __init__(self, state):
        self.metadata_store = state.metadata
        self.episode_service = EpisodeService(metadata_store=state.metadata)
        self.state = state

    async def initialize(self):
        if self.metadata_store._conn is None:
            raise RuntimeError("情景存储已关闭。")

    def _cfg(self, key, default=None):
        return self.episode_service._cfg(key, default)

    def _persist(self):
        self.state.graph.save()

    async def rebuild(self):
        return await MemoryIngestService(self).process_episode_source_rebuild_batch(
            limit=1,
            max_wait_seconds=0,
            lease_seconds=180,
        )
