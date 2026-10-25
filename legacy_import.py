"""Read-only LivingMemory snapshots imported into the original native SDK."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import math
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from astrbot.core.star.session_plugin_manager import SessionPluginManager


@dataclass(frozen=True, slots=True)
class LegacyUnit:
    external_id: str
    namespace: str
    parent_id: int
    content: str
    created: float
    expires: float | None
    entities: list[str]
    relations: list[dict]
    metadata: dict


def source_namespace(path):
    return hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()[:20]


def read_legacy(path, namespace, source_scopes, persona, snapshot=""):
    """The source boundary validates values once; internal units are typed."""
    with closing(
        sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    ) as db:
        db.row_factory = sqlite3.Row
        tables = {
            r[0]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        columns = {r[1] for r in db.execute("PRAGMA table_info(documents)")}
        if not {"id", "text", "metadata"} <= columns:
            raise ValueError("LivingMemory记忆表结构不匹配，未执行导入。")
        marks = ",".join("?" for _ in source_scopes)
        rows = db.execute(
            f"SELECT id,text,metadata FROM documents WHERE json_extract(metadata,'$.session_id') IN ({marks}) AND COALESCE(json_extract(metadata,'$.persona_id'),'')=? AND COALESCE(json_extract(metadata,'$.status'),'active')='active' ORDER BY id",
            (*source_scopes, "" if persona == "[%None]" else persona),
        ).fetchall()
        result, excluded = [], 0
        now = time.time()
        for row in rows:
            parent = int(row["id"])
            metadata = json.loads(row["metadata"] or "{}")
            source_row = (
                db.execute(
                    "SELECT source_json FROM memory_sources WHERE memory_id=?",
                    (parent,),
                ).fetchone()
                if "memory_sources" in tables
                else None
            )
            sources = json.loads(source_row[0]) if source_row else []
            if not isinstance(metadata, dict) or not isinstance(sources, list):
                raise ValueError(f"旧记忆{parent}的元数据或来源结构不正确。")
            excerpts = []
            for message in sources[:12]:
                if not isinstance(message, dict) or not isinstance(
                    message.get("content"), str
                ):
                    raise ValueError(f"旧记忆{parent}包含无法识别的来源消息。")
                excerpts.append(
                    {
                        "source_id": f"livingmemory:{namespace}:message:{message.get('id', '')}",
                        "sender": str(message.get("sender_id") or "unknown"),
                        "role": str(message.get("role") or "user"),
                        "quote": message["content"][:160],
                        "session_id": str(message.get("session_id") or ""),
                        "timestamp": message.get("timestamp"),
                    }
                )
            atoms = (
                db.execute(
                    "SELECT * FROM memory_atoms WHERE parent_memory_id=? ORDER BY id",
                    (parent,),
                ).fetchall()
                if "memory_atoms" in tables
                else []
            )
            live = [
                atom
                for atom in atoms
                if atom["status"] == "active" and float(atom["expires_at"]) > now
            ]
            excluded += len(atoms) - len(live)
            if atoms or metadata.get("atom_types"):
                units = [
                    (
                        f"atom:{atom['id']}",
                        atom["content"],
                        atom["created_at"],
                        float(atom["expires_at"]),
                        json.loads(atom["entities"] or "[]"),
                        atom["atom_type"],
                    )
                    for atom in live
                ]
            else:
                units = [
                    (
                        "document",
                        row["text"],
                        metadata.get("create_time", 0),
                        None,
                        [],
                        "document",
                    )
                ]
            relations = []
            if (
                not atoms
                and not metadata.get("atom_types")
                and {"graph_edges", "graph_nodes"} <= tables
            ):
                relations = [
                    {"subject": r[0], "predicate": r[1], "object": r[2]}
                    for r in db.execute(
                        "SELECT s.node_value,e.relation_type,t.node_value FROM graph_edges e JOIN graph_nodes s ON s.id=e.source_node_id JOIN graph_nodes t ON t.id=e.target_node_id WHERE e.source_memory_id=? AND e.status='active'",
                        (parent,),
                    )
                ]
            for identifier, content, created, expires, entities, kind in units:
                if (
                    not isinstance(content, str)
                    or not content.strip()
                    or not isinstance(entities, list)
                    or any(not isinstance(entity, str) for entity in entities)
                ):
                    raise ValueError(f"旧记忆{parent}的有效内容或实体字段不正确。")
                created = float(created or 0)
                if (
                    not math.isfinite(created)
                    or expires is not None
                    and not math.isfinite(expires)
                ):
                    raise ValueError(f"旧记忆{parent}包含无效时间。")
                origin = {
                    "parent_id": parent,
                    "unit": identifier,
                    "namespace": namespace,
                    "scope": metadata.get("session_id"),
                    "persona": metadata.get("persona_id"),
                    "kind": kind,
                    "source_count": len(sources),
                    "snapshot": snapshot,
                }
                result.append(
                    LegacyUnit(
                        f"livingmemory:{namespace}:memory:{parent}:{identifier}",
                        namespace,
                        parent,
                        content.strip(),
                        created,
                        expires,
                        entities,
                        relations,
                        {
                            "legacy_import": origin,
                            "legacy_sources": excerpts,
                            "participants": [],
                            "evidence": [],
                        },
                    )
                )
        return {
            "documents": len(rows),
            "units": len(result),
            "excluded_atoms": excluded,
            "source_scopes": list(source_scopes),
            "persona": persona,
        }, result


def backup_legacy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (
        closing(
            sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True)
        ) as original,
        closing(sqlite3.connect(destination)) as snapshot,
    ):
        original.backup(snapshot)


def native_preview(path, units):
    from .vendor.maibot.amemorix.utils.hash import compute_hash, normalize_text

    counts = {"ready": 0, "skipped": 0, "conflicts": 0}
    if not path.exists():
        return counts | {"ready": len(units)}
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        ledger = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ovenheart_legacy_imports'"
        ).fetchone()
        for unit in units:
            hashed = compute_hash(normalize_text(unit.content))
            previous = (
                db.execute(
                    "SELECT content_hash FROM ovenheart_legacy_imports WHERE external_id=?",
                    (unit.external_id,),
                ).fetchone()
                if ledger
                else None
            )
            if previous:
                counts["skipped" if previous[0] == hashed else "conflicts"] += 1
            elif db.execute(
                "SELECT 1 FROM paragraphs WHERE hash=?", (hashed,)
            ).fetchone():
                counts["skipped"] += 1
            else:
                counts["ready"] += 1
    return counts


class LivingMemoryImport:
    def __init__(self, plugin):
        self.plugin = plugin
        self.busy = {}
        self.status = {}
        self.pending = set()
        self.launching = {}
        self.directory = plugin.room_memory.directory.parent / "livingmemory_imports"

    async def restore(self):
        self.pending = set(await self.plugin.store.call("legacy_import_queue"))
        for scope in self.pending:
            self.status[scope] = {
                "state": "waiting_source",
                "message": "已保存迁入请求；等待当前会话下一条真实且获准的消息。",
            }

    def observe(self, scope):
        if scope in self.pending and scope not in self.busy:
            self.pending.remove(scope)
            self.launching[scope] = self.plugin.track(self.resume(scope))

    async def resume(self, scope):
        try:
            await self.start(scope)
        except Exception as exc:
            self.status[scope] = {
                "state": "error",
                "message": str(exc)
                if isinstance(exc, ValueError)
                else "待迁入请求未能启动（" + type(exc).__name__ + "），请重新预览。",
            }
            await self.plugin.store.call("legacy_import_queue", scope, False)
        finally:
            self.launching.pop(scope, None)

    async def cancel(self, scope):
        self.pending.discard(scope)
        await self.plugin.store.call("legacy_import_queue", scope, False)
        task = self.busy.get(scope) or self.launching.get(scope)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.status[scope] = {
            "state": "cancelled",
            "message": "已取消待迁入请求；已成功写入的原生记忆和原库保留。",
        }
        return self.status[scope]

    async def access(self, event, scope, persona):
        if not await self.plugin.room_memory.permitted(event, scope, persona, True):
            raise ValueError("当前会话、人格或学习权限不允许导入记忆。")
        meta = self.plugin.context.get_registered_star("astrbot_plugin_livingmemory")
        owner = meta.star_cls if meta and meta.activated else None
        initializer = getattr(owner, "initializer", None)
        if not initializer or not initializer.is_initialized:
            raise ValueError("LivingMemory尚未启用或初始化完成。")
        if not await SessionPluginManager.is_plugin_enabled_for_session(
            event.unified_msg_origin, "astrbot_plugin_livingmemory"
        ):
            raise ValueError("当前会话已停用LivingMemory，不读取其旧记忆。")
        enabled = self.plugin.context.get_config(event.unified_msg_origin).get(
            "plugin_set", ["*"]
        )
        if enabled != ["*"] and "astrbot_plugin_livingmemory" not in enabled:
            raise ValueError("当前宿主配置不允许访问LivingMemory。")
        policy = importlib.import_module(
            type(owner).__module__.rsplit(".", 1)[0] + ".core.memory_scope"
        )
        if not policy.is_event_memory_allowed(owner.config_manager, event):
            raise ValueError("LivingMemory的访问规则不允许读取此会话。")
        origin = policy.resolve_memory_scope(owner.config_manager, event)
        if (
            origin
            and origin.startswith("livingmemory:user:")
            and not event.is_private_chat()
        ):
            raise ValueError(
                "旧库按用户隔离，不能直接合并进共享会话记忆；原库保持可用。"
            )
        engine = owner.initializer.memory_engine
        if not event.is_private_chat() and policy._config_get(
            owner.config_manager, "access_control.whitelist_enabled", False
        ):
            allowed = {
                value.casefold()
                for value in policy.parse_value_list(
                    policy._config_get(
                        owner.config_manager, "access_control.allowed_ids", ""
                    )
                )
            }
            if (
                not {
                    event.unified_msg_origin.casefold(),
                    event.get_group_id().casefold(),
                }
                & allowed
            ):
                raise ValueError(
                    "旧记忆仅向部分成员开放，不能迁入共享会话库而扩大访问；原库保持可用。"
                )
        path = Path(engine.db_path)
        return (
            owner,
            path,
            tuple(
                dict.fromkeys([event.unified_msg_origin, *([origin] if origin else [])])
            ),
        )

    async def preview(self, scope):
        if scope in self.busy:
            raise ValueError("当前会话正在导入，请查看进度，完成后可重新预览。")
        if scope in self.pending:
            raise ValueError("当前会话已排队等待真实消息；可先取消，再重新预览。")
        event = self.plugin.events.get(scope)
        if event is None:
            raise ValueError("当前会话缺少近期真实来源，请收到新消息后重试。")
        actual, persona, _ = await self.plugin.resolve_scope(event)
        if actual != scope:
            raise ValueError("当前人格归属已变化，请刷新后重试。")
        _, path, scopes = await self.access(event, scope, persona)
        overview, units = await asyncio.to_thread(
            read_legacy, path, source_namespace(path), scopes, persona
        )
        overview["native"] = await asyncio.to_thread(
            native_preview,
            self.plugin.room_memory.directory / scope / "metadata/metadata.db",
            units,
        )
        self.status[scope] = overview | {"state": "preview"}
        return overview

    async def start(self, scope):
        if scope in self.busy:
            raise ValueError("当前会话已有记忆导入任务。")
        event = self.plugin.events.get(scope)
        if event is None:
            await self.plugin.store.call("legacy_import_queue", scope)
            self.pending.add(scope)
            self.status[scope] = {
                "state": "waiting_source",
                "message": "已保存迁入请求；等待当前会话下一条真实且获准的消息。",
            }
            return self.status[scope]
        actual, persona, _ = await self.plugin.resolve_scope(event)
        if actual != scope:
            raise ValueError("当前人格归属已变化，请刷新后重试。")
        owner, path, scopes = await self.access(event, scope, persona)
        await self.plugin.store.call("legacy_import_queue", scope)
        self.pending.discard(scope)
        self.status[scope] = {
            "state": "running",
            "processed": 0,
            "stored": 0,
            "skipped": 0,
            "conflicts": 0,
        }
        self.busy[scope] = self.plugin.track(
            self.run(event, scope, persona, owner, path, scopes)
        )
        return self.status[scope]

    async def run(self, event, scope, persona, owner, path, scopes):
        status = self.status[scope]
        revision = self.plugin.settings_revision
        namespace = source_namespace(path)
        snapshot = self.directory / (
            time.strftime("%Y%m%d-%H%M%S", time.gmtime())
            + "-"
            + uuid4().hex[:8]
            + ".sqlite3"
        )
        try:
            await asyncio.to_thread(backup_legacy, path, snapshot)
            overview, units = await asyncio.to_thread(
                read_legacy, snapshot, namespace, scopes, persona, snapshot.name
            )
            status.update(overview, snapshot=snapshot.name)
            if not overview["documents"]:
                status["state"] = "empty"
                return
            for index, unit in enumerate(units):
                if revision != self.plugin.settings_revision:
                    raise ValueError(
                        "导入期间配置已变化，已停止；可重复执行并跳过已有记录。"
                    )
                if index % 25 == 0 and await self.access(event, scope, persona) != (
                    owner,
                    path,
                    scopes,
                ):
                    raise ValueError("旧插件实例或来源规则已变化，已停止导入。")
                result = await self.plugin.room_memory.call(
                    scope, "legacy_ingest", unit, event=event
                )
                status[result] += 1
                status["processed"] += 1
            if revision != self.plugin.settings_revision or await self.access(
                event, scope, persona
            ) != (owner, path, scopes):
                raise ValueError("导入结束前权限或来源已变化，未切换旧记忆钩子。")
            if status["conflicts"]:
                status["state"] = "completed_with_conflicts"
                return
            await self.plugin.room_memory.call(
                scope, "legacy_finish", namespace, scopes, snapshot.name, event=event
            )
            status["state"] = "completed"
            status["legacy_hooks_bound"] = self.plugin.memory.install()
        except asyncio.CancelledError:
            status["state"] = "cancelled"
            raise
        except Exception as exc:
            status.update(
                state="error",
                message=str(exc)
                if isinstance(exc, ValueError)
                else "记忆导入未完成（"
                + type(exc).__name__
                + "）；已有原生记录保留，可重复执行。",
            )
        finally:
            self.busy.pop(scope, None)
            if not self.plugin.closing:
                await self.plugin.store.call("legacy_import_queue", scope, False)

    async def retired(self, event):
        meta = self.plugin.context.get_registered_star("astrbot_plugin_livingmemory")
        if (
            not meta
            or not meta.activated
            or not getattr(meta.star_cls, "initializer", None)
            or not meta.star_cls.initializer.is_initialized
        ):
            return False
        owner = meta.star_cls
        source_path = getattr(owner.initializer.memory_engine, "db_path", None)
        if not isinstance(source_path, (str, Path)):
            return False
        scope, _, _ = await self.plugin.resolve_scope(event)
        database = self.plugin.room_memory.directory / scope / "metadata/metadata.db"
        if not database.exists():
            return False
        try:
            policy = importlib.import_module(
                type(owner).__module__.rsplit(".", 1)[0] + ".core.memory_scope"
            )
        except ImportError:
            return False
        origin = policy.resolve_memory_scope(owner.config_manager, event)
        scopes = tuple(
            dict.fromkeys([event.unified_msg_origin, *([origin] if origin else [])])
        )
        namespace = source_namespace(source_path)
        return await asyncio.to_thread(self.read_retired, database, namespace, scopes)

    @staticmethod
    def read_retired(path, namespace, scopes):
        with closing(
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as db:
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ovenheart_legacy_imports'"
            ).fetchone():
                return False
            return all(
                db.execute(
                    "SELECT 1 FROM ovenheart_legacy_imports WHERE external_id=?",
                    (f"retired:{namespace}:{origin}",),
                ).fetchone()
                for origin in scopes
            )
