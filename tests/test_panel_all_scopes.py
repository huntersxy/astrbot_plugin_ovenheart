"""Panel overview queries span sources without changing chat recall isolation."""

import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest

from core.memory import memory_panel
from core.memory.emoji_panel import EmojiPanel
from core.memory.memory_panel import (
    MemoryPanel,
    read_console,
    read_visible_console,
    storage_sources,
)
from core.memory.persona_store import persona_key
from core.settings import SHARED_SCOPE
from core.store import Store

A = "a" * 24
B = "b" * 24
C = "c" * 24
SOURCES = [
    {"id": A, "umo": "qq:GroupMessage:1", "persona": "one", "label": "First room"},
    {"id": B, "umo": "qq:GroupMessage:2", "persona": "one", "label": "Second room"},
    {"id": C, "umo": "qq:GroupMessage:3", "persona": "two", "label": "Third room"},
]


@pytest.fixture
async def store(tmp_path):
    instance = Store(tmp_path / "workshop.sqlite3")
    await instance.call("initialize")
    for row in SOURCES:
        await instance.call(
            "touch_scope", row["id"], row["umo"], row["persona"], row["label"]
        )
    yield instance
    await instance.close()


async def test_history_defaults_to_all_sources_with_real_record_owners(store):
    first = await store.call("start_run", A)
    second = await store.call("start_run", C)
    page = await store.read("panel_history", "all")
    assert page["total"] == page["counts"]["learning"] == 2
    assert [(row["id"], row["label"], row["persona"]) for row in page["items"]] == [
        (second, "Third room", "two"),
        (first, "First room", "one"),
    ]
    filtered = await store.read("panel_history", A)
    assert filtered["total"] == 1
    assert filtered["items"][0]["scope"] == A
    detail = await store.read("history_detail", C, "learning", second)
    assert detail["scope"] == C


async def test_persona_overview_and_local_source_filters(store):
    await store.call("self_portrait", "one", {"description": "Short silver hair."})
    await store.call(
        "self_portrait", "two", {"description": "A red coat.", "image": b"sample"}
    )
    portraits = await store.read("self_portraits")
    assert {row["persona"] for row in portraits} == {"one", "two"}
    assert all("image" not in row for row in portraits)
    panel = MemoryPanel(SimpleNamespace(store=store))
    assert len(await panel.sources()) == 3
    assert {row["id"] for row in await panel.sources("all", "one")} == {A, B}
    assert [row["id"] for row in await panel.sources(C)] == [C]
    with pytest.raises(ValueError, match="具体会话"):
        await panel.scope("all")
    emojis = EmojiPanel(SimpleNamespace(store=store))
    assert {row["id"] for row in await emojis.sources()} == {A, B, C, SHARED_SCOPE}


@pytest.fixture
def memory_files(tmp_path):
    owners = storage_sources(SOURCES)
    for key in owners:
        directory = tmp_path / key / "metadata"
        directory.mkdir(parents=True)
        with closing(sqlite3.connect(directory / "metadata.db")) as db:
            db.execute(
                "CREATE TABLE paragraphs(hash TEXT PRIMARY KEY,content TEXT,source TEXT,metadata TEXT,created_at REAL,is_deleted INTEGER DEFAULT 0,expires_at REAL)"
            )
            for index, row in enumerate(SOURCES, 1):
                if persona_key(row["persona"]) == key:
                    db.execute(
                        "INSERT INTO paragraphs(hash,content,source,metadata,created_at) VALUES (?,?,?,?,?)",
                        (
                            row["id"],
                            row["label"],
                            "chat_summary:" + row["id"],
                            json.dumps({"chat_ids": [row["id"]]}),
                            index,
                        ),
                    )
            db.commit()
    return tmp_path


def test_memory_overview_merges_persona_stores_once_and_preserves_sources(memory_files):
    owners = storage_sources(SOURCES)
    args = (
        memory_files,
        list(owners),
        "records",
        "paragraph",
        "",
        0,
        False,
        "",
        None,
        None,
    )
    result = read_visible_console(*args, sources=owners)
    assert result["total"] == result["stats"]["paragraph"] == 3
    assert [row["scope"] for row in result["items"]] == [C, B, A]
    assert len({(row["storage_scope"], row["id"]) for row in result["items"]}) == 3
    assert result["items"][0]["scope_label"] == "Third room"
    filtered = read_visible_console(*args, visible_scope=A, sources=owners)
    assert filtered["total"] == 1
    assert filtered["items"][0]["scope"] == A


async def test_import_sources_ignore_a_previous_fact_type_filter(
    store, memory_files, monkeypatch
):
    monkeypatch.setattr(
        memory_panel,
        "request",
        SimpleNamespace(query={"scope": "all", "view": "import", "kind": "fact"}),
    )
    plugin = SimpleNamespace(
        store=store,
        room_memory=SimpleNamespace(directory=memory_files),
        person_memory=SimpleNamespace(path=memory_files / "people"),
    )
    result = await MemoryPanel(plugin).read()
    assert result["status"] == "ok"
    assert result["data"]["total"] == 3
    assert {row["scope"] for row in result["data"]["items"]} == {A, B, C}


def test_emoji_filter_requires_an_emoji_occurrence_in_the_selected_room(tmp_path):
    directory = tmp_path / "metadata"
    directory.mkdir()
    with closing(sqlite3.connect(directory / "metadata.db")) as db:
        db.executescript("""
            CREATE TABLE image_assets(asset_id TEXT PRIMARY KEY,content_hash TEXT,storage_key TEXT,status TEXT,created_at REAL);
            CREATE TABLE image_occurrences(occurrence_id TEXT PRIMARY KEY,asset_id TEXT,chat_id TEXT,message_id TEXT,occurred_at REAL,status TEXT);
            CREATE TABLE image_observations(observation_id TEXT PRIMARY KEY,occurrence_id TEXT,text TEXT,confirm_status TEXT,superseded_by TEXT,evidence_json TEXT);
        """)
        db.execute(
            "INSERT INTO image_assets VALUES ('picture','hash','picture.png','active',1)"
        )
        for scope, emoji in ((A, False), (B, True)):
            db.execute(
                "INSERT INTO image_occurrences VALUES (?,'picture',?,'message',1,'active')",
                (scope, scope),
            )
            db.execute(
                "INSERT INTO image_observations VALUES (?,?,'Caption','confirmed','',?)",
                (scope, scope, json.dumps({"is_emoji": emoji})),
            )
        db.commit()
    args = (tmp_path, "emojis", "image", "", 0, False, "", None, None)
    assert read_console(*args)["total"] == 1
    assert read_console(*args, visible_scope=A)["total"] == 0
    assert read_console(*args, visible_scope=B)["total"] == 1
