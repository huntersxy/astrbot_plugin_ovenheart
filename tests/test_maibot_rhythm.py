"""Compare the event adapter with the actual upstream algorithm and native flow."""

from unittest.mock import AsyncMock

from astrbot_plugin_ovenheart.rhythm import ConversationRhythm
from astrbot_plugin_ovenheart.vendor.maibot.dynamic_gate import DynamicReplyGate
from astrbot_plugin_ovenheart.vendor.maibot.reply_likelihood import (
    ReplyLikelihoodInput,
    estimate_reply_probability,
)
from tests.test_participation import requests
from tests.test_runtime import event

pytest_plugins = ["tests.test_participation"]


def test_adapter_matches_the_upstream_probability_and_frequency_feedback():
    rhythm = ConversationRhythm()
    rhythm.observe("room", "old-question", 900, now=900, forced=True)
    rhythm.observe("room", "bot-answer", 910, now=910, own=True)
    rhythm.observe("room", "question", 1000, now=1000)
    reference = DynamicReplyGate()
    reference.take_uncounted_message_ids(["old-question"])
    reference.record_forced_turn(900)
    reference.record_reply(910)
    reference.take_uncounted_message_ids(["question"])
    probability = estimate_reply_probability(
        ReplyLikelihoodInput(
            mention_bot=True,
            at_other=False,
            has_question_mark=True,
            placeholder_only=False,
            recent_self_ratio=1 / 3,
            recent_message_count=3,
            seconds_since_bot_message=90,
            pending_count=1,
        )
    )
    reference.record_proactive_demand(probability, 1000)
    expected = reference.evaluate(probability, 0.5, 1000)
    actual = rhythm.evaluate(
        "room",
        [{"source": "question", "text": "小烤你怎么看？"}],
        0.5,
        bot_names=["小烤"],
        now=1000,
    )
    assert actual == expected


def test_repeated_observation_and_sends_do_not_duplicate_accounting():
    rhythm = ConversationRhythm()
    for _ in range(3):
        rhythm.observe("room", "human", 1000, now=1000, forced=True)
        rhythm.observe("room", "reply", 1001, now=1001, own=True)
    state = rhythm.rooms["room"]
    assert len(state.activity) == 2
    assert len(state.gate._forced_expectations) == 1
    assert len(state.gate._reply_times) == 1


def test_activity_expiry_keeps_the_real_last_reply_time_and_room_isolation():
    rhythm = ConversationRhythm()
    rhythm.observe("one", "reply", 100, now=100, own=True)
    rhythm.observe("one", "question", 700, now=700)
    rhythm.observe("two", "question", 700, now=700)
    assert len(rhythm.rooms["one"].activity) == 1
    assert rhythm.rooms["one"].last_own == 100
    assert rhythm.rooms["two"].last_own is None
    rhythm.forget("one")
    assert set(rhythm.rooms) == {"two"}


async def test_dynamic_mode_does_not_stack_fixed_message_and_cooldown_gates(
    participant,
):
    participant.settings.update(
        participation_rhythm="maibot",
        participation_frequency=1.0,
        participation_min_messages=6,
        planner_interval_seconds=60,
        participation_cooldown=90,
    )
    first = event("first", text="你觉得呢？")
    assert len(await requests(participant, first)) == 1
    first.set_result(first.plain_result("我的看法是这样。"))
    await participant.record_delivery(first)
    participant.settings["participation_followup"] = False
    second = event("second", text="我有另一个看法", sender="bob")
    assert len(await requests(participant, second)) == 1
    assert second.get_extra("ovenheart_rhythm")["should_trigger"] is True
    assert second.get_extra("ovenheart_rhythm")["actual_replies"] == 1


async def test_dynamic_zero_frequency_is_silent_and_still_tracks_direct_wakes(
    participant,
):
    participant.settings.update(
        participation_rhythm="maibot", participation_frequency=0.0
    )
    assert not await requests(participant, event("quiet", text="小烤你怎么看？"))
    participant.context.llm_generate.assert_not_awaited()
    scope, _, _ = await participant.resolve_scope(event("scope"))
    window = participant.participation.windows[scope]
    assert window["consumed"] == window["sequence"]
    direct = event("direct", text="直接问你一个问题")
    direct.is_at_or_wake_command = True
    await participant.prioritize_direct(direct)
    state = participant.participation.rhythm.rooms[
        participant.participation.room_key(direct)
    ]
    assert len(state.gate._forced_expectations) == 1


async def test_hourly_operator_quota_still_applies_to_dynamic_mode(participant):
    participant.settings.update(
        participation_rhythm="maibot",
        participation_frequency=1.0,
        participation_hourly_limit=1,
    )
    assert len(await requests(participant, event("allowed"))) == 1
    participant.context.llm_generate = AsyncMock(
        side_effect=AssertionError("quota bypass")
    )
    assert not await requests(participant, event("blocked", sender="bob"))


async def test_segmented_native_reply_counts_once_in_dynamic_feedback(participant):
    participant.settings["participation_rhythm"] = "maibot"
    direct = event("segments")
    await participant.capture(direct)
    for text in ("先说一点。", "再补一句。"):
        direct.set_result(direct.plain_result(text))
        await participant.record_delivery(direct)
    state = participant.participation.rhythm.rooms[
        participant.participation.room_key(direct)
    ]
    assert len(state.gate._reply_times) == 1
