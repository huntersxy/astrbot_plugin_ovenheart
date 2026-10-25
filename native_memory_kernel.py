"""AstrBot lifecycle boundaries around the original A_memorix SDK kernel."""

from .vendor.maibot.amemorix.runtime.sdk_memory_kernel import SDKMemoryKernel
from .vendor.maibot.amemorix.runtime.services.correction_admin_service import (
    MemoryCorrectionAdminService,
)


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

    async def ingest_text(self, **values):
        metadata = values.get("metadata") or {}
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
