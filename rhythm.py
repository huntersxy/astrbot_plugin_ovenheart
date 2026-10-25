"""AstrBot event adapter for MaiBot's actual dynamic reply gate and fitted score."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from .vendor.maibot.dynamic_gate import DynamicReplyGate
from .vendor.maibot.reply_likelihood import (
    DEFAULT_SECONDS_SINCE_BOT_MESSAGE,
    ReplyLikelihoodInput,
    estimate_reply_probability,
    summarize_pending_texts,
)


@dataclass
class RoomRhythm:
    gate: DynamicReplyGate = field(default_factory=DynamicReplyGate)
    round_sources: set[str] = field(default_factory=set)
    # Receipt time orders late callbacks; original time measures activity.
    # Keep no message body or separate transcript in the scheduler.
    activity: deque = field(default_factory=deque)
    seen: set[str] = field(default_factory=set)
    last_own: float | None = None

    def prune(self, now):
        while self.activity and self.activity[0][0] < now - 300:
            _, source, _, _ = self.activity.popleft()
            self.seen.discard(source)


class ConversationRhythm:
    def __init__(self):
        self.rooms: dict[str, RoomRhythm] = {}

    def observe(self, room, source, created, *, own=False, forced=False, now=None):
        if not room:
            return
        now = time.time() if now is None else now
        state = self.rooms.setdefault(room, RoomRhythm())
        state.prune(now)
        if source not in state.seen:
            state.activity.append((now, source, created, own))
            state.seen.add(source)
            if own:
                state.gate.record_reply(now)
                state.last_own = max(created, state.last_own or created)
        if forced and state.gate.take_uncounted_message_ids([source]):
            state.gate.record_forced_turn(now)

    def evaluate(
        self, room, pending, frequency, *, bot_names=(), has_at_bot=False, now=None
    ):
        now = time.time() if now is None else now
        state = self.rooms.setdefault(room, RoomRhythm())
        state.prune(now)
        human = [m for m in pending if not m.get("self") and m.get("replyable", True)]
        if not human:
            return None
        recent = [entry for entry in state.activity if 0 <= now - entry[2] <= 300]
        last_own = state.last_own

        def score(messages):
            texts = [m["text"] for m in messages]
            at_other, question, media_only = summarize_pending_texts(
                texts, has_at_bot=has_at_bot
            )
            return estimate_reply_probability(
                ReplyLikelihoodInput(
                    mention_bot=any(
                        name.casefold() in text.casefold()
                        for name in bot_names
                        if name
                        for text in texts
                    ),
                    at_other=at_other,
                    has_question_mark=question,
                    placeholder_only=media_only,
                    recent_self_ratio=sum(entry[3] for entry in recent)
                    / max(1, len(recent)),
                    recent_message_count=len(recent),
                    seconds_since_bot_message=max(0, now - last_own)
                    if last_own is not None
                    else DEFAULT_SECONDS_SINCE_BOT_MESSAGE,
                    pending_count=len(messages),
                )
            )

        new_sources = set(
            state.gate.take_uncounted_message_ids([m["source"] for m in human])
        )
        if new_sources:
            if not state.gate.is_round_open(now):
                state.round_sources.clear()
            state.round_sources.update(new_sources)
            state.gate.record_proactive_demand(
                score([m for m in human if m["source"] in state.round_sources]), now
            )
        decision = state.gate.evaluate(score(human), frequency, now)
        if decision.should_trigger:
            state.gate.close_round()
        return decision

    def forget(self, room):
        self.rooms.pop(room, None)
