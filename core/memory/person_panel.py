"""Resolve person ledger IDs over the same native source visibility as recall."""

from .person_memory import person_identity


async def person_ids(plugin, scope, isolated):
    record = await plugin.store.read("scope_profile", scope)
    if record is None:
        return []
    scopes = (
        [record | {"id": scope}]
        if isolated
        else await plugin.store.read("memory_scopes", record["persona"])
    )
    origins = []
    for row in scopes:
        platform = row["umo"].split(":", 1)[0]
        bots = {
            event.get_self_id()
            for event in plugin.events.values()
            if event.get_platform_id() == platform
        }
        bots.update(await plugin.store.read("native_accounts", platform))
        for bot in bots:
            origins.append((platform, bot, [row["umo"], row["id"]]))
            if not isolated:
                origins.append((platform, bot, ["global-memory"]))
    return [
        row["person_id"]
        for row in await plugin.person_memory.call("panel_people")
        if any(
            row["person_id"]
            == person_identity(platform, bot, origin, record["persona"], row["user_id"])
            for platform, bot, origin in origins
        )
    ]
