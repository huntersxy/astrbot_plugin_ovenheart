"""Validate temporary provider context without persisting persona duplicates."""

import pytest

pytest.importorskip("astrbot.api")

from astrbot.api.provider import ProviderRequest  # noqa: E402
from astrbot.core.agent.message import (  # noqa: E402
    Message,
    TextPart,
    dump_messages_with_checkpoints,
)

from tests.test_runtime import event, sources  # noqa: E402

pytest_plugins = ["tests.test_runtime"]


async def test_dual_context_is_bounded_private_and_removed_by_native_history(plugin):
    plugin.settings.update(
        guidance_delivery="dual", learning_enabled=False, prompt_budget=500
    )
    plugin.context.persona_manager.get_persona_v3_by_id = lambda name: {
        "prompt": "角色名小夏，温柔的群友。" * 100
    }
    original = TextPart(text="Other plugin context")
    request = ProviderRequest(
        prompt="你叫什么？",
        system_prompt="Host rules and private tool policy",
        extra_user_content_parts=[original],
    )
    incoming = event("dual-empty")
    await plugin.apply_persona(incoming, request)
    assert (
        request.prompt == "你叫什么？"
        and request.system_prompt == "Host rules and private tool policy"
    )
    assert request.extra_user_content_parts[0] is original
    assert len(request.extra_user_content_parts) == 2
    supplement = request.extra_user_content_parts[-1]
    assert isinstance(supplement, TextPart) and len(supplement.text) <= 500
    assert "小夏" in supplement.text and "private tool policy" not in supplement.text
    assembled = await request.assemble_context()
    # Exercise the host's actual Message conversion and persistence serializer.
    runtime = Message.model_validate(assembled)
    persisted = dump_messages_with_checkpoints([runtime])[0]
    assert "小夏" not in str(persisted) and "OvenHeart turn context" not in str(
        persisted
    )
    assert "Other plugin context" in str(persisted) and "你叫什么" in str(persisted)
    state = await plugin.store.call("state")
    assert state["scopes"][0]["pending"] == 0
    await plugin.apply_persona(incoming, request)
    assert len(request.extra_user_content_parts) == 2


async def test_system_default_and_application_pause_do_not_add_user_context(plugin):
    scope = await sources(plugin)
    request = ProviderRequest(prompt="好家伙", system_prompt="原有人格")
    await plugin.apply_persona(event("system"), request)
    assert request.extra_user_content_parts == []
    plugin.settings["guidance_delivery"] = "dual"
    await plugin.store.call("mutate", scope, "profile", {"apply_enabled": False})
    await plugin.apply_persona(event("paused"), request)
    assert request.extra_user_content_parts == []
    assert request.system_prompt == "原有人格"


async def test_dual_profile_and_habits_stay_in_their_current_scope(plugin):
    scope = await sources(plugin)
    await plugin.store.call(
        "mutate", scope, "profile", {"identity": "Current scope identity"}
    )
    plugin.settings.update(guidance_delivery="dual", learning_enabled=False)
    current = ProviderRequest(prompt="你好")
    other = ProviderRequest(prompt="你好")
    await plugin.apply_persona(event("this"), current)
    await plugin.apply_persona(event("other", session="different-group"), other)
    assert "Current scope identity" in current.extra_user_content_parts[0].text
    assert "Current scope identity" not in other.extra_user_content_parts[0].text
