"""Exercise native catalog SQL and cached semantic matching without a network."""

import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot_plugin_ovenheart.memory.emoji_retrieval import rank_candidates
from astrbot_plugin_ovenheart.memory.memory_embedding import EmbeddingAPIAdapter
from astrbot_plugin_ovenheart.memory.room_memory import RoomState


async def test_catalog_search_finds_old_images_and_nonconsecutive_words(tmp_path):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE image_assets (asset_id TEXT, status TEXT);
        CREATE TABLE image_occurrences (asset_id TEXT,occurrence_id TEXT,chat_id TEXT,status TEXT);
        CREATE TABLE image_observations (occurrence_id TEXT,text TEXT,superseded_by TEXT,evidence_json TEXT,created_at INT,observation_id TEXT);
    """)
    for index in range(70):
        identity = str(index)
        db.execute("INSERT INTO image_assets VALUES (?,'active')", (identity,))
        db.execute(
            "INSERT INTO image_occurrences VALUES (?,?,'scope','active')",
            (identity, identity),
        )
        db.execute(
            "INSERT INTO image_observations VALUES (?,?,'',?, ?,?)",
            (
                identity,
                "开心点头，表达感谢" if index == 0 else "皱眉困惑",
                '{"is_emoji":true}',
                index,
                identity,
            ),
        )
    state = RoomState(tmp_path / "scope", asyncio.Lock())
    state.metadata = SimpleNamespace(_conn=db)
    state.image_asset = AsyncMock(
        side_effect=lambda identity: {"success": True, "id": identity}
    )
    try:
        found = await state.image_candidates("开心 感谢")
        assert [row["id"] for row in found] == ["0"]
        assert not await state.image_candidates("完全无关内容")
        state.image_asset.assert_awaited_once()
    finally:
        db.close()


async def test_semantic_synonyms_use_existing_adapter_and_persist_document_vectors():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    calls = []

    async def transport(texts):
        calls.append(texts)
        return [[0.0, 1.0] if text == "冷漠无视" else [1.0, 0.0] for text in texts]

    provider = SimpleNamespace(
        provider_config={"embedding_model": "test"},
        get_model=lambda: "test",
        get_embeddings=transport,
    )
    context = SimpleNamespace(get_provider_by_id=lambda _: provider)
    state = SimpleNamespace(
        metadata=SimpleNamespace(_conn=db),
        embedding=EmbeddingAPIAdapter(context, "test", asyncio.get_running_loop()),
    )
    rows = [
        {"asset_id": "good", "caption": "恭喜祝贺"},
        {"asset_id": "bad", "caption": "冷漠无视"},
    ]
    try:
        found = await rank_candidates(state, [dict(row) for row in rows], "庆祝成功", 6)
        assert [row["asset_id"] for row in found] == ["good"]
        state.embedding = EmbeddingAPIAdapter(
            context, "test", asyncio.get_running_loop()
        )
        calls.clear()
        await rank_candidates(state, [dict(row) for row in rows], "庆祝成功", 6)
        assert calls == [["庆祝成功"]]
        rows[0]["caption"] = "祝你顺利"
        calls.clear()
        await rank_candidates(state, [dict(row) for row in rows], "庆祝成功", 6)
        assert calls == [["祝你顺利"]]
    finally:
        db.close()
