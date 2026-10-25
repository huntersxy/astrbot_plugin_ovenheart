"""AstrBot lifecycle boundaries around the original A_memorix SDK kernel."""

from contextvars import ContextVar

from .vendor.maibot.amemorix.runtime.sdk_memory_kernel import SDKMemoryKernel
from .vendor.maibot.amemorix.runtime.services.correction_admin_service import (
    MemoryCorrectionAdminService,
)

correction_evidence = ContextVar("ovenheart_memory_correction_evidence", default=None)


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

    async def shutdown(self):
        # Upstream drops its sparse index reference without unloading its
        # independent SQLite connection. Release it after all workers stop.
        sparse = self.sparse_index
        await super().shutdown()
        if sparse is not None:
            sparse.unload()

    def close(self):
        sparse = self.sparse_index
        super().close()
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
