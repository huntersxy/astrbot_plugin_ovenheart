"""Public emoji assets share the native store and keep source imports read-only."""

import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlencode

from astrbot_plugin_ovenheart.core.settings import SHARED_SCOPE
from astrbot_plugin_ovenheart.memory.emoji_panel import EmojiPanel
from tests.test_outgoing_media import PNG
from tests.test_room_memory import material
from tests.test_runtime import api_call

pytest_plugins = ["tests.test_room_memory"]


async def test_library_upload_caption_import_dedup_and_remove(
    memories, tmp_path, monkeypatch
):
    panel = memories.page_api.emoji_panel

    async def action(name, payload):
        result = await api_call(
            panel.action, {"scope": "library", "action": name, "payload": payload}
        )
        assert result["status"] == "ok", result
        return result["data"]

    await action(
        "emoji_upload", {"encoded": base64.b64encode(PNG).decode(), "text": "点头赞同"}
    )
    rows = (await api_call(panel.read, query="scope=library"))["data"]["items"]
    assert len(rows) == 1
    identity = rows[0]["id"]
    thumbnail = await api_call(panel.image, query=f"scope=library&id={identity}")
    assert thumbnail["status"] == "ok", thumbnail
    assert thumbnail["data"]["encoded"]
    await action("emoji_caption", {"id": identity, "text": "收到，表示确认"})
    matches = await api_call(
        panel.read, query=urlencode({"scope": "library", "query": "确认"})
    )
    assert matches["data"]["total"] == 1
    source = tmp_path / "packs"
    pack = source / "test-pack"
    (pack / "memes").mkdir(parents=True)
    (pack / "memes/one.png").write_bytes(PNG)
    metadata = json.dumps(
        {
            "images": {
                "source-one": {"relative_path": "memes/one.png", "caption": "重复图片"}
            }
        }
    )
    (pack / "semantic_metadata.json").write_text(metadata, encoding="utf-8")
    monkeypatch.setattr(EmojiPanel, "packs", property(lambda self: source))
    assert (await api_call(panel.read, query="view=packs"))["data"]["packs"] == [
        {"id": "test-pack", "count": 1}
    ]
    assert (await action("emoji_import", {"pack": "test-pack", "offset": 0}))["done"]
    assert (await api_call(panel.read, query="scope=library"))["data"]["total"] == 1
    assert (pack / "memes/one.png").read_bytes() == PNG
    assert (pack / "semantic_metadata.json").read_text(encoding="utf-8") == metadata
    candidates = await memories.room_memory.call(SHARED_SCOPE, "image_candidates")
    assert len(candidates) == 1
    assert (
        len(await memories.room_memory.call(SHARED_SCOPE, "image_candidates", "确认"))
        == 1
    )
    assert not await memories.room_memory.call(
        SHARED_SCOPE, "image_candidates", "不匹配"
    )
    incoming, scope, persona, _ = await material(memories)
    incoming.send = AsyncMock(return_value=True)
    session = SimpleNamespace(
        event=incoming,
        scope=scope,
        persona=persona,
        emojis={},
        check_current=AsyncMock(),
    )
    offered = await memories.emoji_actions.show(session)
    assert offered.content
    assert len(session.emojis) == 1
    index = next(iter(session.emojis))
    assert session.emojis[index]["scope"] == SHARED_SCOPE
    assert (await memories.emoji_actions.send(incoming, session, index))["sent"]
    incoming.send.assert_awaited_once()
    await action("emoji_remove", {"id": identity})
    assert not await memories.room_memory.call(SHARED_SCOPE, "image_candidates")
    assert (await api_call(panel.read, query="scope=library"))["data"]["total"] == 0


async def test_import_rejects_paths_outside_pack(memories, tmp_path, monkeypatch):
    pack = tmp_path / "packs/test-pack"
    pack.mkdir(parents=True)
    secret = tmp_path / "outside.png"
    secret.write_bytes(PNG)
    (pack / "semantic_metadata.json").write_text(
        json.dumps(
            {
                "images": {
                    "bad": {"relative_path": "../../outside.png", "caption": "bad"}
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(EmojiPanel, "packs", property(lambda self: tmp_path / "packs"))
    result = await api_call(
        memories.page_api.emoji_panel.action,
        {
            "scope": "library",
            "action": "emoji_import",
            "payload": {"pack": "test-pack", "offset": 0},
        },
    )
    assert result["status"] == "error" and "路径无效" in result["message"]
