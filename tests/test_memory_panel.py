"""Use actual SDK stores for the administrator edit/delete/transfer lifecycle."""

import pytest

from astrbot_plugin_ovenheart.memory_panel import MemoryPanel, read_console
from tests.test_room_memory import SyntheticEmbedding, material

pytest_plugins = ["tests.test_room_memory"]


def read(plugin, scope, view="records", kind="paragraph", identity="", inactive=False):
    return read_console(
        plugin.room_memory.directory / scope,
        view,
        kind,
        "",
        0,
        inactive,
        identity,
        None,
        None,
    )


async def test_native_admin_lifecycle_and_scoped_bundle(memories):
    embedding = SyntheticEmbedding()
    memories.settings["embedding_provider_id"] = "panel-test-embedding"
    memories.context.get_provider_by_id = lambda name: embedding
    _, scope, _, _ = await material(memories)
    room = memories.room_memory
    await room.call(
        scope, "admin", "import_text", {"text": "面板导入：小猫喜欢温暖的纸箱。"}
    )
    original = read(memories, scope)["items"][0]
    assert original["text"] == "面板导入：小猫喜欢温暖的纸箱。"
    for view in (
        "records",
        "profiles",
        "images",
        "graph",
        "timeline",
        "import",
        "inspection",
        "delete",
    ):
        result = read(memories, scope, view)
        assert result["available"]
        assert "total" in result
    edited = await room.call(
        scope,
        "admin",
        "edit",
        {
            "kind": "paragraph",
            "id": original["id"],
            "text": "面板修正：小猫更喜欢柔软的毯子。",
        },
    )
    assert edited["success"], edited
    history = read(memories, scope, "inspection", "correction")["items"]
    assert history and history[0]["status"] == "executed"
    assert any("毯子" in item["text"] for item in read(memories, scope)["items"])
    rolled_back = await room.call(scope, "admin", "rollback", {"id": history[0]["id"]})
    assert rolled_back["success"], rolled_back
    preview = await room.call(
        scope, "admin", "delete_preview", {"kind": "paragraph", "id": original["id"]}
    )
    assert preview["success"], preview
    deleted = await room.call(
        scope, "admin", "delete_execute", {"kind": "paragraph", "id": original["id"]}
    )
    assert deleted["success"], deleted
    assert not read(memories, scope, identity=original["id"])["items"]
    operation = read(memories, scope, "delete")["items"][0]
    restored = await room.call(
        scope, "admin", "delete_restore", {"id": operation["id"]}
    )
    assert restored["success"], restored
    assert read(memories, scope, identity=original["id"])["items"]
    exported = await room.call(scope, "admin", "export_bundle", {})
    assert exported["encoded"] and exported["success"], exported
    _, other, _, _ = await material(memories, source_id="second-scope", session="456")
    assert not read(memories, other)["items"]
    imported = await room.call(
        other, "admin", "import_bundle", {"encoded": exported["encoded"]}
    )
    assert imported["success"], imported
    assert read(memories, other)["items"]


async def test_admin_scope_requires_a_real_native_conversation(memories):
    panel = MemoryPanel(memories)
    for scope in ("../metadata", "0" * 24, "b" * 24):
        with pytest.raises(ValueError):
            await panel.scope(scope)


async def test_image_console_uses_native_assets_and_observation_history(memories):
    from tests.test_image_memory import capture, picture
    from tests.test_runtime import api_call

    _, scope, _, _ = await capture(memories, "panel-image", picture())
    item = read(memories, scope, "images")["items"][0]
    response = await api_call(
        memories.page_api.memory_panel.image, query=f"scope={scope}&id={item['id']}"
    )
    assert response["status"] == "ok", response
    assert response["data"]["encoded"]
    observation = item["observations"][0]
    saved = await memories.room_memory.call(
        scope,
        "admin",
        "image_observe",
        {
            "id": observation["occurrence_id"],
            "text": "管理员记录的绿色图片。",
            "previous": observation["observation_id"] or "",
        },
    )
    assert saved
    updated = read(memories, scope, "images")["items"][0]
    assert any(
        row["text"] == "管理员记录的绿色图片。" for row in updated["observations"]
    )
    removed = await memories.room_memory.call(
        scope, "admin", "image_forget", {"id": observation["occurrence_id"]}
    )
    assert removed["success"], removed
