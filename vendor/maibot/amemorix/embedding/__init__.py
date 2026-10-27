"""AstrBot provider transport; retrieval/scoring stays in the upstream modules."""

from .....memory.memory_embedding import EmbeddingAPIAdapter
from ..sdk_host import create_embedding_api_adapter

__all__ = ["EmbeddingAPIAdapter"]
