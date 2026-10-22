"""Check real target anchors handed to the optional LivingMemory protocol."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("astrbot.api")

from tests.test_participation import decision, requests  # noqa: E402
from tests.test_runtime import event  # noqa: E402

pytest_plugins = ["tests.test_participation"]


@pytest.mark.parametrize("missing", [False, True])
async def test_reply_binding_uses_real_target_anchors_even_with_learning_off(
    participant, missing
):
    participant.settings.update(
        learning_enabled=False, participation_mode="observe", participation_cooldown=0
    )
    older = event("older", text="甲的真实提问", sender="alice")
    await requests(participant, older)
    participant.settings["participation_mode"] = "auto"
    participant.context.llm_generate.return_value = decision(target_sequence=1)
    if missing:
        participant.participation.sources.clear()
    bind = Mock(return_value=not missing)
    metadata = SimpleNamespace(
        activated=True,
        star_cls=SimpleNamespace(event_handler=SimpleNamespace(bind_reply_source=bind)),
    )
    participant.context.get_registered_star = lambda name: (
        metadata if name == "astrbot_plugin_livingmemory" else None
    )
    newest = event("newest", text="乙的另一件事", sender="bob")
    assert len(await requests(participant, newest)) == 1
    bind.assert_called_once()
    trigger, source, current = bind.call_args.args
    assert trigger is newest and source is (None if missing else older)
    assert (
        newest.get_sender_id() == "bob" and newest.get_message_str() == "乙的另一件事"
    )
    assert current()
    participant.participation.notice(event("later", text="新的消息"))
    assert not current()
    participant.participation.close()
    assert not participant.participation.sources


async def test_older_livingmemory_keeps_its_native_path_without_a_protocol_call(
    participant,
):
    metadata = SimpleNamespace(
        activated=True, star_cls=SimpleNamespace(event_handler=SimpleNamespace())
    )
    participant.context.get_registered_star = lambda name: metadata
    trigger, source = event("trigger"), event("source", sender="bob")
    message = participant.memory.bind_reply_source(trigger, source, lambda: True)
    assert "不支持回复来源绑定" in message
    assert not trigger.get_extra("livingmemory_reply_source_requested", False)
