"""Check stable ID-less arrivals without merging repeated human responses."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("astrbot.api")

from astrbot_plugin_ovenheart.participation import event_source  # noqa: E402
from tests.test_participation import decision, requests  # noqa: E402
from tests.test_runtime import event  # noqa: E402

pytest_plugins = ["tests.test_participation"]


async def test_identical_idless_arrivals_survive_equal_timestamps_and_hook_overlap(
    plugin,
):
    first, second = event("", text="嗯"), event("", text="嗯")
    now = time.time()
    first.created_at = second.created_at = now
    first.message_obj.timestamp = second.message_obj.timestamp = int(now)
    del second.message_obj.message_id
    await asyncio.gather(
        plugin.capture(first),
        plugin.capture(first),
        plugin.capture(second),
        plugin.capture(second),
    )
    scope, _, _ = await plugin.resolve_scope(first)
    batch = await plugin.store.call("batch", scope, 30)
    assert len(batch) == 2
    assert {m["source_id"] for m in batch} == {
        event_source(first),
        event_source(second),
    }
    assert event_source(first) != event_source(second)
    first.message_obj.message_id = "late-native-id"
    await plugin.capture(first)
    assert len(await plugin.store.call("batch", scope, 30)) == 2
    native, redelivered = (
        event("platform-1", text="正常消息"),
        event("platform-1", text="正常消息"),
    )
    await plugin.capture(native)
    await plugin.capture(redelivered)
    batch = await plugin.store.call("batch", scope, 30)
    assert len(batch) == 3 and sum(m["source_id"] == "platform-1" for m in batch) == 1


async def test_capture_planning_and_send_context_share_one_idless_identity(participant):
    incoming = event("", text="我想找个人说说话")
    await participant.capture(incoming)
    request = (await requests(participant, incoming))[0]
    plan = incoming.get_extra("ovenheart_decision")
    scope = plan["scope"]
    batch = await participant.store.call("batch", scope, 30)
    assert plan["room_source"] == plan["target"]["source"] == batch[0]["source_id"]
    await participant.apply_persona(incoming, request)
    incoming.set_result(incoming.plain_result("我听着。"))
    await participant.guard_participation_result(incoming)
    await participant.record_delivery(incoming)
    humans = [
        m for m in participant.participation.windows[scope]["messages"] if not m["self"]
    ]
    assert len(humans) == 1 and humans[0]["source"] == batch[0]["source_id"]
    own = next(m for m in participant.learning_context[scope] if m.get("self"))
    assert own["trigger"] == batch[0]["source_id"]
    assert len(await participant.store.call("batch", scope, 30)) == 1


async def test_idless_earlier_target_keeps_a_real_memory_anchor_with_learning_off(
    participant,
):
    participant.settings.update(
        learning_enabled=False, participation_mode="observe", participation_cooldown=0
    )
    older = event("", text="较早的真实问题", sender="alice")
    newest = event("", text="另一位的新话题", sender="bob")
    older.created_at = newest.created_at = time.time()
    del older.message_obj.message_id
    await requests(participant, older)
    participant.settings["participation_mode"] = "auto"
    participant.context.llm_generate.return_value = decision(target_sequence=1)
    bind = Mock(return_value=True)
    metadata = SimpleNamespace(
        activated=True,
        star_cls=SimpleNamespace(event_handler=SimpleNamespace(bind_reply_source=bind)),
    )
    participant.context.get_registered_star = lambda name: metadata
    request = (await requests(participant, newest))[0]
    assert request.prompt == older.get_message_str()
    bind.assert_called_once()
    assert bind.call_args.args[0] is newest and bind.call_args.args[1] is older
    assert newest.get_sender_id() == "bob" and not newest.message_obj.message_id
