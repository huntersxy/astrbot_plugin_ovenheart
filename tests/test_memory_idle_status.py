"""Idle summary polling must not evict hot rooms or initialize vector indexes."""

import sqlite3
from types import SimpleNamespace

from astrbot_plugin_ovenheart.memory.room_memory import RoomMemory


async def test_progress_reads_across_cold_rooms_keep_hot_runtime_resident(
    tmp_path, monkeypatch
):
    plugin = SimpleNamespace(
        store=SimpleNamespace(path=tmp_path / "persona.sqlite3"),
        settings_revision=1,
        closing=False,
    )
    memory = RoomMemory(plugin)
    hot = object()
    memory.rooms["hot"] = hot

    def unexpected_runtime(*args, **kwargs):
        raise AssertionError("progress-only read started a full memory runtime")

    monkeypatch.setattr(
        "astrbot_plugin_ovenheart.memory.room_memory.RoomState", unexpected_runtime
    )
    for index in range(6):
        scope = f"cold-{index}"
        path = memory.directory / scope / "metadata" / "metadata.db"
        path.parent.mkdir(parents=True)
        with sqlite3.connect(path) as db:
            db.executescript("""
                CREATE TABLE ovenheart_summary_runtime(singleton INT, attempted REAL);
                INSERT INTO ovenheart_summary_runtime VALUES(1,123);
                CREATE TABLE ovenheart_summary_processed(source_id TEXT);
                INSERT INTO ovenheart_summary_processed VALUES('kept'),('pruned');
                CREATE TABLE episode_rebuild_sources(status TEXT);
                INSERT INTO episode_rebuild_sources VALUES('failed');
            """)
        for _ in range(2):
            status = await memory.call(scope, "summary_status", ["kept"])
            assert status == {
                "attempted": 123,
                "processed": {"kept"},
                "episode_pending": True,
            }
            assert dict(memory.rooms) == {"hot": hot}
    assert not (await memory.call("missing", "summary_status"))["processed"]
