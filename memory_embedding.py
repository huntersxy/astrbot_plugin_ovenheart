"""Expose AstrBot's embedding provider to the original A_memorix retriever."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from contextlib import contextmanager
from contextvars import ContextVar
from types import SimpleNamespace

import numpy as np

_retrieval_deadline = ContextVar("ovenheart_memory_embedding_deadline", default=None)


@contextmanager
def retrieval_embedding_budget(seconds):
    """Leave time for the SDK's sparse fallback after slow vector requests."""
    budget = SimpleNamespace(deadline=time.monotonic() + seconds)
    token = _retrieval_deadline.set(budget)
    try:
        yield
    finally:
        # SDK services can spawn background tasks that inherit this context.
        # They must not retain an exhausted foreground budget after this read.
        budget.deadline = None
        _retrieval_deadline.reset(token)


class EmbeddingAPIAdapter:
    batch_size = 32
    max_concurrent = 1

    def __init__(self, context, provider_id, loop):
        self.provider = context.get_provider_by_id(provider_id) if provider_id else None
        self.loop = loop
        self.cache = {}
        self.dimension = 0
        self.available = bool(
            self.provider and callable(getattr(self.provider, "get_embeddings", None))
        )
        config = getattr(self.provider, "provider_config", {})
        model = self.provider.get_model() if self.available else ""
        self.fingerprint = {
            "model_name": str(model),
            "configuration_key": hashlib.sha256(
                json.dumps(
                    [
                        provider_id,
                        str(model),
                        {
                            k: config.get(k)
                            for k in (
                                "api_base",
                                "api_base_url",
                                "embedding_api_base",
                                "embedding_api_base_url",
                                "type",
                                "dimensions",
                                "embedding_dimensions",
                            )
                        },
                    ],
                    sort_keys=True,
                    default=str,
                ).encode()
            ).hexdigest(),
        }

    async def _request(self, texts):
        try:
            output = await self.provider.get_embeddings(texts)
        except Exception as exc:
            raise RuntimeError(f"嵌入提供商请求未完成 ({type(exc).__name__})") from None
        vectors = np.asarray(output, dtype=np.float32)
        if (
            vectors.ndim != 2
            or vectors.shape[0] != len(texts)
            or not vectors.shape[1]
            or not np.all(np.isfinite(vectors))
        ):
            raise ValueError("嵌入模型返回的向量数量、维度或数值不正确。")
        return vectors

    def get_embedding_fingerprint(self, *, dimension):
        fingerprint = self.fingerprint | {"dimension": dimension}
        fingerprint["hash"] = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(fingerprint, sort_keys=True).encode()
            ).hexdigest()
        )
        fingerprint["source"] = (
            "observed"
            if self.dimension == dimension and self.dimension
            else "configured"
        )
        return fingerprint

    def get_embedding_configuration_key(self):
        return self.fingerprint["configuration_key"]

    async def _detect_dimension(self):
        if not self.dimension:
            await self.encode("A_memorix dimension probe")
        return self.dimension

    def get_embedding_dimension(self):
        return self.dimension

    async def encode(self, texts, **kwargs):
        single = isinstance(texts, str)
        values = [texts] if single else list(texts)
        if not self.available:
            raise ValueError("未配置可用的 AstrBot 嵌入提供商。")
        missing = list(dict.fromkeys(text for text in values if text not in self.cache))
        for start in range(0, len(missing), self.batch_size):
            batch = missing[start : start + self.batch_size]
            budget = _retrieval_deadline.get()
            deadline = budget.deadline if budget is not None else None
            remaining = deadline - time.monotonic() if deadline is not None else None
            if remaining is not None and remaining <= 0:
                raise TimeoutError("本轮记忆嵌入预算已耗尽，使用稀疏检索。")
            work = asyncio.run_coroutine_threadsafe(self._request(batch), self.loop)
            vectors = await asyncio.wait_for(asyncio.wrap_future(work), remaining)
            self.dimension = int(vectors.shape[1])
            self.cache.update(zip(batch, vectors, strict=True))
        result = np.asarray([self.cache[text] for text in values], dtype=np.float32)
        # The cache is local to a runtime/provider, never shared between accounts.
        while len(self.cache) > 128:
            self.cache.pop(next(iter(self.cache)))
        return result[0] if single else result

    async def encode_batch(self, texts, **kwargs):
        return await self.encode(texts, **kwargs)
