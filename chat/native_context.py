"""Read native conversation metadata and the host's enabled clock."""

import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


async def conversation_metadata(manager, umo, cid, *, history_state=False):
    """Avoid decoding and serializing the transcript for scope/epoch checks."""
    database = getattr(manager, "db", None)
    if not callable(getattr(database, "get_db", None)):
        native = await manager.get_conversation(umo, cid)
        if native is None:
            return None
        return SimpleNamespace(
            persona_id=getattr(native, "persona_id", None),
            created_at=getattr(native, "created_at", 0),
            has_history=bool(json.loads(native.history or "[]"))
            if history_state
            else None,
        )

    from astrbot.core.db.po import ConversationV2
    from astrbot.core.utils.datetime_utils import to_utc_timestamp
    from sqlalchemy import func, select

    columns = [ConversationV2.persona_id, ConversationV2.created_at]
    if history_state:
        columns.append(
            (
                func.coalesce(func.json_array_length(ConversationV2.content), 0) > 0
            ).label("has_history")
        )
    async with database.get_db() as session:
        row = (
            await session.execute(
                select(*columns).where(ConversationV2.conversation_id == cid)
            )
        ).one_or_none()
    if row is None:
        return None
    return SimpleNamespace(
        persona_id=row.persona_id,
        created_at=to_utc_timestamp(row.created_at) or 0,
        has_history=row.has_history if history_state else None,
    )


def native_time(context, umo):
    config = context.get_config(umo)
    if not config.get("provider_settings", {}).get("datetime_system_prompt"):
        return ""
    timezone = config.get("timezone") or context.get_config().get("timezone")
    try:
        now = (
            datetime.now(ZoneInfo(timezone))
            if timezone
            else datetime.now().astimezone()
        )
    except (ZoneInfoNotFoundError, ValueError):
        now = datetime.now().astimezone()
    weekday = (
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    )[now.weekday()]
    return (
        f"Current datetime: {now.strftime('%Y-%m-%d %H:%M (%Z)')}, Weekday: {weekday}"
    )
