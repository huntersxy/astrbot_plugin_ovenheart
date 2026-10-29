"""Validate consolidated vector pools without encoding existing memories again."""

import json

import numpy as np
from astrbot.api import logger


def recover_persona_pools(kernel):
    if kernel._dual_vector_ready_manifest_path().exists():
        return False
    fingerprint = kernel._current_embedding_fingerprint_for_validation()
    if fingerprint is None:
        return False
    pools = {}
    repairs = []
    for kind, directory in (
        ("paragraph", kernel._paragraph_vector_dir()),
        ("graph", kernel._graph_vector_dir()),
    ):
        path = directory / "vectors_metadata.json"
        if not path.exists():
            return False
        metadata = json.loads(path.read_text(encoding="utf-8"))
        if (metadata.get("embedding_fingerprint") or {}).get("hash") != fingerprint[
            "hash"
        ]:
            return False
        store = kernel._make_vector_store(directory)
        if not metadata.get("known_hashes"):
            commit = metadata.get("binary_commit") or {}
            id_bytes = commit.get("id_bytes", 0)
            if id_bytes:
                # An unready SDK pool used to be saved without loading it:
                # its committed binaries survived, but known_hashes became [].
                # Reconcile exact metadata IDs and retain inactive owners as
                # tombstones instead of making their old vectors visible.
                ids_path = directory / "vectors_ids.bin"
                vectors_path = directory / "vectors.bin"
                if (
                    id_bytes % 8
                    or ids_path.stat().st_size != id_bytes
                    or commit.get("vector_bytes")
                    != id_bytes // 8 * metadata["dimension"] * 2
                    or vectors_path.stat().st_size != commit["vector_bytes"]
                ):
                    return False
                ids = np.fromfile(ids_path, dtype=">i8")
                tables = (
                    (("paragraphs", ""),)
                    if kind == "paragraph"
                    else (("entities", "entity:"), ("relations", "relation:"))
                )
                owners = {
                    store._generate_id(prefix + row["hash"]): prefix + row["hash"]
                    for table, prefix in tables
                    for row in kernel.metadata_store.query(f"SELECT hash FROM {table}")
                }
                if len(set(ids)) != len(ids) or any(
                    value not in owners for value in ids
                ):
                    return False
                metadata["known_hashes"] = [owners[value] for value in ids]
                active = set(kernel._v1_valid_hashes_for_pool(kind))
                metadata["deleted_ids"] = sorted(
                    set(metadata.get("deleted_ids", []))
                    | {int(value) for value in ids if owners[value] not in active}
                )
                repairs.append((path, metadata))
        pools[kind] = store
    # Reconcile both pools before changing either metadata file. Binary payloads
    # and source stores stay untouched, and different model spaces never mix.
    for path, metadata in repairs:
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    for store in pools.values():
        store.load(expected_embedding_fingerprint=fingerprint)
    counts = kernel._count_vector_rebuild_targets()
    paragraph_count = pools["paragraph"].num_vectors
    entity_count = sum(
        value.startswith("entity:") and value in pools["graph"]
        for value in pools["graph"]._known_hashes
    )
    relation_count = pools["graph"].num_vectors - entity_count
    actual = {
        "paragraphs": paragraph_count,
        "entities": entity_count,
        "relations": relation_count,
    }
    missing = {kind: max(0, counts[kind] - done) for kind, done in actual.items()}
    # A ready pair means these validated pools can be read together. Record
    # incomplete embedding coverage explicitly; sparse recall still covers it.
    kernel._write_dual_vector_ready_manifest(
        stats={
            kind: {"done": done, "failed": missing[kind]}
            for kind, done in actual.items()
        },
        migration_stats={
            kind: {"copied": done, "encoded": 0, "missing": missing[kind]}
            for kind, done in actual.items()
        },
        generation_reason="persona_consolidation",
        build_fingerprint=fingerprint,
    )
    logger.info(
        "人格记忆向量池已校验并恢复：paragraph=%s, graph=%s, 待补充=%s，缺项保留稀疏检索",
        paragraph_count,
        pools["graph"].num_vectors,
        sum(missing.values()),
    )
    return True
