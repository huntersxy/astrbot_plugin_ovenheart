"""Run the original summary and episode services inside an initialized room."""

from ..vendor.maibot.amemorix.utils.hash import compute_hash, normalize_text
from ..vendor.maibot.amemorix.utils.summary_importer import SummaryImporter
from .memory_embedding import embedding_budget
from .room_memory import native_provenance


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
        # Foreground recovery may have reopened the derived vector pools while
        # the summary model was waiting. Commit to the current owning stores.
        state = self.plugin_config["native_state"]
        self.vector_store = state.paragraph_vectors
        self.plugin_config["graph_vector_store"] = state.graph_vectors
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
        # Import owns the room queue after the text model returns. Let the SDK
        # retain metadata and enqueue slow vectors instead of blocking recall.
        with embedding_budget(1.5):
            return await super()._execute_import(
                summary,
                entities,
                relations,
                stream_id,
                time_meta=time_meta,
                metadata=metadata,
            )
