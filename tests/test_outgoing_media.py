"""Verify real outgoing image chains and the reversible separate-send adapter."""

import asyncio
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.api.provider import ProviderRequest
from astrbot.core.message.components import Image, Plain
from astrbot.core.message.message_event_result import ResultContentType

from tests.test_structured_conversation import incoming

pytest_plugins = ["tests.test_structured_conversation"]

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
REF = "gimg_" + "a" * 24
QQ_CACHE_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "astrbot_plugin_qq_enhance/group_image_cache.py"
)


class MemeSender:
    def __init__(self):
        self.sent = []
        self.fail = False

    def _scope_allows_llm_origin(self, event):
        return True

    async def _send_meme_image(self, event, image):
        if self.fail:
            raise OSError("platform send failed")
        self.sent.append(image)

    async def _on_decorating_result_impl(self, event):
        event.set_extra("meme_manager_semantic_selected_ids", None)


async def test_planner_owns_emoji_selection_without_changing_other_requests(
    structured, tmp_path
):
    wake, _, sender, _, _ = await prepare(structured, tmp_path)
    structured.settings.update(
        emoji_enabled=True,
        native_memory_enabled=True,
        image_memory_enabled=True,
        memory_enabled=True,
    )
    assert sender._scope_allows_llm_origin(wake)
    wake.set_extra("ovenheart_decision", {"planner_session": "current"})
    assert not sender._scope_allows_llm_origin(wake)
    structured.settings["emoji_enabled"] = False
    assert sender._scope_allows_llm_origin(wake)
    structured.outgoing_media.disconnect()
    assert sender._scope_allows_llm_origin(wake)


async def prepare(plugin, tmp_path, *, caption="蓝发女孩抱着西瓜啃，神情满足"):
    path = tmp_path / "meme.png"
    path.write_bytes(PNG)
    image = Image.fromFileSystem(str(path))
    sender = MemeSender()
    cache = SimpleNamespace(
        put=AsyncMock(return_value={"image_ref": REF, "width": 1, "height": 1})
    )
    qq = SimpleNamespace(
        config={"context_images": {"enabled": True, "group_cache_enabled": True}},
        context_images=SimpleNamespace(group_cache=cache),
    )
    stars = {
        "meme_manager": SimpleNamespace(star_cls=sender, activated=True),
        "astrbot_plugin_qq_enhance": SimpleNamespace(star_cls=qq, activated=True),
    }
    plugin.context.get_registered_star = stars.get
    assert plugin.outgoing_media.install()
    wake = incoming("wake", "说点什么")
    wake.is_at_or_wake_command = True
    await plugin.apply_persona(wake, ProviderRequest(prompt=wake.message_str))
    wake.set_extra(
        "meme_manager_semantic_candidates",
        {
            "meme:one": {
                "id": "meme:one",
                "content_sha256": hashlib.sha256(PNG).hexdigest(),
                "caption": caption,
            }
        },
    )
    wake.set_extra("meme_manager_semantic_selected_ids", ["meme:one"])
    return wake, image, sender, cache, stars


def own(plugin, event):
    return [
        m
        for m in plugin.participation.windows[event.get_extra("ovenheart_reply_scope")][
            "messages"
        ]
        if m.get("self")
    ]


async def test_separate_meme_send_reaches_next_reply_with_caption_and_real_reference(
    structured, tmp_path
):
    wake, image, sender, cache, _ = await prepare(structured, tmp_path)
    await sender._send_meme_image(wake, image)
    rows = own(structured, wake)
    assert len(rows) == 1 and rows[0]["media"][0]["text"].startswith("蓝发女孩")
    assert rows[0]["image_refs"] == [REF]
    assert cache.put.await_args.args[-1] == "dialog-a"
    assert str(tmp_path) not in json.dumps(rows)
    praise = incoming("praise", "你发的啃西瓜那张好可爱", sender="peer")
    request = ProviderRequest(prompt=praise.message_str)
    await structured.apply_persona(praise, request)
    await structured.apply_participation(praise, request)
    assembled = json.dumps(await request.assemble_context(), ensure_ascii=False)
    assert "蓝发女孩抱着西瓜啃" in assembled and REF in assembled
    assert praise.get_extra("ovenheart_reply_focus")["recent_self"][-1]["media"][0][
        "text"
    ].startswith("蓝发女孩")
    structured.context.llm_generate.assert_not_awaited()


async def test_search_or_failed_send_is_not_a_posted_image(structured, tmp_path):
    wake, image, sender, _, _ = await prepare(structured, tmp_path)
    assert not own(structured, wake)
    sender.fail = True
    with pytest.raises(OSError):
        await sender._send_meme_image(wake, image)
    assert not own(structured, wake)


async def test_mixed_result_keeps_metadata_before_selection_is_cleared_and_deduplicates_hook(
    structured, tmp_path
):
    wake, image, sender, _, _ = await prepare(structured, tmp_path)
    wake.set_result(wake.chain_result([Plain("正文"), image]))
    await sender._on_decorating_result_impl(wake)
    assert wake.get_extra("meme_manager_semantic_selected_ids") is None
    await wake.send(wake.get_result())
    await structured.record_delivery(wake)
    await structured.outgoing_media.capture_result(wake)
    images = [m for m in own(structured, wake) if m["media"]]
    assert (
        len(images) == 1
        and images[0]["media"][0]["description_source"] == "meme_metadata"
    )
    assert any(m["text"] == "正文" for m in own(structured, wake))


