"""Native image provenance with original assets, jobs and exact/vector retrieval."""

import asyncio
import base64
import io
import sys
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
from astrbot.core.message.components import Image
from PIL import Image as PILImage

from tests.test_runtime import event

pytest_plugins = ["tests.test_room_memory"]


def picture(color=(10, 200, 10), size=(16, 16), fmt="PNG"):
    output = io.BytesIO()
    PILImage.new("RGB", size, color).save(output, format=fmt)
    return output.getvalue()


async def capture(plugin, identifier, payload, text="", session="123"):
    incoming = event(identifier, text=text, session=session)
    incoming.message_obj.message = [
        Image.fromBase64(base64.b64encode(payload).decode())
    ]
    await plugin.capture(incoming)
    await asyncio.gather(*tuple(plugin.image_memory.busy.values()))
    scope, persona, _ = await plugin.resolve_scope(incoming)
    plugin.participation.sources[scope, identifier] = incoming
    return (
        incoming,
        scope,
        persona,
        [
            {
                "sequence": 1,
                "source": identifier,
                "sender_id": "alice",
                "message_id": identifier,
            }
        ],
    )


async def process_jobs(state):
    return await state.kernel.image_memory_runtime.process_jobs_once()


async def test_actual_binary_embedding_probe_jobs_and_native_image_recall(
    memories, monkeypatch
):
    p = memories
    p.settings["image_embedding_provider_id"] = "native-image"
    requests = []

    def embed(**kwargs):
        requests.append(kwargs["input"])
        raw = base64.b64decode(kwargs["input"][0]["image"].split(",", 1)[1])
        with PILImage.open(io.BytesIO(raw)) as image:
            vector = (
                np.asarray(image.convert("RGB"), dtype=float).mean(axis=(0, 1)).tolist()
            )
        return SimpleNamespace(
            status_code=200, output={"embeddings": [{"embedding": vector}]}
        )

    monkeypatch.setitem(
        sys.modules,
        "dashscope",
        SimpleNamespace(MultiModalEmbedding=SimpleNamespace(call=embed)),
    )
    native = SimpleNamespace(
        provider_config={"type": "dashscope_embedding"},
        get_model=lambda: "qwen3-vl-embedding",
        base_url="https://binary.invalid/api/v1",
        api_key="transport-test-only",
        request_headers={},
    )
    vision = SimpleNamespace(
        text_chat=AsyncMock(
            return_value=SimpleNamespace(
                completion_text='{"description":"绿色猫猫开心表情包","is_emoji":true}'
            )
        )
    )
    p.context.get_provider_by_id = lambda identifier: (
        native
        if identifier == "native-image"
        else vision
        if identifier == "vision"
        else None
    )
    p.context.get_config = lambda _: {
        "plugin_set": ["*"],
        "platform_settings": {},
        "wake_prefix": [],
        "provider_ltm_settings": {
            "image_caption": True,
            "image_caption_provider_id": "vision",
        },
    }
    first, scope, persona, messages = await capture(
        p, "first-green", picture(), "开心猫猫"
    )
    original = first.get_extra("ovenheart_image_assets")[0]
    state = p.room_memory.rooms[scope]
    state.process_jobs = MethodType(process_jobs, state)
    jobs = await p.room_memory.call(scope, "process_jobs")
    assert jobs["status"] == "ready", jobs
    same, _, _, messages = await capture(p, "same-green", picture())
    exact = await p.image_memory.query(same, scope, persona, messages, "1", 0)
    assert exact["success"] and exact["hits"][0]["match_kind"] == "exact_hash", exact
    assert any(
        row["text"] == "绿色猫猫开心表情包" for row in exact["hits"][0]["observations"]
    )
    similar, _, _, messages = await capture(
        p, "near-green", picture((12, 190, 14), (17, 17))
    )
    found = await p.image_memory.query(similar, scope, persona, messages, "1", 0)
    hit = next(row for row in found["hits"] if row["asset_id"] == original["asset_id"])
    assert hit["match_kind"] != "exact_hash" and hit["similarity"] > 0.9
    assert all("image" in entry[0] and "text" not in entry[0] for entry in requests)
    assert "chat_provider_id" not in vision.text_chat.await_args.kwargs
    p.settings["learning_enabled"] = False
    paused = await p.room_memory.call(scope, "process_jobs")
    assert paused["status"] == "paused"
    await p.auxiliary.flush()
    assert (
        next(
            row
            for row in await p.store.call("auxiliary_usage")
            if row["stage"] == "image_caption"
        )["calls"]
        == 3
    )


async def test_image_only_gif_exact_memory_and_scope_without_image_model(memories):
    p = memories
    first, scope, persona, _ = await capture(p, "gif-one", picture(fmt="GIF"))
    assert first.get_extra("ovenheart_image_assets")[0]["asset_id"]
    second, _, _, messages = await capture(p, "gif-two", picture(fmt="GIF"))
    found = await p.image_memory.query(second, scope, persona, messages, "1", 0)
    assert found["success"] and found["hits"][0]["match_kind"] == "exact_hash", found
    other, other_scope, other_persona, other_messages = await capture(
        p, "other-room", picture(fmt="GIF"), session="456"
    )
    hidden = await p.image_memory.query(
        other, other_scope, other_persona, other_messages, "1", 0
    )
    assert not hidden["hits"]
    assert not (
        await p.image_memory.query(other, other_scope, other_persona, messages, "1", 0)
    )["success"]


async def test_animated_gif_keeps_original_bytes_and_exact_recall(memories):
    output = io.BytesIO()
    frames = [PILImage.new("RGB", (16, 16), color) for color in ("red", "blue")]
    frames[0].save(
        output, format="GIF", save_all=True, append_images=frames[1:], duration=100
    )
    payload = output.getvalue()
    incoming, scope, persona, messages = await capture(memories, "animated", payload)
    assets = incoming.get_extra("ovenheart_image_assets")
    assert assets, "Animated GIF was rejected before its occurrence was stored"
    stored = await memories.room_memory.call(
        scope, "image_asset", assets[0]["asset_id"]
    )
    assert await asyncio.to_thread(Path(stored["path"]).read_bytes) == payload
    incoming, _, _, messages = await capture(memories, "animated-again", payload)
    found = await memories.image_memory.query(
        incoming, scope, persona, messages, "1", 0
    )
    assert found["success"] and found["hits"][0]["match_kind"] == "exact_hash", found
