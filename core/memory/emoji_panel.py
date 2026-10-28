"""Administrator emoji library backed by the existing native image store."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time

import jsonschema
from astrbot.api import logger
from astrbot.api.web import request

from ..settings import SHARED_SCOPE
from .memory_panel import MemoryPanel, read_console

FIELDS = {
    "emoji_upload": {
        "encoded": {"type": "string", "minLength": 1, "maxLength": 14 * 1024 * 1024},
        "text": {"type": "string", "minLength": 1, "maxLength": 1000},
    },
    "emoji_caption": {
        "id": {"type": "string", "minLength": 1, "maxLength": 128},
        "text": {"type": "string", "minLength": 1, "maxLength": 1000},
    },
    "emoji_remove": {"id": {"type": "string", "minLength": 1, "maxLength": 128}},
    "emoji_import": {
        "pack": {"type": "string", "pattern": r"^[A-Za-z0-9_-]{1,80}$"},
        "offset": {"type": "integer", "minimum": 0, "maximum": 100000},
    },
}


def meme_source(directory, pack=None, offset=0):
    if pack is None:
        return (
            {
                "packs": [
                    {
                        "id": p.name,
                        "count": len(
                            json.loads(
                                (p / "semantic_metadata.json").read_text(
                                    encoding="utf-8"
                                )
                            )["images"]
                        ),
                    }
                    for p in sorted(directory.iterdir())
                    if p.is_dir()
                    and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", p.name)
                    and (p / "semantic_metadata.json").is_file()
                ]
            }
            if directory.is_dir()
            else {"packs": []}
        )
    folder = (directory / pack).resolve()
    if (
        folder.parent != directory.resolve()
        or not (folder / "semantic_metadata.json").is_file()
    ):
        raise ValueError("表情来源不存在，请重新读取图库。")
    data = json.loads((folder / "semantic_metadata.json").read_text(encoding="utf-8"))[
        "images"
    ]
    rows = []
    for key, row in sorted(data.items())[offset : offset + 3]:
        path = (folder / row["relative_path"]).resolve()
        if (
            not path.is_relative_to(folder / "memes")
            or not path.is_file()
            or path.stat().st_size > 10 * 1024 * 1024
        ):
            raise ValueError("来源图片缺失、路径无效或超过 10 MB；已导入部分会保留。")
        caption = (
            row.get("manual_caption")
            or row.get("caption")
            or row.get("category")
            or "表情包"
        )
        if not isinstance(caption, str):
            raise ValueError("来源描述格式无效，请在原图库中修正后重试。")
        rows.append(
            {
                "bytes": path.read_bytes(),
                "text": caption[:1000],
                "source": f"meme_manager:{pack}:{key}",
            }
        )
    return {
        "records": rows,
        "offset": offset + len(rows),
        "done": offset + len(rows) >= len(data),
    }


class EmojiPanel(MemoryPanel):
    async def scope(self, value):
        return SHARED_SCOPE if value == "library" else await super().scope(value)

    @property
    def packs(self):
        return self.plugin.store.path.parent.parent / "meme_manager/packs"

    async def read(self):
        if request.query.get("view") == "packs":
            try:
                return {
                    "status": "ok",
                    "data": await asyncio.to_thread(meme_source, self.packs),
                }
            except (OSError, ValueError, KeyError, TypeError):
                return {
                    "status": "error",
                    "message": "meme_manager 图库元数据无法读取，请检查来源是否完整。",
                }
        try:
            scope = await self.scope(request.query.get("scope", "library"))
            query = request.query.get("query", "").strip()
            offset = int(request.query.get("offset", 0))
            if len(query) > 500 or not 0 <= offset <= 100000:
                raise ValueError("请缩短关键词或重置页码。")
            result = await asyncio.to_thread(
                read_console,
                self.plugin.room_memory.directory / await self.plugin.room_memory.storage_scope(scope),
                "emojis",
                "image",
                query,
                offset,
                False,
                "",
                None,
                None,
            )
            return {"status": "ok", "data": result}
        except (ValueError, OSError) as exc:
            return {
                "status": "error",
                "message": str(exc)
                if isinstance(exc, ValueError)
                else "图库暂不可读，请稍后刷新。",
            }

    async def action(self):
        try:
            raw = await request.body()
            if len(raw) > 15 * 1024 * 1024:
                raise ValueError("图片上限为 10 MB。")
            body = json.loads(raw)
            action = body.get("action") if isinstance(body, dict) else None
            if action not in FIELDS:
                raise ValueError("未知的表情操作。")
            fields = FIELDS[action]
            jsonschema.validate(
                body,
                {
                    "type": "object",
                    "required": ["action", "scope", "payload"],
                    "additionalProperties": False,
                    "properties": {
                        "action": {"const": action},
                        "scope": {"const": "library"},
                        "payload": {
                            "type": "object",
                            "properties": fields,
                            "required": list(fields),
                            "additionalProperties": False,
                        },
                    },
                },
            )
            payload = body["payload"]
            if action == "emoji_import":
                payload = await asyncio.to_thread(
                    meme_source, self.packs, payload["pack"], payload["offset"]
                )
            result = await self.plugin.room_memory.call(
                SHARED_SCOPE, "admin", action, payload
            )
            return {"status": "ok", "data": result}
        except (jsonschema.ValidationError, json.JSONDecodeError, TypeError):
            return {"status": "error", "message": "表情操作内容无效，请检查必填项。"}
        except (ValueError, OSError, KeyError) as exc:
            return {
                "status": "error",
                "message": str(exc)
                if isinstance(exc, ValueError)
                else "导入来源不完整，请检查文件后重试；已导入记录会保留。",
            }
        except Exception as exc:
            logger.warning("表情管理未完成 (%s)", type(exc).__name__)
            return {
                "status": "error",
                "message": "表情操作未完成，请刷新图库检查结果。",
            }


async def execute_emoji(state, operation, payload):
    if operation in {"emoji_remove", "emoji_caption"}:
        result = await state.image_asset(payload["id"])
        if not result["success"]:
            raise ValueError("该表情已删除，请刷新图库。")
        for occurrence in result["occurrences"]:
            if operation == "emoji_remove":
                await state.kernel.image_memory(
                    action="delete_occurrence",
                    occurrence_id=occurrence["occurrence_id"],
                )
            else:
                observations = [
                    row
                    for row in result["observations"]
                    if row["occurrence_id"] == occurrence["occurrence_id"]
                    and not row["superseded_by"]
                ]
                await state.kernel.image_memory(
                    action="observe",
                    occurrence_id=occurrence["occurrence_id"],
                    text=payload["text"],
                    source_kind="manual",
                    confirm_status="confirmed",
                    evidence={"is_emoji": True, "admin": "astrbot_panel"},
                    supersedes_id=observations[-1]["observation_id"]
                    if observations
                    else "",
                )
        return {"success": True}
    records = (
        payload["records"]
        if operation == "emoji_import"
        else [
            {
                "bytes": base64.b64decode(payload["encoded"], validate=True),
                "text": payload["text"],
                "source": "panel",
            }
        ]
    )
    for row in records:
        if not row["text"].strip():
            raise ValueError("请填写表情含义或适用场景。")
        result = await state.kernel.image_memory(
            action="ingest",
            image_bytes=row["bytes"],
            source_kind="manual",
            external_ref="emoji:" + hashlib.sha256(row["bytes"]).hexdigest(),
            scope_type="chat",
            chat_id=SHARED_SCOPE,
            message_id="",
            occurred_at=time.time(),
        )
        if not result.get("success"):
            raise ValueError(result.get("error") or "图片入库未完成。")
        current = await state.image_asset(result["asset_id"])
        if not any(
            json.loads(o["evidence_json"]).get("is_emoji")
            for o in current["observations"]
            if not o["superseded_by"]
        ):
            await state.kernel.image_memory(
                action="observe",
                occurrence_id=result["occurrence_id"],
                text=row["text"],
                source_kind="manual",
                confirm_status="confirmed",
                evidence={
                    "is_emoji": True,
                    "admin": "astrbot_panel",
                    "import_source": row["source"],
                },
            )
    return {
        "success": True,
        "processed": len(records),
        **{key: payload[key] for key in ("offset", "done") if key in payload},
    }
