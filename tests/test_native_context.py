"""Metadata checks use native rows without decoding a whole transcript."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot.core.db.po import ConversationV2
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from astrbot_plugin_ovenheart.native_context import conversation_metadata
from tests.test_runtime import event

pytest_plugins = ["tests.test_runtime"]


async def test_scope_metadata_does_not_deserialize_history_and_follows_persona_changes(
    plugin, tmp_path
):
    engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "native.db"))
    sessions = async_sessionmaker(engine)
    manager = plugin.context.conversation_manager
    manager.db = SimpleNamespace(get_db=sessions)
    manager.get_curr_conversation_id.return_value = "native-metadata"
    manager.get_conversation = AsyncMock(
        side_effect=AssertionError("full transcript loaded")
    )
    try:
        async with engine.begin() as db:
            await db.run_sync(ConversationV2.__table__.create)
        async with sessions() as db:
            db.add(
                ConversationV2(
                    conversation_id="native-metadata",
                    platform_id="test",
                    user_id="123",
                    persona_id="first",
                    content=[],
                )
            )
            await db.commit()
            await db.execute(
                text("UPDATE conversations SET content=:content"),
                {"content": "private-undecodable-transcript"},
            )
            await db.commit()
        incoming = event("metadata-check")
        await plugin.resolve_scope(incoming)
        assert (
            plugin.context.persona_manager.resolve_selected_persona.call_args.kwargs[
                "conversation_persona_id"
            ]
            == "first"
        )
        async with sessions() as db:
            await db.execute(
                text("UPDATE conversations SET persona_id='changed',content='[]'")
            )
            await db.commit()
        await plugin.resolve_scope(incoming)
        assert (
            plugin.context.persona_manager.resolve_selected_persona.call_args.kwargs[
                "conversation_persona_id"
            ]
            == "changed"
        )
        metadata = await conversation_metadata(
            manager, incoming.unified_msg_origin, "native-metadata", history_state=True
        )
        assert metadata.created_at > 0 and not metadata.has_history
        async with sessions() as db:
            await db.execute(
                text("UPDATE conversations SET content=:content"),
                {"content": '[{"role":"user","content":"private-history"}]'},
            )
            await db.commit()
        assert (
            await conversation_metadata(
                manager,
                incoming.unified_msg_origin,
                "native-metadata",
                history_state=True,
            )
        ).has_history
        await plugin.resolve_scope(
            incoming,
            SimpleNamespace(conversation=SimpleNamespace(persona_id="request-persona")),
        )
        assert (
            plugin.context.persona_manager.resolve_selected_persona.call_args.kwargs[
                "conversation_persona_id"
            ]
            == "request-persona"
        )
        manager.get_conversation.assert_not_called()
    finally:
        await engine.dispose()
