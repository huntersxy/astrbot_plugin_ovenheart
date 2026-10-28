"""Keep the host's single group transcript ordered while images are captioned."""

from __future__ import annotations

import inspect
from uuid import uuid4

from astrbot.api import logger
from astrbot.api.platform import MessageType
from astrbot.core.agent.message import TextPart
from astrbot.core.message.components import Image


class GroupContextBridge:
    """Reserve native records before slow formatting; retain no second transcript."""

    def __init__(self, plugin):
        self.plugin = plugin
        self._owner = None
        self._hooks = []

    def disconnect(self):
        """Restore only instance methods still owned by this adapter."""
        for owner, name, original, wrapper, local in self._hooks:
            if getattr(owner, name, None) is wrapper:
                if local:
                    setattr(owner, name, original)
                else:
                    delattr(owner, name)
        self._hooks.clear()
        self._owner = None

    def install(self) -> bool:
        """Attach to a supported built-in instance without clearing its queues."""
        get_stars = getattr(self.plugin.context, "get_all_stars", None)
        if not callable(get_stars):
            self.disconnect()
            return False
        candidates = [
            getattr(getattr(meta, "star_cls", None), "group_chat_context", None)
            for meta in get_stars()
            if getattr(meta, "activated", False)
        ]
        candidates = [
            owner
            for owner in candidates
            if owner is not None
            and type(owner).__module__
            == "astrbot.builtin_stars.astrbot.group_chat_context"
        ]
        if len(candidates) != 1:
            self.disconnect()
            return False
        owner = candidates[0]
        if self._owner is owner and all(
            getattr(current, name, None) is wrapper
            for current, name, _, wrapper, _ in self._hooks
        ):
            return True
        self.disconnect()
        if not all(
            hasattr(owner, name)
            for name in (
                "raw_records",
                "_record_ids",
                "_get_lock",
                "cfg",
                "_format_message",
            )
        ):
            return False
        originals = {}
        for name, parameters in (
            ("handle_message", ("event",)),
            ("on_req_llm", ("event", "req")),
        ):
            original = getattr(owner, name, None)
            if (
                not inspect.iscoroutinefunction(original)
                or getattr(original, "__self__", None) is not owner
                or original.__module__ != type(owner).__module__
                or tuple(inspect.signature(original).parameters) != parameters
            ):
                return False
            originals[name] = original
        format_block = getattr(
            inspect.getmodule(type(owner)), "_format_group_history_block", None
        )
        if not callable(format_block):
            return False

        async def ordered(event):
            if (
                self._owner is not owner
                or event.get_message_type() != MessageType.GROUP_MESSAGE
                or not await self.plugin.permitted(event)
            ):
                return await originals["handle_message"](event)
            cfg = owner.cfg(event)
            # Native formatting with captions disabled has no provider wait.
            # Keep its names, quotes, cards and image markers intact.
            fallback = await owner._format_message(
                event, cfg | {"image_caption": False}
            )
            umo = event.unified_msg_origin
            record_id = uuid4().hex
            async with owner._get_lock(umo):
                records, ids = owner.raw_records[umo], owner._record_ids[umo]
                records.append(fallback)
                ids.append(record_id)
                while len(records) > cfg["group_message_max_cnt"]:
                    records.popleft()
                    ids.popleft()
                event.set_extra("_group_context_record_id", record_id)
                event.set_extra("_group_context_raw_idx", len(records) - 1)
            if not cfg["image_caption"] or not any(
                isinstance(part, Image) for part in event.get_messages()
            ):
                return
            # The original inbound task owns captioning and cancellation. A later
            # request can already see this image's placeholder in the right slot.
            final = await owner._format_message(event, cfg)
            if self._owner is not owner:
                return
            if final.count("[Image:") < sum(
                isinstance(part, Image) for part in event.get_messages()
            ):
                # The native formatter catches caption failures and can omit the
                # image entirely. Retain the truthful placeholder in that case.
                return
            async with owner._get_lock(umo):
                ids = owner._record_ids.get(umo, ())
                if record_id in ids:
                    owner.raw_records[umo][ids.index(record_id)] = final
                # Consumed, trimmed or reset records must never reappear next turn.

        async def bounded(event, req):
            if self._owner is not owner or not await self.plugin.permitted(event):
                return await originals["on_req_llm"](event, req)
            umo = event.unified_msg_origin
            record_id = event.get_extra("_group_context_record_id")
            async with owner._get_lock(umo):
                records = owner.raw_records.get(umo)
                ids = owner._record_ids.get(umo)
                if not records:
                    return
                if isinstance(record_id, str):
                    if not ids or record_id not in ids:
                        return
                    index = ids.index(record_id)
                else:
                    index = event.get_extra("_group_context_raw_idx", -1)
                    if not isinstance(index, int) or not 0 <= index < len(records):
                        return
                selected = list(records)[:index]
                remaining = list(records)[index + 1 :]
                remaining_ids = list(ids)[index + 1 :] if ids else []
                records.clear()
                records.extend(remaining)
                if ids is not None:
                    ids.clear()
                    ids.extend(remaining_ids)
            if selected:
                req.extra_user_content_parts.append(
                    TextPart(text=format_block(selected))
                )

        self._owner = owner
        for name, wrapper in (("handle_message", ordered), ("on_req_llm", bounded)):
            self._hooks.append(
                (owner, name, originals[name], wrapper, name in vars(owner))
            )
            setattr(owner, name, wrapper)
        logger.info("OvenHeart installed native group-context ordering adapter")
        return True

    def status(self) -> dict:
        """Expose adapter readiness without chat contents or additional storage."""
        return {"ordered": self.install(), "transcript": "native"}
