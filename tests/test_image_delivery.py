"""Verify actual GIF bytes and native sending without changing original assets."""

import base64
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.core.message.components import Image, Plain
from astrbot.core.message.message_event_result import MessageChain
from PIL import Image as PILImage

from astrbot_plugin_ovenheart.integrations.image_delivery import single_frame_gif
from astrbot_plugin_ovenheart.integrations.outgoing_media import OutgoingMediaBridge
from tests.test_runtime import event


def source_image():
    output = BytesIO()
    image = PILImage.new("RGBA", (8, 8), (200, 30, 10, 255))
    image.putpixel((0, 0), (0, 0, 0, 0))
    image.save(output, "PNG")
    return output.getvalue()


def test_transparent_static_image_becomes_one_frame_gif_and_animations_pass_through():
    converted = single_frame_gif(source_image())
    with PILImage.open(BytesIO(converted)) as result:
        assert result.format == "GIF" and result.n_frames == 1
        assert result.convert("RGBA").getpixel((0, 0))[3] == 0
    assert single_frame_gif(converted) is None
    for fmt in ("GIF", "WEBP"):
        output = BytesIO()
        PILImage.new("RGB", (8, 8), "red").save(
            output,
            fmt,
            save_all=True,
            append_images=[PILImage.new("RGB", (8, 8), "blue")],
            duration=100,
            loop=0,
        )
        assert single_frame_gif(output.getvalue()) is None


@pytest.mark.parametrize("enabled", [True, False])
async def test_native_send_switch_changes_only_the_wire_image(enabled):
    bridge = OutgoingMediaBridge(
        SimpleNamespace(settings={"emoji_static_to_gif": enabled})
    )
    bridge.capture = AsyncMock()
    incoming = event()
    native_send = AsyncMock(return_value=True)
    incoming.send = native_send
    image = Image.fromBytes(source_image())
    chain = MessageChain(chain=[Plain("正文"), image])
    bridge.bind_event(incoming)
    assert await incoming.send(chain)
    wire = native_send.await_args.args[0]
    encoded = await wire.chain[1].convert_to_base64()
    with PILImage.open(BytesIO(base64.b64decode(encoded))) as result:
        assert result.format == ("GIF" if enabled else "PNG") and result.n_frames == 1
    assert chain.chain[1] is image
    assert bridge.capture.await_args.args[1] == [image]
