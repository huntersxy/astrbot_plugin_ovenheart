"""Reject empty private events and keep one active turn plus the latest waiter."""

import asyncio
import weakref

from astrbot.api import logger
from astrbot.core.message.components import At, Plain, Reply

from ..message_source import own_message_text


def empty_private_input(event):
    return (
        event.is_private_chat()
        and not own_message_text(event).strip()
        and not any(
            not isinstance(part, (Plain, At, Reply)) for part in event.get_messages()
        )
    )


class PrivateReplyGuard:
    def __init__(self):
        self.rooms = {}

    @staticmethod
    def cancel(entry):
        event, task = entry["event"](), entry["task"]()
        if event is not None:
            event.set_extra("agent_stop_requested", True)
            event.stop_event()
            event.clear_result()
        if task is not None and not task.done() and not task.cancelling():
            task.cancel()

    async def admit(self, event):
        if event.get_extra("ovenheart_private_admitted"):
            return
        event.set_extra("ovenheart_private_admitted", True)
        if empty_private_input(event):
            raw = event.message_obj.raw_message
            raw = raw if isinstance(raw, dict) else {}
            logger.info(
                "忽略空私聊事件 (post_type=%s, notice_type=%s, sub_type=%s)",
                str(raw.get("post_type", ""))[:40],
                str(raw.get("notice_type", ""))[:40],
                str(raw.get("sub_type", ""))[:40],
            )
            event.stop_event()
            event.clear_result()
            return
        room = event.unified_msg_origin
        text = own_message_text(event).strip().strip("。.!！?？~～，,、 ").lower()
        stopping = text in {
            "停",
            "停止",
            "别说了",
            "不要说了",
            "不要再说了",
            "停止回复",
            "别回复了",
            "别再回复了",
            "闭嘴",
            "stop",
        }
        entries, lock = self.rooms.get(room, ({}, asyncio.Lock()))
        for entry in list(entries.values()):
            if stopping or not entry["active"]:
                self.cancel(entry)
        if stopping:
            event.stop_event()
            event.clear_result()
            logger.info("私聊停止指令已取消在途和排队回复")
            return
        self.rooms[room] = entries, lock
        task = asyncio.current_task()
        event.set_extra("ovenheart_native_task", weakref.ref(task))
        entry = {
            "event": weakref.ref(event),
            "task": weakref.ref(task),
            "active": False,
        }
        entries[id(event)] = entry

        def finished(_):
            entries.pop(id(event), None)
            if entry["active"]:
                lock.release()
            if not entries and self.rooms.get(room, (None,))[0] is entries:
                self.rooms.pop(room, None)

        task.add_done_callback(finished)
        await lock.acquire()
        entry["active"] = True

    async def close(self):
        tasks = []
        for entries, _ in list(self.rooms.values()):
            for entry in list(entries.values()):
                self.cancel(entry)
                task = entry["task"]()
                if task is not None:
                    tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.rooms.clear()
