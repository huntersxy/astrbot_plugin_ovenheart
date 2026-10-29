"""Resolve person ledger IDs over the same native source visibility as recall."""

from .person_memory import get_person_id


async def person_ids(plugin, scope, isolated):
    record = await plugin.store.read("scope_profile", scope)
    if record is None:
        return []
    scopes = (
        [record | {"id": scope}]
        if isolated
        else await plugin.store.read("memory_scopes", record["persona"])
    )
    senders = set(
        await plugin.store.call("person_visible_senders", [row["id"] for row in scopes])
    )
    if not senders:
        return []
    platforms = {
        event.get_platform_name()
        for row in scopes
        for event in plugin.events.values()
        if event.get_platform_id() == row["umo"].split(":", 1)[0] and event.get_platform_name()
    }
    people = await plugin.person_memory.call("panel_people")
    chosen = {}
    for row in people:
        user_id = row["user_id"]
        if user_id not in senders:
            continue
        score = 0
        if row.get("platform") and row["person_id"] == get_person_id(
            row["platform"], user_id
        ):
            score = 2
        elif any(
            row["person_id"] == get_person_id(name, user_id) for name in platforms
        ):
            score = 2
        elif row.get("platform"):
            score = 1
        previous = chosen.get(user_id)
        if previous is None or score > previous[0]:
            chosen[user_id] = (score, row["person_id"], row.get("platform") or "")
    nicknames = {
        row["user_id"]: row["user_nickname"] or row["user_id"]
        for row in people
        if row["user_id"] in chosen
    }
    result = []
    for user_id, (_, person_id, platform) in chosen.items():
        name = next(iter(platforms), platform)
        if name:
            canonical = get_person_id(name, user_id)
            await plugin.person_memory.call(
                "register",
                canonical,
                user_id,
                nicknames.get(user_id, user_id),
                name,
            )
            result.append(canonical)
        else:
            result.append(person_id)
    return list(dict.fromkeys(result))
