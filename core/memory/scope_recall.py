"""Federate native stores without copying memories or maintaining another index."""

import asyncio
import hashlib
import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace


def queue_feedback(room, scope, query, source, origin, response):
    from ..vendor.maibot.amemorix import host
    from ..vendor.maibot.amemorix.runtime.services.feedback_correction_service import (
        MemoryFeedbackCorrectionService,
    )
    from ..vendor.maibot.amemorix.storage.metadata_store import MetadataStore
    from .room_memory import NativeMemoryIntegration

    metadata = MetadataStore(room.directory / scope / "metadata")
    try:
        metadata.connect()
        identity = hashlib.sha256(json.dumps([scope, source, query, [h["hash"] for h in response["hits"]]]).encode()).hexdigest()
        service = MemoryFeedbackCorrectionService(SimpleNamespace(metadata_store=metadata))
        with host.bind(None, None, integration=NativeMemoryIntegration(room.plugin)):
            return asyncio.run(service.enqueue_feedback_task(query_tool_id="native:" + identity, session_id=scope, structured_content=response | {"query":query,"chat_id":scope,"source_scope":origin}))
    finally:
        metadata.close()


def contains(directory, scope, table, column, identity=None):
    path = directory / scope / "metadata/metadata.db"
    if not path.exists():
        return False
    with closing(
        sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
    ) as db:
        if identity is None:
            condition = "is_deleted=0" if table == "paragraphs" else "status='active'"
            return (
                db.execute(
                    f"SELECT 1 FROM {table} WHERE {condition} LIMIT 1"
                ).fetchone()
                is not None
            )
        return (
            db.execute(
                f"SELECT 1 FROM {table} WHERE {column}=? LIMIT 1", (identity,)
            ).fetchone()
            is not None
        )


async def locate_operation(room, keys, operation, args):
    if len(keys) == 1:
        return keys[0]
    target = None
    if operation == "image_asset":
        target = ("image_assets", "asset_id", args[0])
    elif operation in {"image_description"}:
        target = ("image_occurrences", "occurrence_id", args[0])
    elif operation == "change" and args[0] == "undo_memory_change":
        identity = args[1].get("change_id")
        if identity:
            for key in keys:
                for table, column in (
                    ("memory_fuzzy_modify_plans", "plan_id"),
                    ("delete_operations", "operation_id"),
                ):
                    if await asyncio.to_thread(
                        contains, room.directory, key, table, column, identity
                    ):
                        return key
        else:
            actor = "ovenheart:" + args[2][0]["sender"]
            changes = await asyncio.to_thread(
                latest_change, room.directory, keys, actor
            )
            return changes
    elif operation == "change" and args[0] != "undo_memory_change":
        target = ("paragraphs", "hash", args[1]["memory_id"])
    elif operation == "admin":
        from .memory_panel import SPECS

        action, payload = args
        identity = payload.get("id")
        kind = payload.get("kind")
        if action in {"reinforce", "freeze", "protect"}:
            kind = "relation"
        elif action in {"rollback", "review_execute"}:
            kind = "correction"
        elif action == "delete_restore":
            kind = "delete"
        elif action in {"image_forget", "image_observe"}:
            target = ("image_occurrences", "occurrence_id", identity)
        elif action == "node_rename":
            target = ("entities", "name", identity)
        if kind == "source" and identity:
            target = ("paragraphs", "source", identity)
        if target is None and kind in SPECS and identity:
            table, column, _, _ = SPECS[kind]
            target = (table, column, identity)
    if target is not None:
        for key in keys:
            if await asyncio.to_thread(contains, room.directory, key, *target):
                return key
    return None


def latest_change(directory, keys, actor):
    changes = []
    for key in keys:
        path = directory / key / "metadata/metadata.db"
        if not path.exists():
            continue
        with closing(
            sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
        ) as db:
            for table, condition in (
                (
                    "memory_fuzzy_modify_plans",
                    "status='executed' AND json_extract(preview_json,'$.native_action')='correct_memory'",
                ),
                (
                    "delete_operations",
                    "status IN ('pending_cleanup','completed','restore_pending') AND json_extract(reason,'$.native_action')='forget_memory'",
                ),
            ):
                row = db.execute(
                    f"SELECT MAX(created_at) FROM {table} WHERE requested_by=? AND {condition}",
                    (actor,),
                ).fetchone()
                if row[0] is not None:
                    changes.append((row[0], key))
    return max(changes)[1] if changes else None


def ranked(rows, limit):
    unique = {}
    for row in sorted(
        rows,
        key=lambda item: (item.get("match_kind") == "exact_hash", item.get("score", 0)),
        reverse=True,
    ):
        identity = (
            row.get("type"),
            row.get("id")
            or row.get("hash")
            or row.get("asset_id")
            or row.get("asset", {}).get("asset_id"),
        )
        unique.setdefault(identity, row)
    return list(unique.values())[:limit]


async def read_all(room, keys, operation, args, event, source_scope):
    outputs = []
    # Keep hot runtimes useful across global scans; cold stores share the same
    # bounded pool and provider cache instead of each keeping another corpus.
    shared = keys[-1]
    keys = sorted(keys, key=lambda key: key not in room.rooms)
    for key in keys:
        if operation in {
            "image_search",
            "image_candidates",
        } and not await asyncio.to_thread(
            contains, room.directory, key, "image_assets", "asset_id"
        ):
            continue
        if operation in {
            "search",
            "person_context",
        } and not await asyncio.to_thread(
            contains, room.directory, key, "paragraphs", "hash"
        ):
            continue
        extra = [True] if operation == "search" else []
        if operation == "image_search":
            extra = [
                f"{key}:{source_scope}:{args[1]}:{args[2]}"
                if key == shared
                else f"{source_scope}:{args[1]}:{args[2]}"
            ]
        result = await room.call_storage(key, operation, *args, *extra, event=event)
        if operation == "search":
            result["results"] = [
                row | {"storage_scope": key} for row in result["results"]
            ]
            result["storage_scope"] = key
        outputs.append(result)
    if operation == "search":
        selected = ranked(
            [row for result in outputs for row in result["results"]], args[1]
        )
        for packet in outputs:
            used = [
                row
                for row in selected
                if row["storage_scope"] == packet["storage_scope"]
            ]
            hashes = {row["id"] for row in used} | {
                p["id"] for row in used for p in row["metadata"].get("paragraphs", [])
            }
            hits = [row for row in packet["feedback"]["hits"] if row["hash"] in hashes]
            if hits and args[2]:
                if packet["storage_scope"] in room.rooms:
                    await room.call_storage(
                        packet["storage_scope"],
                        "queue_feedback",
                        args[0],
                        args[2],
                        args[3],
                        packet["feedback"] | {"hits": hits},
                        event=event,
                    )
                    continue
                await asyncio.to_thread(
                    queue_feedback,
                    room,
                    packet["storage_scope"],
                    args[0],
                    args[2],
                    args[3],
                    packet["feedback"] | {"hits": hits},
                )
        return selected
    if operation == "person_context":
        return {
            kind: ranked([row for result in outputs for row in result[kind]], 4)
            for kind in ("paragraphs", "relations", "episodes")
        }
    if operation == "image_search":
        return {
            "success": not outputs or any(result.get("success") for result in outputs),
            "hits": ranked(
                [row for result in outputs for row in result.get("hits", [])], 8
            ),
        }
    return ranked(
        [row for result in outputs for row in result],
        12,
    )
