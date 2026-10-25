"""Keep the light SQLite lane available without eager Faiss/SciPy loading."""

from importlib import import_module

from .metadata_store import MetadataStore
from .knowledge_types import KnowledgeType, resolve_stored_knowledge_type


def __getattr__(name):
    if name in ("GraphStore", "SparseMatrixFormat"):
        from . import graph_store

        return getattr(graph_store, name)
    if name in ("VectorStore", "VectorStoreIntegrityError", "ReadOnlyVectorStoreView", "QuantizationType", "TrustedVectorViewReport"):
        from . import vector_store

        return getattr(vector_store, name)
    for module_name in ("knowledge_types", "type_detection"):
        module = import_module(f"{__name__}.{module_name}")
        if hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(name)
