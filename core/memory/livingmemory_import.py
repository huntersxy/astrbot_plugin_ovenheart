"""Explicit, read-only transfer of LivingMemory text into the native SDK."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime

# Scope fields belong to LivingMemory's document metadata, not its vector index.
META = "CASE WHEN json_valid(metadata) THEN metadata ELSE '{}' END"
SESSION = f"COALESCE(json_extract({META},'$.session_id'),'')"
PERSONA = f"COALESCE(json_extract({META},'$.persona_id'),'')"
ELIGIBLE = (
    f"json_valid(metadata) AND json_type({META})='object' "
    f"AND typeof({SESSION})='text' AND length({SESSION})<=1024 "
    f"AND typeof({PERSONA})='text' AND length({PERSONA})<=128 "
    "AND length(trim(text)) BETWEEN 1 AND 30000"
)


def read_source(path, selection=None):
    if not path.is_file():
        if selection is not None:
            raise ValueError(
                "未找到 LivingMemory 记忆库，请将其数据目录放入 AstrBot 的 plugin_data 后重新读取。"
            )
        return {
            "available": False,
            "groups": [],
            "message": "未找到 LivingMemory 记忆库；无需启用该插件。",
        }
    try:
        with closing(
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        ) as db:
            db.row_factory = sqlite3.Row
            if selection is None:
                groups = [
                    dict(row)
                    for row in db.execute(
                        f"SELECT {SESSION} AS session,{PERSONA} AS persona,COUNT(*) AS count,MAX(id) AS until "
                        f"FROM documents WHERE {ELIGIBLE} GROUP BY {SESSION},{PERSONA} ORDER BY MAX(id) DESC"
                    )
                ]
                excluded = db.execute(
                    f"SELECT COUNT(*) FROM documents WHERE NOT ({ELIGIBLE})"
                ).fetchone()[0]
                return {"available": True, "groups": groups, "excluded": excluded}
            rows = db.execute(
                f"SELECT id,doc_id,text,metadata,created_at FROM documents WHERE {ELIGIBLE} "
                f"AND {SESSION}=? AND {PERSONA}=? AND id>? AND id<=? ORDER BY id LIMIT 3",
                (
                    selection["session"],
                    selection["persona"],
                    selection["after"],
                    selection["until"],
                ),
            ).fetchall()
            records = []
            for row in rows:
                metadata = json.loads(row["metadata"])
                records.append(
                    {
                        "id": row["id"],
                        "external_id": f"livingmemory:{row['doc_id']}",
                        "text": row["text"].strip(),
                        "timestamp": datetime.fromisoformat(
                            row["created_at"]
                        ).timestamp()
                        if row["created_at"]
                        else None,
                        "metadata": {
                            "admin": "astrbot_panel",
                            "import_source": "LivingMemory",
                            "source_record_id": row["doc_id"],
                            "source_session": selection["session"],
                            "source_persona": selection["persona"],
                            "source_metadata": metadata,
                        },
                    }
                )
            return records
    except (sqlite3.Error, ValueError, TypeError) as exc:
        raise ValueError(
            "LivingMemory 数据库无法读取：需要有效的 documents 文本表与来源元数据，请检查数据库是否完整、可读。"
        ) from exc


async def import_records(state, payload):
    imported = skipped = 0
    cursor = payload["after"]
    for record in payload["records"]:
        result = await state.kernel.ingest_text(
            external_id=record["external_id"],
            source_type="memory",
            chat_id=state.scope,
            text=record["text"],
            timestamp=record["timestamp"],
            metadata=record["metadata"],
        )
        if not result.get("success", True):
            raise ValueError(
                "本批导入未完成，已写入的记录会保留；重新导入会自动跳过重复来源。"
            )
        imported += bool(result.get("stored_ids"))
        skipped += bool(result.get("skipped_ids"))
        cursor = record["id"]
    return {
        "success": True,
        "imported": imported,
        "skipped": skipped,
        "after": cursor,
        "done": len(payload["records"]) < 3 or cursor >= payload["until"],
    }
