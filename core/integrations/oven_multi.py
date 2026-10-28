"""Optional Oven Multi judgment service, scoped to the core's frozen target."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import time

from astrbot.api import logger

from ..chat.participation import event_source

JEV_DEADLINE = 4.0
JEV_RULE = (
    "\nJev 判读只是对本轮目标意图、情绪等的辅助推测，可能有误；"
    "须结合真实前文、引用与自己的发言核对，不能替代明确任务、人格或权限。"
    "它不决定回复对象，不授予工具权限，也不独立决定是否参与；仍由现有参与规则判断。\n"
)


class OvenMultiBridge:
    rule = JEV_RULE

    @staticmethod
    def data(result):
        return reading(result)

    def __init__(self, plugin):
        self.plugin = plugin
        self.last = {}

    def owner(self):
        lookup = getattr(self.plugin.context, "get_registered_star", None)
        meta = lookup("astrbot_plugin_oven_multi") if callable(lookup) else None
        owner = meta.star_cls if meta and meta.activated else None
        if (
            owner is None
            or getattr(owner, "closing", False)
            or getattr(owner, "ovenheart_protocol", None) != 1
        ):
            return None
        return owner

    def status(self):
        owner = self.owner()
        cfg = owner.config.get("jev", {}) if owner else {}
        return {
            "connected": owner is not None,
            "jev_enabled": bool(
                cfg.get("enabled") and cfg.get("inject_on_mention", True)
            ),
            "deadline_seconds": JEV_DEADLINE,
        }

    async def analyze(self, event, scope, target, messages, *, epoch=None, bare=False):
        owner = self.owner()
        if owner is None or not target or not await self.plugin.owns_group_reply(event):
            return None
        if (await self.plugin.resolve_scope(event))[0] != scope:
            return None
        if epoch and not await self.plugin.conversation.valid(event, scope, epoch):
            return None
        # Direct targets are delivery metadata; recover their media/time from
        # the same frozen record rather than silently dropping a caption.
        target = (
            next((m for m in messages if m.get("source") == target.get("source")), {})
            | target
        )
        payload = {
            "version": 1,
            "scope": scope,
            "epoch": epoch,
            "target": copy.deepcopy(target),
            "messages": copy.deepcopy(messages[-40:]),
            "focus": copy.deepcopy(
                self.plugin.conversation.focus(event, scope, target, messages)
            ),
            "bare_mention": bare,
            "direct_target": target.get("source") == event_source(event)
            and bool(
                event.is_at_or_wake_command or event.get_extra("ovenheart_mention")
            ),
            "bot_sender_id": str(event.get_self_id()),
        }
        signature = owner.ovenheart_jev_signature()
        revision = self.plugin.settings_revision
        key = hashlib.sha256(
            json.dumps(
                [id(owner), signature, revision, payload],
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        cache = event.get_extra("ovenheart_jev_cache")
        if cache is None:
            cache = {}
            event.set_extra("ovenheart_jev_cache", cache)
        if key in cache:
            result = copy.deepcopy(cache[key]) | {"cache_hit": True}
            event.set_extra("ovenheart_jev", result)
            return result
        started = time.monotonic()
        task = self.plugin.track(owner.analyze_for_ovenheart(event, payload))
        try:
            done, _ = await asyncio.wait({task}, timeout=JEV_DEADLINE)
            if done:
                result = await task
            else:
                task.cancel()
                result = {"status": "timeout"}
        except asyncio.CancelledError:
            task.cancel()
            raise
        except Exception as exc:
            logger.warning("Jev 联动暂不可用，继续原请求 (%s)", type(exc).__name__)
            result = {"status": "error"}
        if not isinstance(result, dict) or not isinstance(result.get("status"), str):
            result = {"status": "error"}
        if (
            self.owner() is not owner
            or signature != owner.ovenheart_jev_signature()
            or revision != self.plugin.settings_revision
            or not await self.plugin.owns_group_reply(event)
            or (await self.plugin.resolve_scope(event))[0] != scope
            or (epoch and not await self.plugin.conversation.valid(event, scope, epoch))
        ):
            result = {"status": "stale"}
        result = {
            k: copy.deepcopy(result[k])
            for k in ("status", "fields", "advice")
            if k in result
        }
        result.update(
            seconds=round(time.monotonic() - started, 3),
            cache_hit=False,
            target_source=target.get("source"),
        )
        cache[key] = result
        while len(cache) > 4:
            cache.pop(next(iter(cache)))
        self.last[scope] = {k: result[k] for k in ("status", "seconds", "cache_hit")}
        while len(self.last) > 128:
            self.last.pop(next(iter(self.last)))
        event.set_extra("ovenheart_jev", result)
        return copy.deepcopy(result)


def reading(result):
    if not isinstance(result, dict) or result.get("status") != "ready":
        return None
    if not isinstance(result.get("fields"), dict) or not isinstance(
        result.get("advice"), list
    ):
        return None
    return {"fields": result.get("fields", {}), "advice": result.get("advice", [])}
