"""Share the host's enabled clock with planning and isolated reply examples."""

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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
