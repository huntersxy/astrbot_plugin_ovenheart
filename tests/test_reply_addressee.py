"""Bind the leading reply address to this turn rather than copied old names."""

import pytest
from astrbot.api.provider import ProviderRequest
from astrbot.core.message.components import At, Plain, Reply

from astrbot_plugin_ovenheart.main import ParticipationFilter
from tests.test_participation import decision, requests, result_decorate_stage
from tests.test_runtime import event

pytest_plugins = ["tests.test_participation"]


async def test_direct_qq_reply_replaces_a_stale_leading_address(participant):
    participant.settings["guidance_delivery"] = "cached"
    original_config = participant.context.get_config
    participant.context.get_config = lambda umo: (
        original_config(umo) | {"platform_settings": {"reply_with_quote": True}}
    )
    current = event(
        "whale-question",
        text="@烤箱插座 那生图模型是啥呀",
        sender="whale",
        sender_name="鲸鱼娘",
    )
    current.is_at_or_wake_command = True
    await participant.capture(current)
    assert not ParticipationFilter().filter(current, {})
    assert await requests(participant, current) == []
    assert current.get_extra("enable_streaming") is None
    request = ProviderRequest(
        prompt=current.get_message_str(),
        system_prompt="稳定人格",
        contexts=[{"role": "assistant", "content": "@石头 我的图不是硅基流动画的"}],
    )
    await participant.apply_participation(current, request)
    current.set_result(
        current.plain_result("@石头 具体名字我记不太清，插件里挂的是gpt系模型。")
    )
    await participant.guard_participation_result(current)
    final = current.get_result().chain
    assert [str(p.qq) for p in final if isinstance(p, At)] == ["whale"]
    assert "@石头" not in "".join(p.text for p in final if isinstance(p, Plain))
    assert "具体名字我记不太清" in "".join(
        p.text for p in final if isinstance(p, Plain)
    )
    stage = result_decorate_stage(mention=True, quote=True)
    async for _ in stage.process(current):
        pass
    assert [str(p.qq) for p in current.get_result().chain if isinstance(p, At)] == [
        "whale"
    ]
    assert [p.id for p in current.get_result().chain if isinstance(p, Reply)] == [
        "whale-question"
    ]
    assert (
        current.get_sender_id() == "whale"
        and current.message_obj.message_id == "whale-question"
    )
    assert current.get_extra("enable_streaming") is None
    assert request.system_prompt == "稳定人格"
    assert request.extra_user_content_parts[-1].model_dump_for_context()["_no_save"]
    participant.context.llm_generate.assert_not_awaited()


@pytest.mark.parametrize("decoration", ["none", "mention"])
async def test_correct_plan_cannot_notify_a_copied_previous_addressee(
    participant, decoration
):
    participant.settings.update(participation_mode="observe", participation_cooldown=0)
    await requests(
        participant, event("stone-question", sender="stone", sender_name="石头")
    )
    participant.settings["participation_mode"] = "auto"
    participant.context.llm_generate.return_value = decision(
        target_sequence=2, decoration=decoration
    )
    current = event(
        "whale-question", text="那生图模型是啥呀", sender="whale", sender_name="鲸鱼娘"
    )
    request = (await requests(participant, current))[0]
    await participant.apply_participation(current, request)
    current.set_result(current.plain_result("@石头 插件里挂的是gpt系模型。"))
    await participant.guard_participation_result(current)
    assert [str(p.qq) for p in current.get_result().chain if isinstance(p, At)] == [
        "whale"
    ]
    assert "@石头" not in "".join(
        p.text for p in current.get_result().chain if isinstance(p, Plain)
    )


async def test_address_binding_preserves_inline_mentions_and_literal_examples(
    participant,
):
    current = event("direct", sender="whale", sender_name="鲸鱼娘")
    current.message_obj.message = [
        Reply(id="old", sender_id="stone", sender_nickname="石头")
    ]
    await participant.apply_participation(current, ProviderRequest(prompt="解释一下"))
    current.set_result(
        current.plain_result(
            "代码 `@石头` 和邮箱 user@stone.example 保留；正文里问 @石头 这个问题。"
        )
    )
    await participant.guard_participation_result(current)
    chain = current.get_result().chain
    assert [str(p.qq) for p in chain if isinstance(p, At)] == ["stone"]
    text = "".join(p.text for p in chain if isinstance(p, Plain))
    assert "`@石头`" in text and "user@stone.example" in text
    current.set_result(
        current.chain_result([At(qq="stone", name="石头"), Plain("已有组件保持原对象")])
    )
    await participant.guard_participation_result(current)
    assert [str(p.qq) for p in current.get_result().chain if isinstance(p, At)] == [
        "stone"
    ]
