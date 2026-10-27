"""Authenticated memory console over the same scoped stores and SDK as chat."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import math
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import jsonschema
from astrbot.api import logger
from astrbot.api.web import request

SPECS = {
    "paragraph": ("paragraphs", "hash", ("content", "source"), "created_at"),
    "entity": ("entities", "hash", ("name",), "created_at"),
    "relation": ("relations", "hash", ("subject", "predicate", "object"), "created_at"),
    "fact": (
        "fact_claims",
        "claim_id",
        ("scope_id", "fact_key", "value_text"),
        "updated_at",
    ),
    "episode": ("episodes", "episode_id", ("title", "summary"), "event_time_start"),
    "profile": (
        "person_profile_snapshots",
        "person_id",
        ("person_id", "profile_text"),
        "updated_at",
    ),
    "image": ("image_assets", "asset_id", ("asset_id", "content_hash"), "created_at"),
    "delete": (
        "delete_operations",
        "operation_id",
        ("reason", "mode", "status"),
        "created_at",
    ),
    "correction": (
        "memory_fuzzy_modify_plans",
        "plan_id",
        ("request_text", "status"),
        "created_at",
    ),
    "feedback": ("memory_feedback_tasks", "task_id", ("status",), "created_at"),
}
TEXT = {"type": "string", "minLength": 1, "maxLength": 30000, "pattern": r"\S"}
IDENTIFIER = {"type": "string", "minLength": 1, "maxLength": 128}
ACTION_FIELDS = {
    "import_text": {"text": TEXT},
    "import_livingmemory": {
        "session": {"type": "string", "maxLength": 1024},
        "persona": {"type": "string", "maxLength": 128},
        "after": {"type": "integer", "minimum": 0, "maximum": 9223372036854775807},
        "until": {"type": "integer", "minimum": 1, "maximum": 9223372036854775807},
    },
    "edit": {
        "id": IDENTIFIER,
        "kind": {"enum": ["paragraph", "relation"]},
        "text": TEXT,
    },
    "rollback": {"id": IDENTIFIER},
    "delete_preview": {
        "id": IDENTIFIER,
        "kind": {"enum": ["paragraph", "relation", "entity", "source"]},
    },
    "delete_execute": {
        "id": IDENTIFIER,
        "kind": {"enum": ["paragraph", "relation", "entity", "source"]},
    },
    "delete_restore": {"id": IDENTIFIER},
    "profile_save": {"id": IDENTIFIER, "text": TEXT},
    "profile_clear": {"id": IDENTIFIER},
    "image_forget": {"id": IDENTIFIER},
    "image_observe": {
        "id": IDENTIFIER,
        "text": TEXT,
        "previous": {"type": "string", "maxLength": 128},
    },
    "profile_aliases": {
        "id": IDENTIFIER,
        "aliases": {
            "type": "array",
            "maxItems": 30,
            "items": {"type": "string", "minLength": 1, "maxLength": 120},
        },
    },
    "fact_save": {"id": IDENTIFIER, "text": TEXT},
    "fact_retract": {"id": IDENTIFIER},
    "fact_restore": {"id": IDENTIFIER},
    "reinforce": {"id": IDENTIFIER},
    "freeze": {"id": IDENTIFIER},
    "protect": {"id": IDENTIFIER},
    "node_create": {"name": {"type": "string", "minLength": 1, "maxLength": 120}},
    "node_rename": {
        "id": {"type": "string", "minLength": 1, "maxLength": 120},
        "name": {"type": "string", "minLength": 1, "maxLength": 120},
    },
    "edge_create": {
        key: {"type": "string", "minLength": 1, "maxLength": 120}
        for key in ("subject", "predicate", "object")
    },
    "export_bundle": {},
    "import_bundle": {
        "encoded": {"type": "string", "minLength": 1, "maxLength": 24 * 1024 * 1024}
    },
}


def record(row, kind):
    item = dict(row)
    for key, value in list(item.items()):
        if (
            value
            and isinstance(value, str)
            and (key == "metadata" or key.endswith("_json"))
        ):
            item[key] = json.loads(value)
    item["kind"] = kind
    item["id"] = str(item[SPECS[kind][1]])
    item["text"] = str(
        item.get("content")
        or item.get("summary")
        or item.get("value_text")
        or item.get("profile_text")
        or item.get("request_text")
        or ""
    )
    item["title"] = str(
        item.get("title")
        or item.get("name")
        or item.get("fact_key")
        or item.get("person_id")
        or item["id"]
    )
    if kind == "relation":
        item["title"] = f"{item['subject']} · {item['predicate']} · {item['object']}"
    item["status"] = (
        "deleted"
        if item.get("is_deleted")
        else "inactive"
        if item.get("is_inactive")
        else item.get("status") or "active"
    )
    if (
        item.get("expires_at")
        and item["expires_at"] <= time.time()
        and item["status"] == "active"
    ):
        item["status"] = "expired"
    valid_to = ((item.get("metadata") or {}).get("memory_change") or {}).get("valid_to")
    if valid_to is not None and valid_to <= time.time() and item["status"] == "active":
        item["status"] = "superseded"
    item["timestamp"] = item.get(SPECS[kind][3]) or item.get("created_at") or 0
    return item


def read_console(
    directory, view, kind, query, offset, inactive, identity, since, until
):
    """Read-only views do not create an SDK runtime or call an embedding model."""
    path = directory / "metadata/metadata.db"
    result = {
        "items": [],
        "total": 0,
        "offset": offset,
        "limit": 30,
        "stats": {},
        "available": path.exists(),
    }
    if not path.exists():
        return result
    with closing(
        sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    ) as db:
        db.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for key in ("paragraph", "entity", "relation", "fact", "episode", "image"):
            table = SPECS[key][0]
            if table in tables:
                clause = (
                    " WHERE COALESCE(is_deleted,0)=0"
                    if key in {"paragraph", "entity"}
                    else ""
                )
                result["stats"][key] = db.execute(
                    f"SELECT COUNT(*) FROM {table}{clause}"
                ).fetchone()[0]

        def rows(selected, *, limit=30, start=offset):
            table, column, search, timestamp = SPECS[selected]
            if table not in tables:
                return [], 0
            clauses, params = [], []
            if identity:
                clauses.append(f"{column}=?")
                params.append(identity)
            if query:
                clauses.append(
                    "("
                    + " OR ".join(
                        f"instr(lower(COALESCE({key},'')),lower(?))>0" for key in search
                    )
                    + ")"
                )
                params.extend([query] * len(search))
                if selected == "image":
                    clauses[-1] = clauses[-1][:-1] + " OR EXISTS(SELECT 1 FROM image_occurrences o JOIN image_observations v ON v.occurrence_id=o.occurrence_id WHERE o.asset_id=image_assets.asset_id AND o.status='active' AND v.superseded_by='' AND instr(lower(v.text),lower(?))>0))"
                    params.append(query)
            if not inactive:
                if selected in {"paragraph", "entity"}:
                    clauses.append("COALESCE(is_deleted,0)=0")
                if selected == "paragraph":
                    clauses.append("(expires_at IS NULL OR expires_at>?)")
                    params.append(time.time())
                    clauses.append(
                        "(json_extract(metadata,'$.memory_change.valid_to') IS NULL OR json_extract(metadata,'$.memory_change.valid_to')>?)"
                    )
                    params.append(time.time())
                if selected == "relation":
                    clauses.append("COALESCE(is_inactive,0)=0")
                if selected == "fact":
                    clauses.append("status='active'")
                if selected == "image":
                    clauses.append("status='active'")
                    if view == "emojis":
                        clauses.append("EXISTS(SELECT 1 FROM image_occurrences o JOIN image_observations v ON v.occurrence_id=o.occurrence_id WHERE o.asset_id=image_assets.asset_id AND o.status='active' AND v.superseded_by='' AND json_extract(v.evidence_json,'$.is_emoji')=1)")
            if selected == "profile":
                clauses.append(
                    "profile_version=(SELECT MAX(p.profile_version) FROM person_profile_snapshots p WHERE p.person_id=person_profile_snapshots.person_id)"
                )
            for value, operator in ((since, ">="), (until, "<=")):
                if value is not None:
                    clauses.append(f"{timestamp}{operator}?")
                    params.append(value)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            count = db.execute(
                f"SELECT COUNT(*) FROM {table}{where}", params
            ).fetchone()[0]
            items = [
                record(row, selected)
                for row in db.execute(
                    f"SELECT * FROM {table}{where} ORDER BY {timestamp} DESC,{column} LIMIT ? OFFSET ?",
                    (*params, limit, start),
                )
            ]
            if selected == "profile":
                for item in items:
                    override = db.execute(
                        "SELECT override_text FROM person_profile_overrides WHERE person_id=?",
                        (item["id"],),
                    ).fetchone()
                    item["manual_override"] = override[0] if override else ""
                    aliases = db.execute(
                        "SELECT * FROM person_profile_alias_overrides WHERE person_id=?",
                        (item["id"],),
                    ).fetchone()
                    item["aliases"] = (
                        json.loads(aliases["aliases_json"]) if aliases else []
                    )
            return items, count

        if view == "timeline":
            # Each stream only needs its first offset+page rows for the merged page.
            streams = [
                rows(key, limit=offset + 30, start=0)
                for key in ("paragraph", "fact", "episode", "correction", "delete")
            ]
            items = sorted(
                (item for stream, _ in streams for item in stream),
                key=lambda item: item["timestamp"],
                reverse=True,
            )
            result.update(
                items=items[offset : offset + 30],
                total=sum(count for _, count in streams),
            )
        elif view == "import":
            where = (
                " WHERE instr(lower(COALESCE(source,'')),lower(?))>0" if query else ""
            )
            params = (query,) if query else ()
            result["items"] = [
                dict(row)
                for row in db.execute(
                    f"SELECT source,COUNT(*) AS count,SUM(COALESCE(is_deleted,0)) AS deleted,MAX(created_at) AS timestamp FROM paragraphs{where} GROUP BY source ORDER BY timestamp DESC LIMIT 30 OFFSET ?",
                    (*params, offset),
                )
            ]
            result["total"] = db.execute(
                f"SELECT COUNT(DISTINCT source) FROM paragraphs{where}", params
            ).fetchone()[0]
        else:
            selected = {
                "profiles": "profile",
                "images": "image",
                "emojis": "image",
                "graph": "relation",
                "delete": "delete",
            }.get(view, kind)
            result["items"], result["total"] = rows(
                selected, limit=100 if view == "graph" else 30
            )
            if view == "graph":
                result["limit"] = 100
            if view in {"images", "emojis"}:
                for item in result["items"]:
                    item["observations"] = [
                        dict(row)
                        for row in db.execute(
                            "SELECT o.occurrence_id,o.message_id,o.occurred_at,o.status AS occurrence_status,b.observation_id,b.text,b.confirm_status FROM image_occurrences o LEFT JOIN image_observations b ON b.occurrence_id=o.occurrence_id AND b.superseded_by='' WHERE o.asset_id=? ORDER BY o.occurred_at DESC LIMIT 20",
                            (item["id"],),
                        )
                    ]
            if identity and result["items"] and selected == "paragraph":
                item = result["items"][0]
                item["entities"] = [
                    row[0]
                    for row in db.execute(
                        "SELECT e.name FROM paragraph_entities p JOIN entities e ON e.hash=p.entity_hash WHERE p.paragraph_hash=?",
                        (identity,),
                    )
                ]
                item["relations"] = [
                    dict(row)
                    for row in db.execute(
                        "SELECT r.hash,r.subject,r.predicate,r.object FROM paragraph_relations p JOIN relations r ON r.hash=p.relation_hash WHERE p.paragraph_hash=?",
                        (identity,),
                    )
                ]
        return result


class MemoryPanel:
    def __init__(self, plugin):
        self.plugin = plugin

    async def scope(self, value):
        if (
            not isinstance(value, str)
            or not re.fullmatch(r"[a-f0-9]{24}", value)
            or value == "0" * 24
        ):
            raise ValueError("请先选择具体会话与人格。")
        if not await self.plugin.store.read("scope_profile", value):
            raise ValueError("当前会话不存在，请刷新会话列表。")
        return value

    async def read(self):
        try:
            scope = await self.scope(request.query.get("scope"))
            view = request.query.get("view", "records")
            if view == "livingmemory":
                from .livingmemory_import import read_source

                result = await asyncio.to_thread(read_source, self.livingmemory_path)
                return {"status": "ok", "data": result}
            kind = request.query.get("kind", "paragraph")
            query = request.query.get("query", "").strip()
            identity = request.query.get("id", "")
            offset = int(request.query.get("offset", 0))
            if (
                view
                not in {
                    "records",
                    "profiles",
                    "images",
                    "graph",
                    "timeline",
                    "import",
                    "inspection",
                    "delete",
                }
                or kind not in SPECS
                or len(query) > 500
                or len(identity) > 128
                or not 0 <= offset <= 100000
            ):
                raise ValueError("记忆查询条件无效，请缩短关键词或重置筛选。")
            bounds = [
                float(value) if (value := request.query.get(key)) else None
                for key in ("since", "until")
            ]
            if any(
                value is not None and (not math.isfinite(value) or value < 0)
                for value in bounds
            ):
                raise ValueError("时间范围无效，请重新选择起止时间。")
            result = await asyncio.to_thread(
                read_console,
                self.plugin.room_memory.directory / scope,
                view,
                kind,
                query,
                offset,
                request.query.get("inactive") == "true",
                identity,
                *bounds,
            )
            return {"status": "ok", "data": result}
        except (ValueError, sqlite3.Error) as exc:
            return {
                "status": "error",
                "message": str(exc)
                if isinstance(exc, ValueError)
                else "记忆库暂忙，请稍后刷新。",
            }

    @property
    def livingmemory_path(self):
        return (
            self.plugin.store.path.parent.parent
            / "astrbot_plugin_livingmemory"
            / "livingmemory.db"
        )

    async def image(self):
        try:
            scope = await self.scope(request.query.get("scope"))
            identity = request.query.get("id", "")
            if not identity or len(identity) > 128:
                raise ValueError("请选择有效的图片记忆。")

            def thumbnail():
                from PIL import Image

                directory = self.plugin.room_memory.directory / scope
                with closing(
                    sqlite3.connect(
                        (directory / "metadata/metadata.db").as_uri() + "?mode=ro",
                        uri=True,
                    )
                ) as db:
                    row = db.execute(
                        "SELECT storage_key FROM image_assets WHERE asset_id=? AND status='active'",
                        (identity,),
                    ).fetchone()
                if not row:
                    raise ValueError("图片记忆不存在或已删除。")
                from .image_memory import NativeImageAssetStore

                store = NativeImageAssetStore(
                    directory / "images/assets",
                    max_bytes=10 * 1024 * 1024,
                    max_pixels=40_000_000,
                )
                with Image.open(store.path_for_read(row[0])) as source:
                    source.thumbnail((640, 640))
                    output = io.BytesIO()
                    source.convert("RGB").save(output, format="JPEG", quality=85)
                return base64.b64encode(output.getvalue()).decode()

            return {
                "status": "ok",
                "data": {"encoded": await asyncio.to_thread(thumbnail)},
            }
        except (ValueError, OSError, sqlite3.Error):
            return {
                "status": "error",
                "message": "无法读取这张图片，可能已被删除或资源文件缺失。",
            }

    async def action(self):
        try:
            raw = await request.body()
            if len(raw) > 25 * 1024 * 1024:
                raise ValueError("请求过大，记忆包上限为 16 MB。")
            body = json.loads(raw)
            operation = body.get("action") if isinstance(body, dict) else None
            if operation not in ACTION_FIELDS:
                raise ValueError("未知的记忆管理操作。")
            fields = ACTION_FIELDS[operation]
            jsonschema.validate(
                body,
                {
                    "type": "object",
                    "required": ["scope", "action", "payload"],
                    "additionalProperties": False,
                    "properties": {
                        "scope": {"type": "string"},
                        "action": {"const": operation},
                        "payload": {
                            "type": "object",
                            "properties": fields,
                            "required": list(fields),
                            "additionalProperties": False,
                        },
                    },
                },
            )
            scope = await self.scope(body["scope"])
            if operation == "import_livingmemory":
                from .livingmemory_import import read_source

                body["payload"] = body["payload"] | {
                    "records": await asyncio.to_thread(
                        read_source, self.livingmemory_path, body["payload"]
                    )
                }
            result = await self.plugin.room_memory.call(
                scope, "admin", operation, body["payload"]
            )
            if not result.get("success", True):
                if operation == "delete_restore" and any(
                    result.get(key)
                    for key in (
                        "restored_paragraphs",
                        "restored_relations",
                        "restored_entities",
                    )
                ):
                    return {
                        "status": "ok",
                        "data": result
                        | {
                            "partial": True,
                            "message": "记忆正文已恢复，关联索引恢复尚未完成；可在删除管理中查看状态并继续恢复。",
                        },
                    }
                raise ValueError(
                    result.get("error")
                    or result.get("detail")
                    or "原生记忆操作未完成，请刷新记录后重试。"
                )
            return {"status": "ok", "data": result}
        except (jsonschema.ValidationError, json.JSONDecodeError):
            return {
                "status": "error",
                "message": "操作内容无效，请检查必填项和内容长度。",
            }
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        except Exception as exc:
            logger.warning("记忆管理操作未完成 (%s)", type(exc).__name__)
            return {
                "status": "error",
                "message": "原生记忆操作未完成，请刷新检查结果；不要重复提交不确定的写入。",
            }


async def execute_admin(state, operation, payload):
    """Explicit administrator writes, serialized by RoomMemory's native writer."""
    kernel = state.kernel
    identity = payload.get("id", "")
    actor = "astrbot_panel"
    reason = "Panel 管理员明确操作"
    if operation.startswith("emoji_"):
        from .emoji_panel import execute_emoji

        return await execute_emoji(state, operation, payload)
    if operation == "import_livingmemory":
        from .livingmemory_import import import_records

        return await import_records(state, payload)
    if operation.startswith("delete_"):
        action = operation.removeprefix("delete_")
        if action == "restore":
            return await kernel.memory_delete_admin(
                action="restore",
                operation_id=identity,
                requested_by=actor,
                reason=reason,
            )
        selector = (
            {"sources": [identity]}
            if payload["kind"] == "source"
            else {"hashes": [identity]}
        )
        return await kernel.memory_delete_admin(
            action=action,
            mode=payload["kind"],
            selector=selector,
            requested_by=actor,
            reason=reason,
        )
    if operation == "import_text":
        return await kernel.ingest_text(
            external_id=f"panel:{uuid4().hex}",
            source_type="memory",
            chat_id=state.path.name,
            text=payload["text"],
            timestamp=time.time(),
            metadata={"admin": actor},
        )
    if operation == "edit":
        service = kernel._correction_admin_service
        candidates = service._collect_selected_fuzzy_modify_candidates(
            [{"type": payload["kind"], "id": identity}]
        )
        plan = service._normalize_fuzzy_modify_plan(
            {
                "confidence": 1,
                "reason": reason,
                "operations": [
                    {"action": "mark_superseded", "hash": identity},
                    {
                        "action": "ingest_text",
                        "text": payload["text"],
                        "chat_id": state.path.name,
                        "source_type": "memory",
                    },
                ],
            },
            request_text=payload["text"],
            scope="memory",
            person_id="",
            chat_id=state.path.name,
            candidates=candidates,
        )
        saved = state.metadata.create_fuzzy_modify_plan(
            request_text=payload["text"],
            scope="memory",
            target_chat_id=state.path.name,
            plan=plan,
            status="awaiting_confirmation",
            confidence=1,
            requested_by=actor,
            reason=reason,
        )
        return await kernel.memory_correction_admin(
            action="execute",
            plan_id=saved["plan_id"],
            confirmed=True,
            requested_by=actor,
            reason=reason,
        )
    if operation == "rollback":
        return await kernel.memory_correction_admin(
            action="rollback", plan_id=identity, requested_by=actor, reason=reason
        )
    if operation in {"profile_save", "profile_clear", "profile_aliases"}:
        action = {
            "profile_save": "set_override",
            "profile_clear": "delete_override",
            "profile_aliases": "set_aliases",
        }[operation]
        values = (
            {"override_text": payload["text"]}
            if operation == "profile_save"
            else {"aliases": payload["aliases"]}
            if operation == "profile_aliases"
            else {}
        )
        return await kernel.memory_profile_admin(
            action=action, person_id=identity, updated_by=actor, **values
        )
    if operation == "image_forget":
        return await kernel.image_memory(
            action="delete_occurrence", occurrence_id=identity
        )
    if operation == "image_observe":
        return await kernel.image_memory(
            action="observe",
            occurrence_id=identity,
            text=payload["text"],
            supersedes_id=payload["previous"],
            source_kind="manual",
            confirm_status="confirmed",
            evidence={"admin": actor},
        )
    if operation.startswith("fact_"):
        action = {
            "fact_save": "update",
            "fact_retract": "retract",
            "fact_restore": "restore",
        }[operation]
        return await kernel.memory_fact_admin(
            action=action,
            claim_id=identity,
            **({"value_text": payload["text"]} if operation == "fact_save" else {}),
        )
    if operation in {"reinforce", "freeze", "protect"}:
        return await kernel.memory_v5_admin(
            action=operation, target=identity, hours=24, updated_by=actor, reason=reason
        )
    if operation in {"node_create", "node_rename", "edge_create"}:
        values = (
            {"name": payload["name"]}
            if operation == "node_create"
            else {"old_name": identity, "new_name": payload["name"]}
            if operation == "node_rename"
            else payload
        )
        return await kernel.memory_graph_admin(
            action={
                "node_create": "create_node",
                "node_rename": "rename_node",
                "edge_create": "create_edge",
            }[operation],
            **values,
            updated_by=actor,
            reason=reason,
        )
    if operation == "export_bundle":
        exported = await kernel.memory_bundle_admin(
            action="export",
            content_level="full",
            package={"name": "OvenHeart-memory", "version": str(int(time.time()))},
            selector={"type": "all"},
            include_images=True,
        )
        if exported.get("success"):
            path = Path(exported.pop("path"))
            if (await asyncio.to_thread(path.stat)).st_size > 16 * 1024 * 1024:
                raise ValueError(
                    "记忆包超过当前面板的 16 MB 下载上限，请缩小范围后导出。"
                )
            exported["encoded"] = await asyncio.to_thread(
                lambda: base64.b64encode(path.read_bytes()).decode()
            )
        return exported
    if operation == "import_bundle":
        data = base64.b64decode(payload["encoded"], validate=True)
        if len(data) > 16 * 1024 * 1024:
            raise ValueError("记忆包不能超过 16 MB。")
        path = state.path / "imports" / "bundles" / f"panel-{uuid4().hex}.amembundle"
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)
        return await kernel.memory_bundle_admin(
            action="import",
            path=str(path),
            mode="merge",
            scope_type="chat",
            chat_id=state.path.name,
        )
    raise ValueError("未知的记忆管理操作。")
