"""Hybrid habit retrieval using AstrBot's configured embedding providers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time


def normalized_vector(value) -> list[float]:
    """Validate and normalize a model or cached embedding.

    Args:
        value: Untrusted provider output or cached JSON array.

    Returns:
        A unit-length finite vector.

    Raises:
        ValueError: If the vector is empty, non-finite, oversized or zero.
    """
    if not isinstance(value, list) or not 1 <= len(value) <= 8192:
        raise ValueError("Invalid embedding size")
    if any(type(x) not in (int, float) or not math.isfinite(x) for x in value):
        raise ValueError("Invalid embedding value")
    norm = math.hypot(*value)
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("Invalid embedding norm")
    return [x / norm for x in value]


def rank_traits(
    traits: list[dict], query: str, settings: dict, similarities=None
) -> list[dict]:
    """Rank active habits with relevance, evidence, freshness and explicit feedback.

    Args:
        traits: Habits from exactly one session/persona scope.
        query: Current conversation text.
        settings: Retrieval and output limits.
        similarities: Optional cosine scores keyed by trait identifier.

    Returns:
        Ordered copies with scores and human-readable selection reasons.
    """
    query = query[:2400].casefold()
    terms = set(re.findall(r"[a-z0-9_]{2,}", query))
    for part in re.findall(r"[\u3400-\u9fff]+", query):
        terms.update(part[i : i + 2] for i in range(len(part) - 1))
    ranked = []
    now = time.time()
    for trait in traits:
        if trait["status"] != "active":
            continue
        text = f"{trait['situation']} {trait['content']}".casefold()
        matches = sum(t in text for t in terms)
        lexical = matches / max(1, len(terms))
        semantic = (similarities or {}).get(trait["id"], 0)
        relevant = matches > 0 or semantic >= settings["semantic_threshold"]
        if trait["kind"] == "jargon":
            word = (
                trait["content"].replace("：", ":").split(":", 1)[0].strip().casefold()
            )
            # A definition should not leak into unrelated replies through a broad vector match.
            if (
                word not in query
                and not matches
                and semantic < max(0.6, settings["semantic_threshold"])
            ):
                continue
        elif (
            similarities is not None and trait["kind"] == "expression" and not relevant
        ):
            continue
        evidence = min(
            0.2,
            math.log1p(trait.get("evidence_count", len(trait.get("evidence", []))))
            * 0.04,
        )
        age_days = (
            max(0, now - (trait.get("last_seen") or trait.get("created") or now))
            / 86400
        )
        freshness = 0.5 + 0.5 * 2 ** (-age_days / settings["habit_half_life"])
        feedback = 1 + trait.get("rating", 0) * 0.35
        # Exposure encourages variety but is never interpreted as approval.
        variety = 1 / (1 + min(0.3, math.sqrt(trait.get("use_count", 0)) * 0.02))
        score = (
            (
                lexical * 2
                + max(0, semantic) * 1.5
                + trait["confidence"] * 0.15
                + evidence
            )
            * freshness
            * feedback
            * variety
        )
        reason = (
            "语义与文字匹配"
            if matches and semantic > 0
            else "语义匹配"
            if semantic >= settings["semantic_threshold"]
            else "文字匹配"
            if matches
            else "通用习惯"
        )
        ranked.append(
            trait
            | {
                "_score": round(score, 5),
                "_reason": reason,
                "_semantic": round(semantic, 4),
            }
        )
    ranked.sort(key=lambda t: (t["_score"], t["updated"], -t["id"]), reverse=True)
    return ranked[: settings["max_traits"]]


class HabitRetriever:
    """Keep bounded caches and fall back when semantic services are unavailable."""

    def __init__(self, context, store):
        self.context = context
        self.store = store
        self.lock = asyncio.Lock()
        self.query_cache: dict[str, tuple[float, list[float]]] = {}
        self.status: dict[str, dict] = {}

    async def select(self, scope, traits, query, settings, *, semantic=True):
        """Select habits within a total embedding deadline.

        Args:
            scope: Session/persona identity.
            traits: Available habits for this scope only.
            query: Current input plus bounded recent conversation context.
            settings: Effective retrieval configuration.
            semantic: Whether model calls are permitted (false for free previews).

        Returns:
            Habits, retrieval mode and a visible fallback explanation.
        """
        active = [t for t in traits if t["status"] == "active"]
        similarities = None
        mode, message = "lexical", "使用文字相关性；未配置向量模型。"
        if settings["selection_mode"] == "lexical":
            message = "按设置使用文字相关性。"
        elif not semantic:
            message = "免费预览仅使用文字相关性；真实回复可使用语义检索。"
        elif active and query.strip() and settings["embedding_provider_id"]:
            try:
                similarities = await asyncio.wait_for(
                    self.semantic_scores(scope, active, query, settings),
                    timeout=settings["retrieval_timeout"],
                )
                mode, message = (
                    "hybrid",
                    f"已结合语义、文字、证据积累与反馈；本次覆盖 {len(similarities)}/{len(active)} 条习惯。",
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                message = "向量检索不可用或超时，已回退文字相关性；请检查向量模型 ID、网络与维度。"
        result = {
            "traits": rank_traits(active, query, settings, similarities),
            "mode": mode,
            "message": message,
        }
        self.status[scope] = {"mode": mode, "message": message}
        if len(self.status) > 128:
            self.status.pop(next(iter(self.status)))
        return result

    async def semantic_scores(self, scope, traits, query, settings):
        """Embed missing habits in bounded batches and score with cosine similarity.

        Args:
            scope: Session/persona identity used to load only its cached vectors.
            traits: Active habits in this scope.
            query: Bounded conversational query; never persisted to disk.
            settings: Embedding provider and latency configuration.

        Returns:
            Cosine similarities for indexed habits.

        Raises:
            ValueError: If the provider or any embedding is invalid.
        """
        async with self.lock:
            provider_id = settings["embedding_provider_id"]
            provider = self.context.get_provider_by_id(provider_id)
            if provider is None or not callable(
                getattr(provider, "get_embeddings", None)
            ):
                raise ValueError("Embedding provider is unavailable")
            config = getattr(provider, "provider_config", {})
            signature = hashlib.sha256(
                json.dumps(
                    [
                        provider_id,
                        provider.get_model(),
                        {
                            k: config.get(k)
                            for k in ("type", "api_base", "base_url", "dimensions")
                        },
                    ],
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            cached = await self.store.call("vectors", signature, scope)
            records = []
            vectors = {}
            missing = []
            for trait in traits:
                text = f"{trait['situation']}\n{trait['content']}"
                digest = hashlib.sha256(text.encode()).hexdigest()
                entry = cached.get(trait["id"])
                if entry and entry["digest"] == digest:
                    vectors[trait["id"]] = normalized_vector(entry["vector"])
                else:
                    missing.append((trait["id"], digest, text))
            # Cold starts index at most 64 entries per request; subsequent calls advance coverage.
            if missing:
                batch = missing[:64]
                output = await provider.get_embeddings([t[2] for t in batch])
                if not isinstance(output, list) or len(output) != len(batch):
                    raise ValueError("Embedding batch has an invalid length")
                for (identifier, digest, _), vector in zip(batch, output, strict=True):
                    vector = normalized_vector(vector)
                    vectors[identifier] = vector
                    records.append(
                        {"id": identifier, "digest": digest, "vector": vector}
                    )
            cache_key = hashlib.sha256(
                f"{signature}:{scope}:{query[:2400]}".encode()
            ).hexdigest()
            previous = self.query_cache.get(cache_key)
            if previous and previous[0] > time.monotonic() - 300:
                query_vector = previous[1]
            else:
                query_vector = normalized_vector(
                    await provider.get_embedding(query[:2400])
                )
                self.query_cache[cache_key] = (time.monotonic(), query_vector)
                if len(self.query_cache) > 64:
                    self.query_cache.pop(next(iter(self.query_cache)))
            if any(len(v) != len(query_vector) for v in vectors.values()):
                raise ValueError("Embedding dimensions changed without a model change")
            if records:
                await self.store.call("vectors", signature, scope, records)
            return {
                identifier: sum(
                    a * b for a, b in zip(vector, query_vector, strict=True)
                )
                for identifier, vector in vectors.items()
            }
