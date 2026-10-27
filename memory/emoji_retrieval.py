"""Rank the native emoji catalog using its existing text embedding provider."""

import hashlib
from random import sample

import numpy as np
from astrbot.api import logger

from ..core.retrieval import normalized_vector, query_terms
from .memory_embedding import embedding_budget


async def rank_candidates(state, rows, query, limit):
    if not query.strip():
        return sample(rows, min(limit, len(rows)))
    terms = query_terms(query)
    for row in rows:
        text = row["caption"].casefold()
        row["score"] = sum(term in text for term in terms) / max(1, len(terms)) + (
            1.0 if query.casefold() in text else 0
        )
    rows.sort(key=lambda row: row["score"], reverse=True)
    embedding = state.embedding
    if rows and embedding is not None and embedding.available:
        db = state.metadata._conn
        db.execute("""CREATE TABLE IF NOT EXISTS ovenheart_emoji_vectors (
            asset_id TEXT PRIMARY KEY, provider TEXT NOT NULL,
            digest TEXT NOT NULL, vector BLOB NOT NULL)""")
        signature = embedding.get_embedding_configuration_key()
        cached = {
            row["asset_id"]: row
            for row in db.execute(
                "SELECT * FROM ovenheart_emoji_vectors WHERE provider=?", (signature,)
            )
        }
        vectors, missing = {}, []
        for row in rows:
            digest = hashlib.sha256(row["caption"].encode()).hexdigest()
            record = cached.get(row["asset_id"])
            if record is not None and record["digest"] == digest:
                vectors[row["asset_id"]] = np.frombuffer(
                    record["vector"], dtype=np.float32
                )
            else:
                missing.append((row, digest))
        try:
            # Share the room's adapter and bounded cache. Persist document
            # vectors in its existing DB; no second vector service or model.
            # Warm one batch per request, beginning with lexical matches.
            with embedding_budget(1.2):
                if missing:
                    batch = missing[:32]
                    encoded = await embedding.encode(
                        [row["caption"] for row, _ in batch]
                    )
                    for (row, digest), vector in zip(batch, encoded, strict=True):
                        value = np.asarray(
                            normalized_vector(vector.tolist()), dtype=np.float32
                        )
                        vectors[row["asset_id"]] = value
                        db.execute(
                            "INSERT OR REPLACE INTO ovenheart_emoji_vectors VALUES (?,?,?,?)",
                            (row["asset_id"], signature, digest, value.tobytes()),
                        )
                    db.commit()
                query_vector = np.asarray(
                    normalized_vector((await embedding.encode(query)).tolist()),
                    dtype=np.float32,
                )
            for row in rows:
                vector = vectors.get(row["asset_id"])
                if vector is not None and vector.shape == query_vector.shape:
                    semantic = float(np.dot(vector, query_vector))
                    row["score"] = max(
                        row["score"], semantic if semantic >= 0.25 else 0
                    )
        except Exception as exc:
            logger.debug(
                "表情语义检索暂不可用，保留全库文字匹配 (%s)", type(exc).__name__
            )
    return sorted(
        (row for row in rows if row["score"] > 0),
        key=lambda row: row["score"],
        reverse=True,
    )[:limit]
