"""Keep a shortened OvenHeart reply's full text in saved history.

``set_result`` stores clamped plain text on ``ovenheart_long_reply_text``.
``RespondStage.process`` optionally wraps ``event.send`` to count delivery.
The save step is the ``long_reply`` optimizer on ``HistoryLabBridge``, which
runs before tool and image cleanup. That bridge captures the current
``_save_to_history``, so this callback composes with group concurrency.

``RespondStage.process`` likewise captures the current method. A forward-retry
wrapper installed earlier or later stays in the same chain.
"""

from __future__ import annotations

import inspect
from copy import copy
from typing import Any

from astrbot.api import logger

from .lab_history import HistoryLabBridge, parse_save_history_call

MAX_LONG_REPLY_TEXT = 20000
LONG_REPLY_EXTRA = "ovenheart_long_reply_text"
LONG_REPLY_SEND_EXTRA = "ovenheart_long_reply_sent"


class LongReplyLabBridge:
    """Restore the captured reply when history would keep a shorter copy."""

    _active: LongReplyLabBridge | None = None
    _event_cls: type | None = None
    _original_set_result: Any = None
    _set_result_wrapper: Any = None
    _respond_stage_cls: type | None = None
    _original_respond_process: Any = None
    _respond_wrapper: Any = None

    def __init__(self, plugin: Any, history: HistoryLabBridge):
        self.plugin = plugin
        self.history = history
        self._installed = False

    @property
    def enabled(self) -> bool:
        return self._installed and type(self)._active is self

    def sync(self, enabled: bool) -> bool:
        if enabled:
            return self.install()
        self.disconnect()
        return False

    def install(self) -> bool:
        if self.enabled:
            return True
        set_ok = self._install_set_result_patch()
        respond_ok = self._install_respond_patch()
        self.history.register_optimizer("long_reply", self.optimize_save_history)
        history_ok = self.history.install()
        if not set_ok and not respond_ok and not history_ok:
            self.history.unregister_optimizer("long_reply")
            logger.warning("OvenHeart 未找到超长回复入口，已跳过安装。")
            return False
        type(self)._active = self
        self._installed = True
        logger.info("OvenHeart 超长回复上下文已启用。")
        return True

    def disconnect(self) -> None:
        cls = type(self)
        if cls._active is self:
            self._restore_set_result()
            self._restore_respond()
            cls._active = None
        self._installed = False
        self.history.unregister_optimizer("long_reply")
        if (
            self.history.enabled
            and not self.history.image_enabled
            and not self.history.tool_enabled
        ):
            self.history.disconnect()

    def record_set_result(self, event: Any, result: Any) -> None:
        if not is_llm_message_chain(result):
            _set_extra(event, LONG_REPLY_EXTRA, None)
            return
        chain = getattr(result, "chain", None)
        if not isinstance(chain, list):
            _set_extra(event, LONG_REPLY_EXTRA, None)
            return
        text = clamp_long_reply_text(extract_plain_text_from_chain(chain))
        _set_extra(event, LONG_REPLY_EXTRA, text or None)

    def optimize_save_history(
        self,
        original: Any,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        parsed = parse_save_history_call(original, args, kwargs)
        if parsed is None:
            return args, kwargs
        all_messages, setter = parsed
        if not isinstance(all_messages, list):
            return args, kwargs
        event = _save_event(original, args, kwargs)
        pending = _get_extra(event, LONG_REPLY_EXTRA)
        if not isinstance(pending, str) or not pending.strip():
            return args, kwargs
        index = find_last_persistable_assistant(all_messages)
        if index is None:
            return args, kwargs
        current = join_text_content(message_content(all_messages[index]))
        chain = _result_chain(event)
        replacement = choose_long_reply_replacement(current, pending.strip(), chain)
        if not replacement or replacement == current:
            return args, kwargs
        updated = list(all_messages)
        updated[index] = replace_message_content_text(all_messages[index], replacement)
        return setter(updated)

    def install_send_tracker(self, event: Any):
        """Best-effort send counter. Failure to wrap leaves the original send."""
        original_send = getattr(event, "send", None)
        if not callable(original_send):
            return _noop

        async def ovenheart_tracked_send(*args: Any, **kwargs: Any) -> Any:
            try:
                result = original_send(*args, **kwargs)
                if inspect.isawaitable(result):
                    result = await result
            except Exception:
                _bump_send_extra(event, failed=True)
                raise
            _bump_send_extra(event, failed=False)
            return result

        try:
            event.send = ovenheart_tracked_send
        except Exception:  # noqa: BLE001
            return _noop

        def restore() -> None:
            if same_callable(getattr(event, "send", None), ovenheart_tracked_send):
                try:
                    event.send = original_send
                except Exception:  # noqa: BLE001
                    pass

        return restore

    def _install_set_result_patch(self) -> bool:
        event_cls = _load_astr_message_event_cls()
        if event_cls is None:
            logger.warning("OvenHeart 未找到消息事件入口，已跳过超长回复记录。")
            return False
        current = getattr(event_cls, "set_result", None)
        if not callable(current):
            logger.warning("OvenHeart 未找到 set_result，已跳过超长回复记录。")
            return False
        current = self._peel_set_result(event_cls, current)
        if not callable(current):
            return False
        cls = type(self)
        original = current
        cls._event_cls = event_cls
        cls._original_set_result = original

        def ovenheart_set_result(event_self: Any, result: Any) -> Any:
            ret = original(event_self, result)
            active = cls._active
            if active is not None:
                try:
                    active.record_set_result(event_self, result)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("OvenHeart 记录超长回复失败: %s", exc)
            return ret

        ovenheart_set_result._ovenheart_long_reply_lab = True  # type: ignore[attr-defined]
        cls._set_result_wrapper = ovenheart_set_result
        event_cls.set_result = ovenheart_set_result
        return True

    def _install_respond_patch(self) -> bool:
        stage_cls = _load_respond_stage_cls()
        if stage_cls is None:
            logger.warning("OvenHeart 未找到发送入口，已跳过超长回复发送跟踪。")
            return False
        current = getattr(stage_cls, "process", None)
        if not callable(current):
            logger.warning("OvenHeart 未找到 RespondStage.process，已跳过超长回复发送跟踪。")
            return False
        if inspect.isasyncgenfunction(current):
            logger.warning("OvenHeart 检测到 RespondStage.process 为异步生成器，已跳过超长回复发送跟踪。")
            return False
        current = self._peel_respond(stage_cls, current)
        if not callable(current) or inspect.isasyncgenfunction(current):
            return False
        cls = type(self)
        original = current
        cls._respond_stage_cls = stage_cls
        cls._original_respond_process = original

        async def ovenheart_long_reply_process(stage_self: Any, event: Any) -> Any:
            active = cls._active
            restore_send = None
            if active is not None:
                try:
                    restore_send = active.install_send_tracker(event)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("OvenHeart 超长回复发送跟踪未挂上: %s", exc)
                    restore_send = None
            try:
                processed = original(stage_self, event)
                if inspect.isawaitable(processed):
                    return await processed
                return processed
            finally:
                if callable(restore_send):
                    restore_send()

        ovenheart_long_reply_process._ovenheart_long_reply_lab = True  # type: ignore[attr-defined]
        cls._respond_wrapper = ovenheart_long_reply_process
        stage_cls.process = ovenheart_long_reply_process
        return True

    def _peel_set_result(self, event_cls: type, current: Any) -> Any:
        cls = type(self)
        wrapper = cls._set_result_wrapper
        if wrapper is None or not same_callable(current, wrapper):
            return current
        underlying = cls._original_set_result
        cls._set_result_wrapper = None
        cls._original_set_result = None
        cls._event_cls = None
        if callable(underlying) and not same_callable(underlying, wrapper):
            event_cls.set_result = underlying
            return underlying
        return None

    def _peel_respond(self, stage_cls: type, current: Any) -> Any:
        cls = type(self)
        wrapper = cls._respond_wrapper
        if wrapper is None or not same_callable(current, wrapper):
            return current
        underlying = cls._original_respond_process
        cls._respond_wrapper = None
        cls._original_respond_process = None
        cls._respond_stage_cls = None
        if callable(underlying) and not same_callable(underlying, wrapper):
            stage_cls.process = underlying
            return underlying
        return None

    def _restore_set_result(self) -> None:
        cls = type(self)
        event_cls = cls._event_cls
        original = cls._original_set_result
        wrapper = cls._set_result_wrapper
        if event_cls is not None and original is not None and wrapper is not None:
            current = getattr(event_cls, "set_result", None)
            if same_callable(current, wrapper):
                event_cls.set_result = original
        cls._event_cls = None
        cls._original_set_result = None
        cls._set_result_wrapper = None

    def _restore_respond(self) -> None:
        cls = type(self)
        stage_cls = cls._respond_stage_cls
        original = cls._original_respond_process
        wrapper = cls._respond_wrapper
        if stage_cls is not None and original is not None and wrapper is not None:
            current = getattr(stage_cls, "process", None)
            if same_callable(current, wrapper):
                stage_cls.process = original
        cls._respond_stage_cls = None
        cls._original_respond_process = None
        cls._respond_wrapper = None


def choose_long_reply_replacement(current: str, pending: str, chain: Any) -> str | None:
    """Return ``pending`` when it is longer, or when a forward node truncated it.

    A shorter captured reply does not replace text that is already longer.
    """
    if not pending or pending == current:
        return None
    forward_truncated = False
    if isinstance(chain, list) and has_forward_nodes(chain):
        forward_text = extract_plain_text_from_chain(chain)
        forward_truncated = bool(forward_text) and len(pending) > len(forward_text) and (
            not current or current == forward_text or pending.startswith(current)
        )
    if len(pending) > len(current) or (forward_truncated and len(pending) >= len(current)):
        return pending
    return None


def find_last_persistable_assistant(messages: list[Any]) -> int | None:
    """Return the latest assistant turn that can be rewritten.

    Tool-call and temporary assistant messages are not persistable. The search
    stops there so an earlier turn is not overwritten with this reply.
    """
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message_role(message) != "assistant":
            continue
        if message_no_save(message) or message_has_tool_calls(message):
            return None
        return index
    return None


def extract_plain_text_from_chain(chain: list[Any]) -> str:
    parts: list[str] = []

    def walk(comp: Any) -> None:
        text = getattr(comp, "text", None)
        if isinstance(text, str):
            parts.append(text)
            return
        content = getattr(comp, "content", None)
        if isinstance(content, list):
            for child in content:
                walk(child)
        nodes = getattr(comp, "nodes", None)
        if isinstance(nodes, list):
            for node in nodes:
                walk(node)

    for item in chain:
        walk(item)
    return "".join(parts).strip()


def has_forward_nodes(chain: list[Any]) -> bool:
    def walk(comp: Any) -> bool:
        comp_type = normalize_component_type(getattr(comp, "type", ""))
        if comp_type == "nodes" and isinstance(getattr(comp, "nodes", None), list):
            return True
        content = getattr(comp, "content", None)
        if comp_type == "node" and isinstance(content, list):
            return True
        nodes = getattr(comp, "nodes", None)
        if isinstance(nodes, list):
            return any(walk(node) for node in nodes)
        if isinstance(content, list):
            return any(walk(child) for child in content)
        return False

    return any(walk(comp) for comp in chain)


def normalize_component_type(value: Any) -> str:
    if value is None:
        return ""
    raw = str(value)
    if "." in raw:
        raw = raw.rsplit(".", 1)[-1]
    return raw.lower()


def clamp_long_reply_text(text: str) -> str:
    text = str(text or "").strip()
    if len(text) <= MAX_LONG_REPLY_TEXT:
        return text
    return text[:MAX_LONG_REPLY_TEXT]


def replace_message_content_text(message: Any, text: str) -> Any:
    if isinstance(message, dict):
        copied = dict(message)
        copied["content"] = replace_content_text(copied.get("content"), text)
        return copied
    return clone_message(message, content=replace_content_text(message_content(message), text))


def replace_content_text(content: Any, text: str) -> Any:
    if isinstance(content, str) or content is None:
        return text
    if not isinstance(content, list):
        return content
    copied_parts = list(content)
    for index, part in enumerate(copied_parts):
        if is_text_part(part):
            copied_parts[index] = clone_text_part(part, text)
            return copied_parts
    copied_parts.append(create_text_part(text))
    return copied_parts


def is_text_part(part: Any) -> bool:
    if isinstance(part, dict):
        return part.get("type") == "text" and "text" in part
    return str(getattr(part, "type", "")) == "text" and hasattr(part, "text")


def create_text_part(text: str) -> Any:
    try:
        from astrbot.core.agent.message import TextPart
    except Exception:  # noqa: BLE001
        return {"type": "text", "text": text}
    try:
        return TextPart(text=text)
    except Exception:  # noqa: BLE001
        return {"type": "text", "text": text}


def clone_text_part(part: Any, text: str) -> Any:
    if isinstance(part, dict):
        copied = dict(part)
        copied["text"] = text
        return copied
    if hasattr(part, "model_copy"):
        try:
            return part.model_copy(update={"text": text}, deep=True)
        except TypeError:
            return part.model_copy(update={"text": text})
    try:
        copied = copy_message(part)
        copied.text = text
        return copied
    except Exception:  # noqa: BLE001
        return part


def clone_message(message: Any, *, content: Any) -> Any:
    if hasattr(message, "model_copy"):
        try:
            return message.model_copy(update={"content": content}, deep=True)
        except TypeError:
            try:
                return message.model_copy(update={"content": content})
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass
    try:
        copied = copy_message(message)
        copied.content = content
        return copied
    except Exception:  # noqa: BLE001
        return message


def copy_message(message: Any) -> Any:
    return copy(message)


def join_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if isinstance(part, dict):
            text = part.get("text")
        else:
            text = getattr(part, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def message_content(message: Any) -> Any:
    if isinstance(message, dict):
        return message.get("content")
    return getattr(message, "content", None)


def message_role(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("role") or "")
    return str(getattr(message, "role", "") or "")


def message_no_save(message: Any) -> bool:
    if isinstance(message, dict):
        return bool(message.get("_no_save"))
    return bool(getattr(message, "_no_save", False))


def message_has_tool_calls(message: Any) -> bool:
    if isinstance(message, dict):
        return bool(message.get("tool_calls"))
    return bool(getattr(message, "tool_calls", None))


def is_llm_message_chain(result: Any) -> bool:
    if result is None or isinstance(result, str):
        return False
    checker = getattr(result, "is_llm_result", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:  # noqa: BLE001
            return False
    content_type = getattr(result, "result_content_type", None)
    if content_type is None:
        return False
    return "LLM_RESULT" in str(getattr(content_type, "name", content_type))


def same_callable(first: Any, second: Any) -> bool:
    if first is second:
        return True
    return getattr(first, "__func__", first) is getattr(second, "__func__", second) and getattr(
        first, "__self__", None
    ) is getattr(second, "__self__", None)


def _save_event(original: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    event = kwargs.get("event")
    if event is None and len(args) > 1:
        event = args[1]
    try:
        signature = inspect.signature(original)
        bound = signature.bind_partial(*args, **kwargs)
    except Exception:  # noqa: BLE001
        return event
    return bound.arguments.get("event", event)


def _result_chain(event: Any) -> Any:
    result = _safe_call(getattr(event, "get_result", None))
    return getattr(result, "chain", None)


def _bump_send_extra(event: Any, *, failed: bool) -> None:
    current = _get_extra(event, LONG_REPLY_SEND_EXTRA)
    if not isinstance(current, dict):
        current = {"ok": 0, "failed": 0}
    else:
        current = dict(current)
    key = "failed" if failed else "ok"
    try:
        current[key] = int(current.get(key, 0)) + 1
    except (TypeError, ValueError):
        current[key] = 1
    _set_extra(event, LONG_REPLY_SEND_EXTRA, current)


def _set_extra(event: Any, key: str, value: Any) -> None:
    setter = getattr(event, "set_extra", None)
    if callable(setter):
        try:
            setter(key, value)
            return
        except Exception:  # noqa: BLE001
            pass
    extras = getattr(event, "_extras", None)
    if isinstance(extras, dict):
        extras[key] = value


def _get_extra(event: Any, key: str, default: Any = None) -> Any:
    getter = getattr(event, "get_extra", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            try:
                value = getter(key)
            except Exception:  # noqa: BLE001
                return default
            return default if value is None else value
        except Exception:  # noqa: BLE001
            return default
    extras = getattr(event, "_extras", None)
    if isinstance(extras, dict):
        return extras.get(key, default)
    return default


def _safe_call(func: Any) -> Any:
    if not callable(func):
        return None
    try:
        return func()
    except Exception:  # noqa: BLE001
        return None


def _noop() -> None:
    return None


def _load_astr_message_event_cls() -> type | None:
    try:
        from astrbot.core.platform.astr_message_event import AstrMessageEvent
    except Exception:  # noqa: BLE001
        return None
    return AstrMessageEvent


def _load_respond_stage_cls() -> type | None:
    try:
        from astrbot.core.pipeline.respond.stage import RespondStage
    except Exception:  # noqa: BLE001
        return None
    return RespondStage