async def test_image_only_stream_does_not_require_a_plain_text_send_hook(
    structured, tmp_path
):
    wake, image, sender, _, _ = await prepare(structured, tmp_path)
    finish = wake.plain_result("")
    finish.result_content_type = ResultContentType.STREAMING_FINISH
    wake.set_result(finish)
    await sender._send_meme_image(wake, image)
    await structured.guard_participation_result(wake)
    assert len(own(structured, wake)) == 1 and own(structured, wake)[0]["media"]


async def test_mixed_foreign_image_cannot_borrow_selected_meme_caption(
    structured, tmp_path
):
    wake, image, sender, _, _ = await prepare(structured, tmp_path)
    wake.get_extra("meme_manager_semantic_candidates")["meme:one"]["content_sha256"] = (
        "b" * 64
    )
    wake.set_result(wake.chain_result([image]))
    await sender._on_decorating_result_impl(wake)
    await wake.send(wake.get_result())
    await structured.outgoing_media.capture_result(wake)
    assert "text" not in own(structured, wake)[0]["media"][0]


@pytest.mark.parametrize("reset", [False, True])
async def test_unknown_temporary_image_is_described_after_cleanup_without_reviving_reset(
    structured, tmp_path, reset
):
    wake, image, sender, _, _ = await prepare(structured, tmp_path, caption="")
    cfg = {
        "plugin_set": ["*"],
        "provider_ltm_settings": {
            "image_caption": True,
            "image_caption_provider_id": "vision",
        },
    }
    structured.context.get_config = lambda umo: cfg
    entered, release = asyncio.Event(), asyncio.Event()

    async def describe(**kwargs):
        assert kwargs["image_urls"][0].startswith("base64://")
        entered.set()
        await release.wait()
        return SimpleNamespace(completion_text="一只抱着西瓜的小猫")

    structured.context.get_provider_by_id = lambda key: SimpleNamespace(
        text_chat=describe
    )
    await sender._send_meme_image(wake, image)
    await asyncio.wait_for(entered.wait(), 1)
    (tmp_path / "meme.png").unlink()
    captions = [
        t
        for t in structured.tasks
        if t.get_coro().__qualname__.endswith("ConversationContext._caption")
    ]
    if reset:
        await structured.conversation.clear(wake)
    release.set()
    await asyncio.wait_for(asyncio.gather(*captions), 1)
    rows = own(structured, wake)
    if reset:
        assert not rows
    else:
        assert rows[0]["media"][0]["text"] == "一只抱着西瓜的小猫"
        assert rows[0]["media"][0]["image_ref"] == REF
        scope = wake.get_extra("ovenheart_reply_scope")
        restored = await structured.store.call("chat_context", scope, "dialog-a")
        assert restored["messages"][-1]["media"][0]["status"] == "ready"


async def test_adapter_reloads_and_restores_only_methods_it_still_owns(
    structured, tmp_path
):
    _, _, first, _, stars = await prepare(structured, tmp_path)
    wrapper = first._send_meme_image
    assert structured.outgoing_media.install() and first._send_meme_image is wrapper
    second = MemeSender()
    stars["meme_manager"].star_cls = second
    assert structured.outgoing_media.install()
    assert "_send_meme_image" not in first.__dict__
    replacement = AsyncMock()
    second._send_meme_image = replacement
    structured.outgoing_media.disconnect()
    assert second._send_meme_image is replacement
    assert "_on_decorating_result_impl" not in second.__dict__


async def test_after_send_hook_does_not_claim_a_failed_mixed_image(
    structured, tmp_path
):
    wake, image, sender, _, _ = await prepare(structured, tmp_path)
    # Bind a native adapter that fails before it accepts any components.
    wake.send = AsyncMock(side_effect=OSError("failed image"))
    wake.set_extra("ovenheart_media_send_bound", False)
    structured.outgoing_media.bind_event(wake)
    wake.set_result(wake.chain_result([image]))
    await sender._on_decorating_result_impl(wake)
    with pytest.raises(OSError):
        await wake.send(wake.get_result())
    await structured.record_delivery(wake)
    assert not own(structured, wake)


@pytest.mark.parametrize("encoded", [False, True])
async def test_reference_is_readable_only_in_its_real_group_and_conversation(
    structured, tmp_path, encoded
):
    wake, image, sender, _, stars = await prepare(structured, tmp_path)
    spec = importlib.util.spec_from_file_location(
        "qq_group_cache_for_outgoing_test", QQ_CACHE_SOURCE
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cache = module.GroupImageCache(tmp_path / "cache", 2, 1, 1)
    stars["astrbot_plugin_qq_enhance"].star_cls.context_images.group_cache = cache
    if encoded:
        image = Image.fromBase64(base64.b64encode(PNG).decode())
    await sender._send_meme_image(wake, image)
    reference = own(structured, wake)[0]["image_refs"][0]
    assert reference.startswith("gimg_")
    assert (
        await cache.read(
            reference, wake.get_platform_id(), wake.unified_msg_origin, "dialog-a"
        )
    )[0] == PNG
    assert (
        await cache.read(reference, wake.get_platform_id(), "other-group", "dialog-a")
        is None
    )
    assert (
        await cache.read(
            reference, wake.get_platform_id(), wake.unified_msg_origin, "new-dialog"
        )
        is None
    )
