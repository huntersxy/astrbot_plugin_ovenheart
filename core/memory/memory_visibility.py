"""SQL visibility for the native console and its mutation boundary."""


def visible_clause(kind, scope):
    """Keep soft-deleted records visible to their owner for audit and restore."""
    if not scope:
        return "1", ()
    paragraph = "(source=? OR json_extract(metadata,'$.chat_id')=? OR EXISTS(SELECT 1 FROM json_each(metadata,'$.chat_ids') WHERE value=?))"
    values = ("chat_summary:" + scope, scope, scope)
    if kind == "paragraph":
        return paragraph, values
    if kind == "entity":
        return (
            f"(json_extract(metadata,'$.chat_id')=? OR EXISTS(SELECT 1 FROM json_each(metadata,'$.chat_ids') WHERE value=?) OR hash IN (SELECT entity_hash FROM paragraph_entities WHERE paragraph_hash IN (SELECT hash FROM paragraphs WHERE {paragraph})))",
            (scope, scope, *values),
        )
    if kind == "relation":
        return (
            f"(json_extract(metadata,'$.chat_id')=? OR EXISTS(SELECT 1 FROM json_each(metadata,'$.chat_ids') WHERE value=?) OR source_paragraph IN (SELECT hash FROM paragraphs WHERE {paragraph}) OR hash IN (SELECT relation_hash FROM paragraph_relations WHERE paragraph_hash IN (SELECT hash FROM paragraphs WHERE {paragraph})))",
            (scope, scope, *values, *values),
        )
    if kind == "episode":
        return "source=?", ("chat_summary:" + scope,)
    if kind == "image":
        return (
            "EXISTS(SELECT 1 FROM image_occurrences o WHERE o.asset_id=image_assets.asset_id AND o.chat_id=?)",
            (scope,),
        )
    if kind == "correction":
        return (
            "(target_chat_id=? OR plan_id IN (SELECT id FROM ovenheart_operation_sources WHERE scope=?))",
            (scope, scope),
        )
    if kind == "delete":
        return (
            "operation_id IN (SELECT id FROM ovenheart_operation_sources WHERE scope=?)",
            (scope,),
        )
    if kind == "feedback":
        return "session_id=?", (scope,)
    return "0", ()


def assert_visible(state, operation, payload):
    if state.shared or not payload.get("id"):
        return
    from .memory_panel import SPECS

    identity = payload["id"]
    kind = payload.get("kind")
    if operation in {"reinforce", "freeze", "protect"}:
        kind = "relation"
    elif operation in {"rollback", "review_execute"}:
        kind = "correction"
    elif operation == "delete_restore":
        kind = "delete"
    elif operation in {"image_forget", "image_observe"}:
        if not state.metadata._conn.execute(
            "SELECT 1 FROM image_occurrences WHERE occurrence_id=? AND chat_id=?",
            (identity, state.scope),
        ).fetchone():
            raise ValueError("图片记录不属于当前会话。")
        return
    elif operation == "node_rename":
        kind = "entity"
        row = state.metadata._conn.execute(
            "SELECT hash FROM entities WHERE name=?", (identity,)
        ).fetchone()
        identity = row[0] if row else ""
    if kind == "source":
        kind = "paragraph"
        field = "source"
    elif kind in SPECS:
        field = SPECS[kind][1]
    else:
        return
    table = SPECS[kind][0]
    clause, values = visible_clause(kind, state.scope)
    if not state.metadata._conn.execute(
        f'SELECT 1 FROM "{table}" WHERE "{field}"=? AND ({clause})', (identity, *values)
    ).fetchone():
        raise ValueError("这条记忆不属于当前会话的可见范围。")
    if kind == "entity":
        clause, values = visible_clause("paragraph", state.scope)
        if state.metadata._conn.execute(
            f"SELECT 1 FROM paragraph_entities WHERE entity_hash=? AND paragraph_hash IN (SELECT hash FROM paragraphs WHERE NOT {clause}) LIMIT 1",
            (identity, *values),
        ).fetchone():
            raise ValueError("该实体由多个会话共用，请关闭记忆隔离后再执行全局修改。")
    if kind == "paragraph" and operation in {"edit", "delete_execute"}:
        row = state.metadata.get_paragraph(identity)
        if row and any(
            value != state.scope
            for value in (row.get("metadata") or {}).get("chat_ids", [])
        ):
            raise ValueError("该记忆由多个会话共用，请关闭记忆隔离后再修改。")


def record_operation(state, result):
    if not isinstance(result, dict):
        return
    for key in ("operation_id", "plan_id", "change_id"):
        if result.get(key):
            state.metadata._conn.execute(
                "INSERT OR IGNORE INTO ovenheart_operation_sources VALUES (?,?)",
                (str(result[key]), state.scope),
            )
    state.metadata._conn.commit()


def add_scope(metadata, scope, previous=None):
    previous = previous or {}
    scopes = [
        *previous.get("chat_ids", []),
        *metadata.get("chat_ids", []),
        previous.get("chat_id"),
        scope,
    ]
    return metadata | {
        "chat_ids": list(dict.fromkeys(value for value in scopes if value))
    }


def scoped_metadata(metadata, scope):
    if not scope:
        return metadata
    result = metadata | {"chat_ids": [scope]}
    if "evidence" in result:
        result["evidence"] = [
            entry for entry in result["evidence"] if entry.get("source_scope") == scope
        ]
        result["participants"] = sorted(
            {entry["sender"] for entry in result["evidence"]}
        )
    return result
