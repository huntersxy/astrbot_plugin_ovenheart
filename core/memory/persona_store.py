"""Move legacy room stores into one SDK-owned store per persona at startup."""

import hashlib
import json
import shutil
import sqlite3
import tempfile
import time
from collections import defaultdict
from contextlib import ExitStack, closing
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from astrbot.api import logger

from ..vendor.maibot.amemorix.runtime.runtime_writer_lock import RuntimeWriterLock
from ..vendor.maibot.amemorix.runtime.services.graph_admin_service import (
    MemoryGraphAdminService,
)
from ..vendor.maibot.amemorix.storage.graph_store import GraphStore
from ..vendor.maibot.amemorix.storage.metadata_store import MetadataStore
from ..vendor.maibot.amemorix.storage.vector_store import VectorStore

PROGRESS_SCHEMA = """
    CREATE TABLE IF NOT EXISTS ovenheart_summary_progress (
        scope TEXT NOT NULL, source_id TEXT NOT NULL, created REAL NOT NULL,
        PRIMARY KEY(scope, source_id)
    );
    CREATE TABLE IF NOT EXISTS ovenheart_summary_attempts (
        scope TEXT PRIMARY KEY, attempted REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS ovenheart_operation_sources (
        id TEXT PRIMARY KEY, scope TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS ovenheart_feedback_task_migration (
        scope TEXT NOT NULL, old_id INTEGER NOT NULL, new_id INTEGER NOT NULL,
        PRIMARY KEY(scope,old_id),
        FOREIGN KEY(new_id) REFERENCES memory_feedback_tasks(id)
    );
"""


def persona_key(persona):
    return hashlib.sha256(
        json.dumps(["ovenheart-persona-memory", persona], ensure_ascii=False).encode()
    ).hexdigest()[:24]


