"""Bind an authorized native message snapshot to the original summary importer."""

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace

from . import host

_chat = ContextVar("ovenheart_summary_chat", default=None)
_reader = ContextVar("ovenheart_native_chat_reader", default=None)


@contextmanager
def bind_chat_reader(reader, bot_name):
    token = _reader.set((reader, bot_name))
    try:
        yield
    finally:
        _reader.reset(token)


@contextmanager
def bind_chat(messages, bot_name):
    token = _chat.set((messages, bot_name))
    try:
        yield
    finally:
        _chat.reset(token)


def load_prompt(name, locale="zh-CN", **values):
    if name != "a_memorix_chat_summary":
        raise ValueError("Unknown native memory prompt")
    text = (Path(__file__).parent / "prompts" / f"chat_summary.{locale}.txt").read_text(encoding="utf-8")
    return text.format(**values) if values else text


class NativeConfig:
    @property
    def bot(self):
        snapshot = _chat.get()
        live = _reader.get()
        return SimpleNamespace(nickname=snapshot[1] if snapshot else live[1]() if live else "")

    @property
    def personality(self):
        # The native importer is called with include_personality=False.
        return SimpleNamespace(personality="")


global_config = NativeConfig()


class NativeModelConfig:
    def __init__(self):
        self.callbacks = set()

    def get_model_config(self):
        return SimpleNamespace(models_dict={name: task for task in host.get_available_models().values() for name in task.model_list})

    def register_reload_callback(self, callback):
        self.callbacks.add(callback)

    def unregister_reload_callback(self, callback):
        self.callbacks.discard(callback)

    def notify_model_config_changed(self, changed_scopes=()):
        for callback in tuple(self.callbacks):
            callback(changed_scopes)


config_manager = NativeModelConfig()


def get_messages_by_time_in_chat(*, chat_id, start_time, end_time, limit, limit_mode, filter_mai=False, filter_command=False):
    snapshot, live = _chat.get(), _reader.get()
    # Feedback always reads current authorized input, including when a SDK
    # worker was restarted inside a request's temporary summary snapshot.
    rows = live[0](chat_id=chat_id, start_time=start_time, end_time=end_time, limit=limit, limit_mode=limit_mode, filter_mai=filter_mai, filter_command=filter_command) if live and (snapshot is None or filter_mai or filter_command) else snapshot[0] if snapshot else []
    rows = [m for m in rows if start_time <= m["created"] <= end_time and not (filter_mai and m.get("self")) and not (filter_command and m.get("command"))]
    rows = rows[-limit:] if limit_mode == "latest" else rows[:limit]
    return [SimpleNamespace(message_id=m["source_id"], timestamp=m["created"], sender=m["sender"], text=m["text"], processed_plain_text=m["text"]) for m in rows]


def build_readable_messages(messages):
    return "\n".join(f"[{m.message_id}] time={m.timestamp} sender={m.sender}: {m.text}" for m in messages)
