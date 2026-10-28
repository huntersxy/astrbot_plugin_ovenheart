"""Reference storage and delivery through actual native request parts."""

import base64
from types import SimpleNamespace

import pytest
from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.message import ImageURLPart, TextPart

from tests.test_image_memory import picture
from tests.test_runtime import api_call, event

pytest_plugins = ["tests.test_participation"]


async def test_portrait_is_persona_bound_and_reuses_native_multimodal_request(
    participant,
):
    p = participant
    incoming = event("portrait-source")
    await p.capture(incoming)
    scope, persona, _ = await p.resolve_scope(incoming)
    encoded = base64.b64encode(picture()).decode()
    saved = await api_call(
        p.self_portrait.save,
        {
            "scope": scope,
            "action": "save",
            "description": "金色短发，插座形发饰。",
            "encoded": encoded,
        },
    )
    assert saved["status"] == "ok", saved
    assert saved["data"]["has_image"] and "image" not in saved["data"]
    other = event("another-source", session="456")
    await p.capture(other)
    other_scope, other_persona, _ = await p.resolve_scope(other)
    read = await api_call(p.self_portrait.read, query=f"scope={other_scope}&image=true")
    assert read["data"]["encoded"] == encoded and persona == other_persona
    assert await p.self_portrait.context("different-persona") is None
    p.context.get_provider_by_id = lambda _: SimpleNamespace(
        provider_config={"modalities": ["text", "image"]}
    )
    text = ProviderRequest(prompt="你好", system_prompt="宿主身份", contexts=[])
    await p.self_portrait.apply(incoming, persona, text)
    assert len(text.extra_user_content_parts) == 1
    assert isinstance(text.extra_user_content_parts[0], TextPart)
    assert text.extra_user_content_parts[0].model_dump_for_context()["_no_save"]
    assert text.system_prompt == "宿主身份"
    image = ProviderRequest(
        prompt="这是谁",
        image_urls=["https://example.invalid/incoming.png"],
        contexts=[],
    )
    await p.self_portrait.apply(incoming, persona, image)
    await p.self_portrait.apply(incoming, persona, image)
    refs = [
        part
        for part in image.extra_user_content_parts
        if isinstance(part, ImageURLPart)
    ]
    assert len(refs) == 1 and refs[0].model_dump_for_context()["_no_save"]
    assert refs[0].image_url.url == "data:image/png;base64," + encoded
    edited = await api_call(
        p.self_portrait.save,
        {"scope": scope, "action": "save", "description": "金色短发。"},
    )
    assert edited["data"]["has_image"]
    assert (await api_call(p.self_portrait.read, query=f"scope={scope}&image=true"))[
        "data"
    ]["encoded"] == encoded
    p.context.llm_generate.assert_not_awaited()
    cleared = await api_call(p.self_portrait.save, {"scope": scope, "action": "clear"})
    assert cleared["status"] == "ok" and await p.self_portrait.context(persona) is None


@pytest.mark.parametrize(
    "encoded",
    [
        "not-base64",
        base64.b64encode(b"not an image").decode(),
        base64.b64encode(picture(fmt="BMP")).decode(),
    ],
)
async def test_portrait_rejects_invalid_image_without_saving(participant, encoded):
    p = participant
    incoming = event("invalid-portrait")
    await p.capture(incoming)
    scope, persona, _ = await p.resolve_scope(incoming)
    result = await api_call(
        p.self_portrait.save,
        {"scope": scope, "action": "save", "description": "短发。", "encoded": encoded},
    )
    assert result["status"] == "error"
    assert await p.self_portrait.context(persona) is None
