"""Keep the light SQLite lane available without eager Faiss/SciPy loading."""

from .metadata_store import MetadataStore
from .knowledge_types import KnowledgeType, resolve_stored_knowledge_type


def __getattr__(name):
    if name in ("GraphStore", "SparseMatrixFormat"):
        from . import graph_store

        return getattr(graph_store, name)
    if name in ("VectorStore", "VectorStoreIntegrityError", "ReadOnlyVectorStoreView"):
        from . import vector_store

        return getattr(vector_store, name)
    raise AttributeError(name)
