"""Resolve the person ledger's IDs using real native account identities."""

from .person_memory import person_identity


async def person_ids(plugin, scope, isolated):
    record = await plugin.store.read("scope_profile", scope)
    if record is None:
        return []
    platform = record["umo"].split(":", 1)[0]
    accounts = {
        (event.get_platform_id(), event.get_self_id())
        for event in plugin.events.values()
        if event.get_platform_id() == platform
    }
    accounts.update(
        (platform, bot) for bot in await plugin.store.read("native_accounts", platform)
    )
    origin = [record["umo"], scope] if isolated else ["global-memory"]
    return [
        row["person_id"]
        for row in await plugin.person_memory.call("panel_people")
        if any(
            row["person_id"]
            == person_identity(provider, bot, origin, record["persona"], row["user_id"])
            for provider, bot in accounts
        )
    ]