def migrate_persona(directory, persona, scopes):
    """Publish only a validated complete directory; leave original rooms intact."""
    key = persona_key(persona)
    target = directory / key
    marker = target / "persona_store.json"
    if marker.exists():
        return json.loads(marker.read_text(encoding="utf-8"))
    if target.exists():
        raise ValueError("人格记忆目录存在但没有迁移完成标记，保留原库等待检查。")
    shared = hashlib.sha256(
        json.dumps(["ovenheart-global-memory", persona], ensure_ascii=False).encode()
    ).hexdigest()[:24]
    sources = [
        directory / scope
        for scope in dict.fromkeys([*scopes, shared])
        if (directory / scope / "metadata/metadata.db").exists()
    ]
    directory.mkdir(parents=True, exist_ok=True)
    with (
        ExitStack() as writers,
        tempfile.TemporaryDirectory(prefix=f".{key}-", dir=directory) as temporary,
    ):
        for source in sources:
            writer = RuntimeWriterLock(source / ".a_memorix_runtime_writer.lock")
            writer.acquire()
            writers.callback(writer.release)
        staging = Path(temporary) / key
        metadata = MetadataStore(staging / "metadata")
        metadata.connect()
        try:
            db = metadata._conn
            db.executescript(PROGRESS_SCHEMA)
            db.execute("PRAGMA foreign_keys=OFF")
            for source in sources:
                _merge_metadata(db, source)
                logger.info("人格记忆迁移：已合并来源 %s", source.name)
            db.commit()
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("合库后的关联校验失败，未切换原库。")
            db.execute("PRAGMA foreign_keys=ON")
            metadata.ensure_fts_backfilled()
            metadata.ensure_relations_fts_schema()
            metadata.ensure_relations_fts_backfilled()
            metadata.ensure_paragraph_ngram_schema()
            metadata.ensure_paragraph_ngram_backfilled()
            graph = GraphStore(data_dir=staging / "graph")
            MemoryGraphAdminService(
                SimpleNamespace(metadata_store=metadata, graph_store=graph)
            )._rebuild_graph_from_metadata()
            graph.save()
            _merge_vectors(staging, sources)
            _copy_assets(db, staging, sources)
            report = {
                "version": 1,
                "storage_scope": key,
                "sources": [source.name for source in sources],
                "created_at": time.time(),
                "counts": {
                    table: db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                    for table in (
                        "paragraphs",
                        "relations",
                        "entities",
                        "episodes",
                        "image_assets",
                        "image_occurrences",
                        "image_memory_links",
                        "memory_feedback_tasks",
                        "memory_feedback_action_logs",
                        "memory_fuzzy_modify_plans",
                        "delete_operations",
                    )
                },
            }
            db.commit()
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("合库完整性检查失败，未切换原库。")
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            metadata.close()
        (staging / "persona_store.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        staging.rename(target)
    return report


def _merge_metadata(target, source):
    skipped = {
        "schema_migrations",
        "sqlite_sequence",
        "sqlite_stat1",
        "sqlite_stat4",
        "graph_edge_relation_map",
        "paragraph_ngrams",
        "paragraph_ngram_meta",
        "paragraph_tokenized_fts_meta",
        "ovenheart_summary_processed",
        "ovenheart_summary_runtime",
    }
    with closing(
        sqlite3.connect(
            (source / "metadata/metadata.db").as_uri() + "?mode=ro", uri=True
        )
    ) as original:
        original.row_factory = sqlite3.Row
        tables = dict(
            original.execute("SELECT name,sql FROM sqlite_master WHERE type='table'")
        )
        for table, sql in tables.items():
            if table in skipped or "fts" in table or table.startswith("sqlite_"):
                continue
            if not target.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone():
                target.execute(sql)
        task_ids = {}
        # Task IDs are local autoincrements; logs and stale marks refer to them.
        ordered = sorted(tables, key=lambda table: table != "memory_feedback_tasks")
        for table in ordered:
            if table in skipped or "fts" in table or table.startswith("sqlite_"):
                continue
            keys = [
                r[1] for r in original.execute(f'PRAGMA table_info("{table}")') if r[5]
            ]
            for raw in original.execute(f'SELECT * FROM "{table}"'):
                row = dict(raw)
                if table in {"paragraphs", "entities", "relations"}:
                    meta = json.loads(row.get("metadata") or "{}")
                    meta["chat_ids"] = list(
                        dict.fromkeys([*meta.get("chat_ids", []), source.name])
                    )
                    if "evidence" in meta:
                        meta["evidence"] = [
                            entry
                            | {"source_scope": entry.get("source_scope") or source.name}
                            for entry in meta["evidence"]
                        ]
                    row["metadata"] = json.dumps(meta, ensure_ascii=False)
                if table == "memory_feedback_tasks":
                    old_id = row.pop("id")
                    task_ids[old_id] = _insert(target, table, row).lastrowid
                    target.execute(
                        "INSERT INTO ovenheart_feedback_task_migration VALUES (?,?,?)",
                        (source.name, old_id, task_ids[old_id]),
                    )
                    continue
                if table == "memory_feedback_action_logs":
                    row.pop("id")
                if "task_id" in row and row["task_id"] is not None:
                    row["task_id"] = task_ids[row["task_id"]]
                if table == "memory_feedback_action_logs":
                    for name in ("before_json", "after_json"):
                        if row.get(name):
                            row[name] = json.dumps(
                                _feedback_references(json.loads(row[name]), task_ids),
                                ensure_ascii=False,
                            )
                if table == "image_embedding_jobs":
                    match = target.execute(
                        "SELECT job_id FROM image_embedding_jobs WHERE asset_id=? AND fingerprint_hash=? AND generation=?",
                        (row["asset_id"], row["fingerprint_hash"], row["generation"]),
                    ).fetchone()
                    if match:
                        row["job_id"] = match[0]
                where = " AND ".join(f'"{name}"=?' for name in keys)
                previous = (
                    target.execute(
                        f'SELECT * FROM "{table}" WHERE {where}',
                        [row[name] for name in keys],
                    ).fetchone()
                    if keys and all(name in row for name in keys)
                    else None
                )
                if previous is None:
                    _insert(target, table, row)
                    if table in {"memory_fuzzy_modify_plans", "delete_operations"}:
                        target.execute(
                            "INSERT INTO ovenheart_operation_sources VALUES (?,?)",
                            (
                                row[
                                    "plan_id"
                                    if table == "memory_fuzzy_modify_plans"
                                    else "operation_id"
                                ],
                                source.name,
                            ),
                        )
                    continue
                previous = dict(previous)
                if table == "entities":
                    if previous["name"] != row["name"]:
                        raise ValueError("实体标识冲突，未切换原库。")
                    previous["appearance_count"] += row["appearance_count"]
                    previous["is_deleted"] = min(
                        previous["is_deleted"], row["is_deleted"]
                    )
                    previous_meta = json.loads(previous.get("metadata") or "{}")
                    previous_meta["chat_ids"] = list(
                        dict.fromkeys(
                            [*previous_meta.get("chat_ids", []), *meta["chat_ids"]]
                        )
                    )
                    previous["metadata"] = json.dumps(previous_meta, ensure_ascii=False)
                    row = previous
                elif table in {
                    "image_assets",
                    "image_embeddings",
                    "image_embedding_jobs",
                    "image_runtime_state",
                    "ovenheart_emoji_vectors",
                }:
                    if (
                        table == "image_assets"
                        and previous["content_hash"] != row["content_hash"]
                    ):
                        raise ValueError("图片资产标识冲突，未切换原库。")
                    row = max(
                        (previous, row),
                        key=lambda item: (
                            item.get("updated_at", item.get("created_at", 0)) or 0
                        ),
                    )
                    if table == "image_assets" and (
                        previous["status"] == "active" or raw["status"] == "active"
                    ):
                        row = row | {"status": "active"}
                elif previous != row:
                    raise ValueError(f"迁移发现 {table} 记录冲突，未切换原库。")
                target.execute(
                    f'UPDATE "{table}" SET '
                    + ",".join(f'"{name}"=?' for name in row)
                    + f" WHERE {where}",
                    [*row.values(), *[row[name] for name in keys]],
                )
        for old_id, new_id in task_ids.items():
            row = original.execute(
                "SELECT rollback_plan_json,rollback_result_json,decision_json FROM memory_feedback_tasks WHERE id=?",
                (old_id,),
            ).fetchone()
            target.execute(
                "UPDATE memory_feedback_tasks SET rollback_plan_json=?,rollback_result_json=?,decision_json=? WHERE id=?",
                (
                    *[
                        json.dumps(
                            _feedback_references(json.loads(value), task_ids),
                            ensure_ascii=False,
                        )
                        if value
                        else value
                        for value in row
                    ],
                    new_id,
                ),
            )
        if "ovenheart_summary_processed" in tables:
            target.executemany(
                "INSERT INTO ovenheart_summary_progress VALUES (?,?,?)",
                [
                    (source.name, *row)
                    for row in original.execute(
                        "SELECT source_id,created FROM ovenheart_summary_processed"
                    )
                ],
            )
        if "ovenheart_summary_runtime" in tables:
            attempted = original.execute(
                "SELECT attempted FROM ovenheart_summary_runtime WHERE singleton=1"
            ).fetchone()
            if attempted:
                target.execute(
                    "INSERT INTO ovenheart_summary_attempts VALUES (?,?)",
                    (source.name, attempted[0]),
                )


def _insert(db, table, row):
    return db.execute(
        f'INSERT INTO "{table}" ('
        + ",".join(f'"{name}"' for name in row)
        + ") VALUES ("
        + ",".join("?" for _ in row)
        + ")",
        list(row.values()),
    )


def _feedback_references(value, task_ids):
    if isinstance(value, list):
        return [_feedback_references(item, task_ids) for item in value]
    if isinstance(value, dict):
        return {
            key: task_ids[item]
            if key == "task_id" and isinstance(item, int) and item in task_ids
            else _feedback_references(item, task_ids)
            for key, item in value.items()
        }
    return value


def _merge_vectors(staging, sources):
    pools = defaultdict(list)
    for source in sources:
        for path in source.rglob("vectors_metadata.json"):
            pools[path.parent.relative_to(source)].append(path)
    for relative, paths in pools.items():
        first = json.loads(paths[0].read_text(encoding="utf-8"))
        fingerprint = first.get("embedding_fingerprint")
        target = VectorStore(dimension=first["dimension"], data_dir=staging / relative)
        for path in paths:
            info = json.loads(path.read_text(encoding="utf-8"))
            if (info.get("embedding_fingerprint") or {}).get("hash") != (
                fingerprint or {}
            ).get("hash") or info["dimension"] != first["dimension"]:
                raise ValueError("相同向量空间的模型指纹不一致，未切换原库。")
            # Loading can recover append journals. Do so only on a temporary copy.
            with tempfile.TemporaryDirectory(dir=staging) as temporary:
                copied = Path(temporary) / "pool"
                shutil.copytree(path.parent, copied)
                vectors = VectorStore(dimension=info["dimension"], data_dir=copied)
                vectors.load(expected_embedding_fingerprint=fingerprint)
                for batch in vectors.iter_vectors_by_ids(info["known_hashes"]):
                    target.add(np.asarray(list(batch.values())), list(batch))
        target.save(embedding_fingerprint=fingerprint)
    for source in sources:
        for path in (source / "vectors").rglob("*.json"):
            if path.name not in {"active.json", "space.json", "embedding_inputs.json"}:
                continue
            target = staging / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            value = json.loads(path.read_text(encoding="utf-8"))
            if path.name == "embedding_inputs.json" and target.exists():
                value = json.loads(target.read_text(encoding="utf-8")) | value
            target.write_text(json.dumps(value), encoding="utf-8")


def _copy_assets(db, staging, sources):
    for row in db.execute("SELECT storage_key,content_hash,status FROM image_assets"):
        relative = Path(row["storage_key"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("图片资产路径越界，未切换原库。")
        path = next(
            (
                source / "images/assets" / relative
                for source in sources
                if (source / "images/assets" / relative).is_file()
            ),
            None,
        )
        if path is None:
            if row["status"] != "active":
                continue
            raise ValueError("有效图片资产文件缺失，未切换原库。")
        target = staging / "images/assets" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != row["content_hash"]:
            raise ValueError("图片资产校验失败，未切换原库。")
