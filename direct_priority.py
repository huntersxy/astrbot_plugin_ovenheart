"""Reserve explicit wakes before the host's follow-up capture and session lock."""

from __future__ import annotations

import asyncio
import weakref


class DirectPriority:
    def __init__(self, plugin):
        self.plugin = plugin
        self.entries = {}

    def watch(self, event, *, direct):
        task = asyncio.current_task()
        if task is None:
            return
        key = id(event)
        previous = self.entries.get(key)
        if previous and previous["event"]() is event and previous["task"]() is task:
            previous["direct"] = direct
            return
        entry = {
            "event": weakref.ref(event),
            "task": weakref.ref(task),
            "room": self.plugin.participation.room_key(event),
            "direct": direct,
        }
        self.entries[key] = entry

        def finished(_):
            if self.entries.get(key) is entry:
                self.entries.pop(key, None)

        task.add_done_callback(finished)

    def occupied(self, event):
        room = self.plugin.participation.room_key(event)
        for key, entry in list(self.entries.items()):
            owner, task = entry["event"](), entry["task"]()
            if owner is None or task is None or task.done():
                self.entries.pop(key, None)
                continue
            if entry["direct"] and entry["room"] == room and not owner.is_stopped():
                return True
        return False

    def preempt(self, event):
        room = self.plugin.participation.room_key(event)
        current = asyncio.current_task()
        stopped = []
        for key, entry in list(self.entries.items()):
            owner, task = entry["event"](), entry["task"]()
            if owner is None or task is None or task.done():
                self.entries.pop(key, None)
                continue
            if entry["direct"] or entry["room"] != room or owner is event:
                continue
            if owner.get_extra("ovenheart_priority_preempted"):
                continue
            owner.set_extra("ovenheart_priority_preempted", True)
            owner.set_extra("agent_stop_requested", True)
            owner.stop_event()
            owner.clear_result()
            # Only the owned autonomous pipeline is cancelled, once. Its native
            # finally blocks release the session lock and close the provider work.
            if task is not current and not task.cancelling():
                task.cancel()
            stopped.append(owner)
        self.watch(event, direct=True)
        return stopped
