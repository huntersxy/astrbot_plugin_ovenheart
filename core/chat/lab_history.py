"""Experimental history sanitizers owned by OvenHeart.

``HistoryLabBridge`` installs one wrapper on
``InternalAgentSubStage._save_to_history``. ``install`` captures whatever
method is current and keeps it as the original, so a patch already installed
by group concurrency (or another OvenHeart hook) stays underneath and the
chains compose: optimizers run, then that captured method.

Toggling calls ``install`` again. If this bridge's wrapper is still the
outermost method, it is peeled first and the underlying callable is captured
again, so a rebuild does not wrap itself. Disconnect restores the captured
original only while this bridge still owns the outermost method.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from copy import copy
from dataclasses import replace
from typing import Any

from astrbot.api import logger

IMAGE_HISTORY_PLACEHOLDER = "[历史图片：已省略原始图像，仅保留占位符]"
TOOL_HISTORY_PLACEHOLDER = "[历史工具结果：已省略原始内容，最终结论见后续助手回复]"

OPTIMIZER_ORDER = ("long_reply", "tool", "image")

Optimizer = Callable[[Any, tuple[Any, ...], dict[str, Any]], Any]


class _ToolHistoryScanError(Exception):
    """工具历史扫描无法安全完成。"""


class HistoryLabBridge:
    """One reversible ``_save_to_history`` wrapper for ordered lab optimizers."""

    _active: HistoryLabBridge | None = None
    _internal_stage_cls: type | None = None
    _original_save_to_history: Any = None
    _save_history_wrapper: Any = None

    def __init__(self, plugin: Any):
        self.plugin = plugin
        self.image_enabled = False
        self.tool_enabled = False
        self._installed = False
        self._optimizers: dict[str, Optimizer] = {
            "tool": self._optimize_tool,
            "image": self._optimize_image,
        }

    @property
    def enabled(self) -> bool:
        return self._installed and type(self)._active is self

    def register_optimizer(self, name: str, fn: Optimizer) -> None:
        """Register a save-history optimizer.

        ``fn(original, args, kwargs)`` may be sync or async and must return
        ``(args, kwargs)``. Execution order is ``long_reply``, ``tool``,
        ``image``, then any other registered name.
        """
        updated = dict(self._optimizers)
        updated[str(name)] = fn
        ordered: dict[str, Optimizer] = {}
        for key in OPTIMIZER_ORDER:
            if key in updated:
                ordered[key] = updated.pop(key)
        ordered.update(updated)
        self._optimizers = ordered

    def unregister_optimizer(self, name: str) -> None:
        if name in ("tool", "image"):
            return
        self._optimizers.pop(name, None)

    def sync(self, image: bool = False, tool: bool = False) -> bool:
        """Install or tear down the shared history wrapper."""
        self.image_enabled = bool(image)
        self.tool_enabled = bool(tool)
        if self._should_install():
            return self.install()
        self.disconnect()
        return False

    def install(self) -> bool:
        """Install or rebuild the wrapper around the current save method.

        An active bridge is left in place so a later group-concurrency wrapper
        stays outside this one. Turning the bridge off and on again peels a
        leftover OvenHeart wrapper, then captures whatever method is live.
        """
        if self.enabled:
            return True
        stage = _load_internal_stage_cls()
        if stage is None:
            logger.warning("OvenHeart 未找到历史保存入口，已跳过历史上下文优化。")
            return False
        current = getattr(stage, "_save_to_history", None)
        if not callable(current):
            logger.warning("OvenHeart 未找到 _save_to_history，已跳过历史上下文优化。")
            return False
        current = self._peel_own_wrapper(stage, current)
        if not callable(current):
            logger.warning("OvenHeart 历史上下文优化无法重建保存入口，已跳过。")
            return False
        cls = type(self)
        original = current
        cls._internal_stage_cls = stage
        cls._original_save_to_history = original

        async def ovenheart_save_to_history(*args: Any, **kwargs: Any) -> Any:
            active = cls._active
            if active is not None:
                try:
                    args, kwargs = await active.apply_optimizers(original, args, kwargs)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("OvenHeart 历史上下文优化失败，已回退原保存: %s", exc)
            return await original(*args, **kwargs)

        ovenheart_save_to_history._ovenheart_history_lab = True  # type: ignore[attr-defined]
        cls._save_history_wrapper = ovenheart_save_to_history
        stage._save_to_history = ovenheart_save_to_history
        cls._active = self
        self._installed = True
        logger.info("OvenHeart 历史上下文优化已启用。")
        return True

    def disconnect(self) -> None:
        cls = type(self)
        if cls._active is self:
            self._restore_save_history()
        self._installed = False

    def sanitize_request(self, req: Any) -> None:
        """Apply enabled sanitizers to ``contexts`` and ``conversation.history``."""
        if req is None:
            return
        if self.tool_enabled:
            _sanitize_request_field(req, sanitize_tool_messages)
        if self.image_enabled:
            _sanitize_request_field(req, sanitize_image_messages)

    async def apply_optimizers(
        self,
        original: Any,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        names = [name for name in OPTIMIZER_ORDER if name in self._optimizers]
        names.extend(name for name in self._optimizers if name not in names)
        for name in names:
            if not self._optimizer_enabled(name):
                continue
            fn = self._optimizers.get(name)
            if not callable(fn):
                continue
            try:
                result = fn(original, args, kwargs)
                if inspect.isawaitable(result):
                    result = await result
                if isinstance(result, tuple) and len(result) == 2:
                    args, kwargs = result
            except Exception as exc:  # noqa: BLE001
                logger.warning("OvenHeart 历史优化 %s 失败: %s", name, exc)
        return args, kwargs

    def _should_install(self) -> bool:
        if self.image_enabled or self.tool_enabled:
            return True
        return callable(self._optimizers.get("long_reply"))

    def _optimizer_enabled(self, name: str) -> bool:
        if name == "image":
            return self.image_enabled
        if name == "tool":
            return self.tool_enabled
        return name in self._optimizers

    def _optimize_tool(
        self,
        original: Any,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        return _optimize_saved_messages(original, args, kwargs, sanitize_tool_messages)

    def _optimize_image(
        self,
        original: Any,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        return _optimize_saved_messages(original, args, kwargs, sanitize_image_messages)

    def _peel_own_wrapper(self, stage: type, current: Any) -> Any:
        cls = type(self)
        wrapper = cls._save_history_wrapper
        if wrapper is None or not same_callable(current, wrapper):
            return current
        underlying = cls._original_save_to_history
        cls._save_history_wrapper = None
        cls._original_save_to_history = None
        cls._internal_stage_cls = None
        cls._active = None
        if callable(underlying) and not same_callable(underlying, wrapper):
            stage._save_to_history = underlying
            return underlying
        return None

    def _restore_save_history(self) -> None:
        cls = type(self)
        stage = cls._internal_stage_cls
        original = cls._original_save_to_history
        wrapper = cls._save_history_wrapper
        if stage is not None and original is not None and wrapper is not None:
            current = getattr(stage, "_save_to_history", None)
            if same_callable(current, wrapper):
                stage._save_to_history = original
        cls._internal_stage_cls = None
        cls._original_save_to_history = None
        cls._save_history_wrapper = None
        cls._active = None


def sanitize_image_messages(messages: list[Any]) -> tuple[list[Any], bool]:
    changed = False
    sanitized_messages = list(messages)
    for index, message in enumerate(sanitized_messages):
        sanitized_message, message_changed = _sanitize_image_message(message)
        if message_changed:
            sanitized_messages[index] = sanitized_message
            changed = True
    return (sanitized_messages if changed else messages), changed


def sanitize_tool_messages(messages: list[Any]) -> tuple[list[Any], bool]:
    """压缩已完成且已被后续助手消费的工具结果。扫描失败时保留原历史。"""
    try:
        return _sanitize_tool_messages(messages)
    except Exception:  # noqa: BLE001
        return messages, False


def find_completed_tool_result_groups(messages: list[Any]) -> list[list[int]]:
    """找出结构完整且已被后续 assistant 消费的工具结果组。"""
    groups: list[list[int]] = []
    expected_ids: set[str] | None = None
    result_indexes: dict[str, int] = {}
    pending_invalid = False
    max_steps_prompt, interruption_prompt = load_tool_loop_prompt_constants()
    if max_steps_prompt is None or interruption_prompt is None:
        return groups

    def reset_pending() -> None:
        nonlocal expected_ids, result_indexes, pending_invalid
        expected_ids = None
        result_indexes = {}
        pending_invalid = False

    for index, message in enumerate(messages):
        role = message_value(message, "role")
        if role == "assistant":
            if (
                expected_ids
                and not pending_invalid
                and set(result_indexes) == expected_ids
                and is_valid_consumer_assistant(message, interruption_prompt)
                and not message_checkpoint_after(message)
            ):
                groups.append(sorted(result_indexes.values()))
            reset_pending()
            if not message_no_save(message) and not message_checkpoint_after(message):
                tool_call_ids = extract_tool_call_ids(message)
                if isinstance(tool_call_ids, list) and tool_call_ids:
                    expected_ids = set(tool_call_ids)
            continue
        if role == "_checkpoint":
            reset_pending()
            continue
        if expected_ids is not None:
            if role == "tool":
                tool_call_id = message_value(message, "tool_call_id")
                if (
                    not isinstance(tool_call_id, str)
                    or not tool_call_id.strip()
                    or tool_call_id not in expected_ids
                    or tool_call_id in result_indexes
                ):
                    pending_invalid = True
                else:
                    result_indexes[tool_call_id] = index
            elif role == "user" and set(result_indexes) == expected_ids and (
                is_tool_image_user_message(message)
                or is_max_steps_prompt_message(message, max_steps_prompt)
            ):
                pass
            else:
                reset_pending()
        if message_checkpoint_after(message):
            reset_pending()
    return groups


def load_tool_loop_prompt_constants() -> tuple[str | None, str | None]:
    """延迟读取宿主工具循环提示。导入失败时保持保守，不压缩工具结果。"""
    try:
        from astrbot.core.agent.runners.tool_loop_agent_runner import (
            ToolLoopAgentRunner,
        )

        max_steps_prompt = ToolLoopAgentRunner.MAX_STEPS_REACHED_PROMPT
        interruption_prompt = ToolLoopAgentRunner.USER_INTERRUPTION_MESSAGE
    except Exception:  # noqa: BLE001
        return None, None
    if not isinstance(max_steps_prompt, str) or not max_steps_prompt:
        return None, None
    if not isinstance(interruption_prompt, str) or not interruption_prompt:
        return None, None
    return max_steps_prompt, interruption_prompt


def is_valid_consumer_assistant(message: Any, interruption_prompt: str | None) -> bool:
    """判断 assistant 是否真正消费了上一组工具结果。"""
    if message_no_save(message):
        return False
    tool_call_ids = extract_tool_call_ids(message)
    if tool_call_ids:
        return True
    return has_persistable_assistant_text(message, interruption_prompt)


def has_persistable_assistant_text(message: Any, interruption_prompt: str | None) -> bool:
    content = message_value(message, "content")
    for text, part in iter_text_values(content):
        if part is not None and part_no_save(part):
            continue
        stripped = text.strip()
        if not stripped:
            continue
        if text == IMAGE_HISTORY_PLACEHOLDER:
            continue
        if interruption_prompt is not None and text == interruption_prompt:
            continue
        return True
    return False


def iter_text_values(content: Any) -> list[tuple[str, Any | None]]:
    """提取会进入历史的文本块，忽略思考、图片、音频和未知块。"""
    if isinstance(content, str):
        return [(content, None)]
    if isinstance(content, (list, tuple)):
        values: list[tuple[str, Any | None]] = []
        for part in content:
            values.extend(iter_text_part(part))
        return values
    return iter_text_part(content)


def iter_text_part(part: Any) -> list[tuple[str, Any | None]]:
    if isinstance(part, str):
        return [(part, None)]
    part_type = message_value(part, "type")
    if part is None or part_type != "text":
        return []
    text = message_value(part, "text")
    if not isinstance(text, str):
        return []
    return [(text, part)]


def is_max_steps_prompt_message(message: Any, max_steps_prompt: str | None) -> bool:
    role = message_value(message, "role")
    if max_steps_prompt is None or role != "user":
        return False
    content = message_value(message, "content")
    if isinstance(content, str):
        return content == max_steps_prompt
    text_values = iter_text_values(content)
    if len(text_values) != 1:
        return False
    text, part = text_values[0]
    return part is not None and not part_no_save(part) and text == max_steps_prompt


def is_tool_image_user_message(message: Any) -> bool:
    content = message_value(message, "content")
    if not isinstance(content, (list, tuple)):
        return False
    has_marker = False
    has_image = False
    for part in content:
        part_type = message_value(part, "type")
        if part_type == "image_url":
            has_image = True
        if part_type == "text":
            text = message_value(part, "text")
            if isinstance(text, str) and text.startswith("[Image from tool '"):
                has_marker = True
            if text == IMAGE_HISTORY_PLACEHOLDER:
                has_image = True
    return has_marker and has_image


def message_value(message: Any, name: str) -> Any:
    try:
        if isinstance(message, dict):
            return message.get(name)
        return getattr(message, name, None)
    except Exception as exc:  # noqa: BLE001
        raise _ToolHistoryScanError(f"读取消息属性 {name} 失败") from exc


def message_no_save(message: Any) -> bool:
    value = message_value(message, "_no_save")
    try:
        return bool(value)
    except Exception as exc:  # noqa: BLE001
        raise _ToolHistoryScanError("读取消息临时标记失败") from exc


def message_checkpoint_after(message: Any) -> bool:
    return message_value(message, "_checkpoint_after") is not None


def extract_tool_call_ids(message: Any) -> list[str] | None:
    tool_calls = message_value(message, "tool_calls")
    if not isinstance(tool_calls, (list, tuple)) or not tool_calls:
        return None
    tool_call_ids: list[str] = []
    for tool_call in tool_calls:
        tool_call_id = message_value(tool_call, "id")
        if not isinstance(tool_call_id, str) or not tool_call_id.strip():
            return None
        tool_call_ids.append(tool_call_id)
    if len(set(tool_call_ids)) != len(tool_call_ids):
        return None
    return tool_call_ids


def part_no_save(part: Any) -> bool:
    value = message_value(part, "_no_save")
    try:
        return bool(value)
    except Exception as exc:  # noqa: BLE001
        raise _ToolHistoryScanError("读取文本块临时标记失败") from exc


def parse_history_value(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except Exception:  # noqa: BLE001
            return None
        if isinstance(parsed, list):
            return parsed
    return None


def serialize_history_like(original: Any, contexts: list[Any]) -> Any:
    if isinstance(original, str):
        return json.dumps(contexts, ensure_ascii=False)
    return contexts


def parse_save_history_call(
    original_method: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[Any, Callable[[Any], tuple[tuple[Any, ...], dict[str, Any]]]] | None:
    if "all_messages" in kwargs:

        def set_kwarg(value: Any) -> tuple[tuple[Any, ...], dict[str, Any]]:
            updated_kwargs = dict(kwargs)
            updated_kwargs["all_messages"] = value
            return args, updated_kwargs

        return kwargs["all_messages"], set_kwarg

    if len(args) > 4:

        def set_positional(value: Any) -> tuple[tuple[Any, ...], dict[str, Any]]:
            updated_args = list(args)
            updated_args[4] = value
            return tuple(updated_args), kwargs

        return args[4], set_positional

    try:
        signature = inspect.signature(original_method)
        bound = signature.bind_partial(*args, **kwargs)
    except Exception:  # noqa: BLE001
        return None
    if "all_messages" not in bound.arguments:
        return None

    def set_bound(value: Any) -> tuple[tuple[Any, ...], dict[str, Any]]:
        bound.arguments["all_messages"] = value
        return rebuild_call_from_bound(signature, bound, args, kwargs)

    return bound.arguments["all_messages"], set_bound


def rebuild_call_from_bound(
    signature: inspect.Signature,
    bound: inspect.BoundArguments,
    original_args: tuple[Any, ...],
    original_kwargs: dict[str, Any],
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    param_names = list(signature.parameters)
    args = list(original_args)
    kwargs = dict(original_kwargs)
    for name, value in bound.arguments.items():
        if name in kwargs:
            kwargs[name] = value
            continue
        try:
            index = param_names.index(name)
        except ValueError:
            continue
        if index < len(args):
            args[index] = value
        else:
            kwargs[name] = value
    return tuple(args), kwargs


def same_callable(first: Any, second: Any) -> bool:
    if first is second:
        return True
    return getattr(first, "__func__", first) is getattr(second, "__func__", second) and getattr(
        first, "__self__", None
    ) is getattr(second, "__self__", None)


def _sanitize_request_field(req: Any, sanitizer: Callable[[list[Any]], tuple[list[Any], bool]]) -> None:
    contexts = parse_history_value(getattr(req, "contexts", None))
    if contexts is not None:
        sanitized, changed = sanitizer(contexts)
        if changed:
            try:
                req.contexts = sanitized
            except Exception:  # noqa: BLE001
                logger.warning("OvenHeart 写回请求上下文失败。")
    conversation = getattr(req, "conversation", None)
    raw_history = getattr(conversation, "history", None)
    history_contexts = parse_history_value(raw_history)
    if history_contexts is None:
        return
    sanitized_history, changed = sanitizer(history_contexts)
    if not changed:
        return
    try:
        conversation.history = serialize_history_like(raw_history, sanitized_history)
    except Exception:  # noqa: BLE001
        logger.warning("OvenHeart 写回会话历史失败。")


def _optimize_saved_messages(
    original: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    sanitizer: Callable[[list[Any]], tuple[list[Any], bool]],
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    parsed = parse_save_history_call(original, args, kwargs)
    if parsed is None:
        return args, kwargs
    all_messages, setter = parsed
    if not isinstance(all_messages, list):
        return args, kwargs
    sanitized, changed = sanitizer(all_messages)
    if not changed:
        return args, kwargs
    return setter(sanitized)


def _sanitize_image_message(message: Any) -> tuple[Any, bool]:
    if isinstance(message, dict):
        content = message.get("content")
        sanitized_content, changed = _sanitize_image_content(content)
        if not changed:
            return message, False
        sanitized = dict(message)
        sanitized["content"] = sanitized_content
        return sanitized, True
    content = getattr(message, "content", None)
    sanitized_content, changed = _sanitize_image_content(content)
    if not changed:
        return message, False
    return _clone_image_message(message, content=sanitized_content), True


def _sanitize_image_content(content: Any) -> tuple[Any, bool]:
    if not isinstance(content, list):
        return content, False
    changed = False
    sanitized_parts = list(content)
    for index, part in enumerate(sanitized_parts):
        sanitized_part, part_changed = _sanitize_image_part(part)
        if part_changed:
            sanitized_parts[index] = sanitized_part
            changed = True
    return (sanitized_parts if changed else content), changed


def _sanitize_image_part(part: Any) -> tuple[Any, bool]:
    if not is_base64_image_part(part):
        return part, False
    return create_text_part_like(part, IMAGE_HISTORY_PLACEHOLDER), True


def is_base64_image_part(part: Any) -> bool:
    if isinstance(part, dict):
        part_type = str(part.get("type", ""))
        image_url = part.get("image_url")
    else:
        part_type = str(getattr(part, "type", ""))
        image_url = getattr(part, "image_url", None)
    if part_type != "image_url":
        return False
    url = extract_image_url(image_url)
    if not isinstance(url, str):
        return False
    metadata = url.lstrip().lower().split(",", 1)[0]
    return metadata.startswith("data:image/") and ";base64" in metadata


def extract_image_url(image_url: Any) -> str | None:
    if isinstance(image_url, dict):
        url = image_url.get("url")
        return url if isinstance(url, str) else None
    url = getattr(image_url, "url", None)
    return url if isinstance(url, str) else None


def create_text_part_like(original_part: Any, text: str) -> Any:
    if isinstance(original_part, dict):
        replacement = {"type": "text", "text": text}
        if original_part.get("_no_save"):
            replacement["_no_save"] = True
        return replacement
    try:
        from astrbot.core.agent.message import TextPart
    except Exception:  # noqa: BLE001
        TextPart = None  # type: ignore[assignment]
    if TextPart is not None:
        try:
            return inherit_no_save(original_part, TextPart(text=text))
        except Exception:  # noqa: BLE001
            pass
    part = type("OvenHeartImageHistoryTextPart", (), {})()
    part.type = "text"
    part.text = text
    return inherit_no_save(original_part, part)


def inherit_no_save(original_part: Any, replacement_part: Any) -> Any:
    if not bool(getattr(original_part, "_no_save", False)):
        return replacement_part
    marker = getattr(replacement_part, "mark_as_temp", None)
    if callable(marker):
        try:
            marked = marker()
            if marked is not None:
                replacement_part = marked
        except Exception:  # noqa: BLE001
            pass
    try:
        replacement_part._no_save = True
    except Exception:  # noqa: BLE001
        pass
    return replacement_part


def _clone_image_message(message: Any, *, content: Any) -> Any:
    try:
        return replace(message, content=content)
    except Exception:  # noqa: BLE001
        pass
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
        copied = message.__class__.__new__(message.__class__)
        copied.__dict__.update(getattr(message, "__dict__", {}))
        copied.content = content
        return copied
    except Exception:  # noqa: BLE001
        try:
            message.content = content
        except Exception:  # noqa: BLE001
            pass
        return message


def _sanitize_tool_messages(messages: list[Any]) -> tuple[list[Any], bool]:
    groups = find_completed_tool_result_groups(messages)
    if not groups:
        return messages, False
    replacements: dict[int, Any] = {}
    for indexes in groups:
        group_replacements: dict[int, Any] = {}
        group_changed = False
        for index in indexes:
            message = messages[index]
            content = message_value(message, "content")
            if content == TOOL_HISTORY_PLACEHOLDER:
                sanitized_message = message
                message_changed = False
            elif isinstance(message, dict):
                sanitized_message = dict(message)
                sanitized_message["content"] = TOOL_HISTORY_PLACEHOLDER
                message_changed = True
            else:
                sanitized_message = clone_tool_message(message, TOOL_HISTORY_PLACEHOLDER)
                if sanitized_message is None:
                    raise _ToolHistoryScanError("无法安全复制工具结果消息")
                message_changed = True
            group_replacements[index] = sanitized_message
            group_changed = group_changed or message_changed
        if group_changed:
            replacements.update(group_replacements)
    if not replacements:
        return messages, False
    sanitized_messages = list(messages)
    for index, sanitized_message in replacements.items():
        sanitized_messages[index] = sanitized_message
    return sanitized_messages, True


def sanitize_tool_message(message: Any) -> tuple[Any, bool]:
    if isinstance(message, dict):
        if message.get("role") != "tool":
            return message, False
        if message.get("content") == TOOL_HISTORY_PLACEHOLDER:
            return message, False
        sanitized = dict(message)
        sanitized["content"] = TOOL_HISTORY_PLACEHOLDER
        return sanitized, True
    if getattr(message, "role", None) != "tool":
        return message, False
    if getattr(message, "content", None) == TOOL_HISTORY_PLACEHOLDER:
        return message, False
    sanitized = clone_tool_message(message, TOOL_HISTORY_PLACEHOLDER)
    if sanitized is None:
        return message, False
    return sanitized, True


def clone_tool_message(message: Any, content: str) -> Any | None:
    """复制消息后替换 content。复制失败时不修改运行中的对象。"""
    try:
        return replace(message, content=content)
    except Exception:  # noqa: BLE001
        pass
    try:
        model_copy = getattr(message, "model_copy", None)
    except Exception:  # noqa: BLE001
        return None
    if callable(model_copy):
        try:
            return model_copy(update={"content": content}, deep=True)
        except TypeError:
            try:
                return model_copy(update={"content": content})
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass
    try:
        cloned = copy(message)
        cloned.content = content
        return cloned
    except Exception:  # noqa: BLE001
        return None


def _load_internal_stage_cls() -> type | None:
    try:
        from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import (
            InternalAgentSubStage,
        )

        return InternalAgentSubStage
    except Exception:  # noqa: BLE001
        return None
