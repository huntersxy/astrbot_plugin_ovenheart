"""Retry failed aiocqhttp forward sends by splitting nodes.

``ForwardLabBridge`` wraps the current ``RespondStage.process``. Install
captures that method, so a long-reply wrapper already on the stage stays
underneath and the two chains compose. Disconnect restores it only while this
bridge still owns the outermost method.

On aiocqhttp the wrapper installs a temporary ``event.send``. A forward send
that fails with a known forward error is split adaptively and resent. The
default per-node hard limit is 1200 characters.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

from astrbot.api import logger

FORWARD_NODE_TARGET_LENGTH = 1000
FORWARD_NODE_HARD_LIMIT = 1200
DEFAULT_FORWARD_SPLIT_WORDS = ["。", "？", "！", "~", "…", "\n"]
FORWARD_SEND_FAILURE_KEYWORDS = (
    "send_private_forward_msg",
    "send_group_forward_msg",
    "发送转发消息",
    "合并转发消息失败",
    "发送合并转发消息失败",
    "res_id",
)
FORWARD_RETRY_EXTRA = "ovenheart_forward_retry_recovered"


class ForwardLabBridge:
    """Split a failed merged-forward send into smaller node batches."""

    _active: ForwardLabBridge | None = None
    _stage_cls: type | None = None
    _original_process: Any = None
    _process_wrapper: Any = None

    def __init__(self, plugin: Any, hard_limit: Any = FORWARD_NODE_HARD_LIMIT):
        self.plugin = plugin
        self.hard_limit = sanitize_positive_int(hard_limit, FORWARD_NODE_HARD_LIMIT)
        self.target_length = min(FORWARD_NODE_TARGET_LENGTH, self.hard_limit)
        self._installed = False

    @property
    def enabled(self) -> bool:
        return self._installed and type(self)._active is self

    def sync(self, enabled: bool, hard_limit: Any = None) -> bool:
        if hard_limit is not None:
            self.configure(hard_limit=hard_limit)
        if enabled:
            return self.install()
        self.disconnect()
        return False

    def configure(self, *, hard_limit: Any) -> None:
        hard = sanitize_positive_int(hard_limit, FORWARD_NODE_HARD_LIMIT)
        target = min(FORWARD_NODE_TARGET_LENGTH, hard)
        self.hard_limit = hard
        self.target_length = target

    def install(self) -> bool:
        if self.enabled:
            return True
        stage_cls = _load_respond_stage_cls()
        if stage_cls is None:
            logger.warning("OvenHeart 未找到 RespondStage，已跳过合并转发重试。")
            return False
        current = getattr(stage_cls, "process", None)
        if not callable(current):
            logger.warning("OvenHeart 未找到 RespondStage.process，已跳过合并转发重试。")
            return False
        if inspect.isasyncgenfunction(current):
            logger.warning("OvenHeart 检测到 RespondStage.process 为异步生成器，已跳过合并转发重试。")
            return False
        current = self._peel_own_wrapper(stage_cls, current)
        if not callable(current) or inspect.isasyncgenfunction(current):
            logger.warning("OvenHeart 合并转发重试无法重建发送入口，已跳过。")
            return False
        cls = type(self)
        original = current
        cls._stage_cls = stage_cls
        cls._original_process = original

        async def ovenheart_forward_process(stage_self: Any, event: Any) -> Any:
            active = cls._active
            restore_send = None
            if active is not None:
                restore_send = active.install_forward_send_retry(event)
            try:
                processed = original(stage_self, event)
                if inspect.isawaitable(processed):
                    return await processed
                return processed
            finally:
                if callable(restore_send):
                    restore_send()

        ovenheart_forward_process._ovenheart_forward_lab = True  # type: ignore[attr-defined]
        cls._process_wrapper = ovenheart_forward_process
        stage_cls.process = ovenheart_forward_process
        cls._active = self
        self._installed = True
        logger.info(
            "OvenHeart 已启用合并转发失败重试: target=%s, hard_limit=%s",
            self.target_length,
            self.hard_limit,
        )
        return True

    def disconnect(self) -> None:
        cls = type(self)
        if cls._active is self:
            self._restore_process()
        self._installed = False

    def install_forward_send_retry(self, event: Any):
        if _safe_call(getattr(event, "get_platform_name", None)) != "aiocqhttp":
            return _noop
        original_send = getattr(event, "send", None)
        if not callable(original_send):
            return _noop

        async def ovenheart_forward_retry_send(*args: Any, **kwargs: Any) -> Any:
            try:
                result = original_send(*args, **kwargs)
                if inspect.isawaitable(result):
                    return await result
                return result
            except Exception as exc:
                message = get_send_message_arg(args, kwargs)
                if not self._should_retry_forward_send(event, message, exc):
                    raise
                return await self.retry_forward_send(
                    event,
                    original_send,
                    args,
                    kwargs,
                    message,
                    exc,
                )

        try:
            event.send = ovenheart_forward_retry_send
        except Exception:  # noqa: BLE001
            return _noop

        def restore() -> None:
            if same_callable(getattr(event, "send", None), ovenheart_forward_retry_send):
                try:
                    event.send = original_send
                except Exception:  # noqa: BLE001
                    pass

        return restore

    async def retry_forward_send(
        self,
        event: Any,
        original_send: Any,
        original_args: tuple[Any, ...],
        original_kwargs: dict[str, Any],
        message: Any,
        original_exc: Exception,
    ) -> Any:
        components = _load_message_components()
        if components is None:
            raise original_exc
        node_cls, nodes_cls, plain_cls = components
        split_chain = split_single_forward_component(message, node_cls, nodes_cls)
        if split_chain is None:
            raise original_exc
        forward_comp, _prefix_components, suffix_components = split_chain
        if isinstance(forward_comp, nodes_cls):
            nodes = list(getattr(forward_comp, "nodes", []) or [])
        elif isinstance(forward_comp, node_cls):
            nodes = [forward_comp]
        else:
            nodes = []
        if not nodes:
            raise original_exc
        logger.warning(
            "OvenHeart 检测到合并转发发送失败，开始自适应拆包重试: nodes=%s, error=%s",
            len(nodes),
            original_exc,
        )
        recovered_failures = 1
        recovered_failures += await self._send_nodes_adaptively(
            original_send,
            original_args,
            original_kwargs,
            message,
            nodes,
            node_cls=node_cls,
            nodes_cls=nodes_cls,
            plain_cls=plain_cls,
            known_failed_whole=True,
            include_non_forward=False,
        )
        await self._send_suffix_components(
            original_send,
            original_args,
            original_kwargs,
            message,
            suffix_components,
        )
        _increment_event_extra(event, FORWARD_RETRY_EXTRA, amount=recovered_failures)
        logger.info("OvenHeart 合并转发自适应拆包重试完成: nodes=%s", len(nodes))
        return None

    async def _send_nodes_adaptively(
        self,
        original_send: Any,
        original_args: tuple[Any, ...],
        original_kwargs: dict[str, Any],
        base_message: Any,
        nodes: list[Any],
        *,
        node_cls: type,
        nodes_cls: type,
        plain_cls: type,
        known_failed_whole: bool = False,
        include_non_forward: bool = False,
    ) -> int:
        if not nodes:
            return 0
        recovered_failures = 0
        if len(nodes) == 1:
            if not known_failed_whole:
                try:
                    await call_send_with_message(
                        original_send,
                        original_args,
                        original_kwargs,
                        derive_retry_message(
                            base_message,
                            [nodes_cls([nodes[0]])],
                            include_non_forward=include_non_forward,
                        ),
                    )
                    return recovered_failures
                except Exception as exc:
                    if not self._is_forward_send_failure(exc):
                        raise
                    recovered_failures += 1
                    logger.warning(
                        "OvenHeart 单节点合并转发仍发送失败，降级为普通分段: error=%s",
                        exc,
                    )
            await self._send_node_as_plain_chunks(
                original_send,
                original_args,
                original_kwargs,
                base_message,
                nodes[0],
                plain_cls=plain_cls,
                include_non_forward=include_non_forward,
            )
            return recovered_failures

        if not known_failed_whole:
            try:
                await call_send_with_message(
                    original_send,
                    original_args,
                    original_kwargs,
                    derive_retry_message(
                        base_message,
                        [nodes_cls(nodes)],
                        include_non_forward=include_non_forward,
                    ),
                )
                return recovered_failures
            except Exception as exc:
                if not self._is_forward_send_failure(exc):
                    raise
                recovered_failures += 1
                logger.debug(
                    "OvenHeart 合并转发整包发送失败，开始继续拆分: nodes=%s, error=%s",
                    len(nodes),
                    exc,
                )

        for prefix_size in range(len(nodes) - 1, 0, -1):
            prefix_nodes = nodes[:prefix_size]
            remaining_nodes = nodes[prefix_size:]
            try:
                await call_send_with_message(
                    original_send,
                    original_args,
                    original_kwargs,
                    derive_retry_message(
                        base_message,
                        [nodes_cls(prefix_nodes)],
                        include_non_forward=include_non_forward,
                    ),
                )
            except Exception as exc:
                if not self._is_forward_send_failure(exc):
                    raise
                recovered_failures += 1
                logger.debug(
                    "OvenHeart 合并转发子包发送失败，继续缩小: nodes=%s, error=%s",
                    prefix_size,
                    exc,
                )
                if prefix_size == 1:
                    await self._send_node_as_plain_chunks(
                        original_send,
                        original_args,
                        original_kwargs,
                        base_message,
                        prefix_nodes[0],
                        plain_cls=plain_cls,
                        include_non_forward=include_non_forward,
                    )
                    recovered_failures += await self._send_nodes_adaptively(
                        original_send,
                        original_args,
                        original_kwargs,
                        base_message,
                        remaining_nodes,
                        node_cls=node_cls,
                        nodes_cls=nodes_cls,
                        plain_cls=plain_cls,
                        include_non_forward=False,
                    )
                    return recovered_failures
                continue
            recovered_failures += await self._send_nodes_adaptively(
                original_send,
                original_args,
                original_kwargs,
                base_message,
                remaining_nodes,
                node_cls=node_cls,
                nodes_cls=nodes_cls,
                plain_cls=plain_cls,
                include_non_forward=False,
            )
            return recovered_failures
        return recovered_failures

    async def _send_node_as_plain_chunks(
        self,
        original_send: Any,
        original_args: tuple[Any, ...],
        original_kwargs: dict[str, Any],
        base_message: Any,
        node: Any,
        *,
        plain_cls: type,
        include_non_forward: bool = False,
    ) -> None:
        content = getattr(node, "content", None)
        if not isinstance(content, list) or not content:
            raise RuntimeError("合并转发节点内容为空，无法降级普通分段。")
        chunks = split_plain_fallback_content(
            content,
            plain_cls=plain_cls,
            target_length=self.target_length,
            hard_limit=self.hard_limit,
            split_pattern=build_forward_split_pattern(None),
        )
        if not chunks:
            raise RuntimeError("合并转发节点无可发送内容，无法降级普通分段。")
        for chunk in chunks:
            await call_send_with_message(
                original_send,
                original_args,
                original_kwargs,
                derive_retry_message(
                    base_message,
                    chunk,
                    include_non_forward=include_non_forward,
                ),
            )
            include_non_forward = False

    async def _send_suffix_components(
        self,
        original_send: Any,
        original_args: tuple[Any, ...],
        original_kwargs: dict[str, Any],
        base_message: Any,
        suffix_components: list[Any],
    ) -> None:
        if not suffix_components:
            return
        await call_send_with_message(
            original_send,
            original_args,
            original_kwargs,
            derive_retry_message(base_message, suffix_components),
        )

    def _should_retry_forward_send(self, event: Any, message: Any, exc: Exception) -> bool:
        if _safe_call(getattr(event, "get_platform_name", None)) != "aiocqhttp":
            return False
        components = _load_message_components()
        if components is None:
            return False
        node_cls, nodes_cls, _plain_cls = components
        if split_single_forward_component(message, node_cls, nodes_cls) is None:
            return False
        return self._is_forward_send_failure(exc)

    def _is_forward_send_failure(self, exc: Exception) -> bool:
        text = stringify_exception(exc)
        compact = text.replace(" ", "")
        if "retcode=1200" in compact or "retcode':1200" in compact:
            return True
        if '"retcode":1200' in compact or "'retcode':1200" in compact:
            return True
        return any(keyword in text for keyword in FORWARD_SEND_FAILURE_KEYWORDS)

    def _peel_own_wrapper(self, stage_cls: type, current: Any) -> Any:
        cls = type(self)
        wrapper = cls._process_wrapper
        if wrapper is None or not same_callable(current, wrapper):
            return current
        underlying = cls._original_process
        cls._process_wrapper = None
        cls._original_process = None
        cls._stage_cls = None
        cls._active = None
        if callable(underlying) and not same_callable(underlying, wrapper):
            stage_cls.process = underlying
            return underlying
        return None

    def _restore_process(self) -> None:
        cls = type(self)
        stage_cls = cls._stage_cls
        original = cls._original_process
        wrapper = cls._process_wrapper
        if stage_cls is not None and original is not None and wrapper is not None:
            current = getattr(stage_cls, "process", None)
            if same_callable(current, wrapper):
                stage_cls.process = original
        cls._stage_cls = None
        cls._original_process = None
        cls._process_wrapper = None
        cls._active = None


def sanitize_positive_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        sanitized = int(value)
    except (TypeError, ValueError):
        return default
    if sanitized <= 0:
        return default
    return sanitized


def build_forward_split_pattern(split_words: Any) -> re.Pattern | None:
    if isinstance(split_words, list):
        words = [word for word in split_words if isinstance(word, str) and word]
    else:
        words = list(DEFAULT_FORWARD_SPLIT_WORDS)
    if "\n" not in words:
        words.append("\n")
    if not words:
        return None
    escaped_words = sorted((re.escape(word) for word in words), key=len, reverse=True)
    return re.compile(f"(?:{'|'.join(escaped_words)})+")


def find_forward_split_pos(
    text: str,
    target_len: int,
    hard_limit: int,
    split_pattern: re.Pattern | None,
) -> int:
    search_end = min(hard_limit, len(text))
    if len(text) <= target_len:
        return len(text)
    if split_pattern is not None:
        previous_end = 0
        for match in split_pattern.finditer(text, 0, search_end):
            if match.end() >= target_len:
                return match.end()
            if match.end() > 0:
                previous_end = match.end()
        if previous_end > 0:
            return previous_end
    if len(text) > hard_limit:
        return hard_limit
    return search_end


def split_single_forward_component(
    message: Any,
    node_cls: type,
    nodes_cls: type,
) -> tuple[Any, list[Any], list[Any]] | None:
    chain = getattr(message, "chain", None)
    if not isinstance(chain, list):
        return None
    forward_index = -1
    forward_comp = None
    for index, comp in enumerate(chain):
        if not isinstance(comp, (node_cls, nodes_cls)):
            continue
        if forward_comp is not None:
            return None
        forward_index = index
        forward_comp = comp
    if forward_comp is None:
        return None
    return forward_comp, chain[:forward_index], chain[forward_index + 1 :]


def get_send_message_arg(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    if args:
        return args[0]
    return kwargs.get("message")


async def call_send_with_message(
    send_func: Any,
    original_args: tuple[Any, ...],
    original_kwargs: dict[str, Any],
    message: Any,
) -> Any:
    args = list(original_args)
    kwargs = dict(original_kwargs)
    if args:
        args[0] = message
        result = send_func(*args, **kwargs)
    elif "message" in kwargs:
        kwargs["message"] = message
        result = send_func(**kwargs)
    else:
        result = send_func(message, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def derive_message(
    base_message: Any,
    chain: list[Any],
    *,
    include_non_forward: bool = False,
) -> Any:
    if include_non_forward:
        base_chain = getattr(base_message, "chain", None)
        if isinstance(base_chain, list):
            new_chain: list[Any] = []
            replaced = False
            for comp in base_chain:
                if not replaced and is_forward_component_like(comp):
                    new_chain.extend(chain)
                    replaced = True
                elif not is_forward_component_like(comp):
                    new_chain.append(comp)
            if replaced:
                chain = new_chain
    derive = getattr(base_message, "derive", None)
    if callable(derive):
        try:
            return derive(chain)
        except Exception:  # noqa: BLE001
            pass
    try:
        return type(base_message)(chain)
    except Exception:  # noqa: BLE001
        return base_message


def derive_retry_message(
    base_message: Any,
    chain: list[Any],
    *,
    include_non_forward: bool = False,
) -> Any:
    message = derive_message(base_message, chain, include_non_forward=include_non_forward)
    if message is base_message:
        raise RuntimeError("无法构造合并转发重试消息，已停止补发以避免重复发送原始整包。")
    return message


def is_forward_component_like(comp: Any) -> bool:
    comp_type = str(getattr(comp, "type", "") or "").lower()
    if comp_type in {"node", "nodes", "componenttype.node", "componenttype.nodes"}:
        return True
    if isinstance(getattr(comp, "nodes", None), list):
        return True
    return isinstance(getattr(comp, "content", None), list) and hasattr(comp, "uin")


def split_plain_fallback_content(
    content: list[Any],
    *,
    plain_cls: type,
    target_length: int,
    hard_limit: int,
    split_pattern: re.Pattern | None,
) -> list[list[Any]]:
    chunks: list[list[Any]] = []
    current: list[Any] = []
    current_text_len = 0

    def flush_current() -> None:
        nonlocal current, current_text_len
        if current:
            chunks.append(current)
        current = []
        current_text_len = 0

    for comp in content:
        if not isinstance(comp, plain_cls):
            flush_current()
            chunks.append([comp])
            continue
        rest = getattr(comp, "text", "") or ""
        while rest:
            if current_text_len >= target_length:
                flush_current()
            remaining_target = max(1, target_length - current_text_len)
            remaining_hard = max(1, hard_limit - current_text_len)
            split_pos = find_forward_split_pos(
                rest,
                remaining_target,
                remaining_hard,
                split_pattern,
            )
            split_pos = max(1, min(split_pos, remaining_hard, len(rest)))
            current.append(plain_cls(rest[:split_pos]))
            current_text_len += split_pos
            rest = rest[split_pos:]
            if rest:
                flush_current()
    flush_current()
    return chunks


def stringify_exception(exc: Exception) -> str:
    parts = [repr(exc), str(exc)]
    result = getattr(exc, "result", None)
    if result is not None:
        parts.append(repr(result))
    return " ".join(parts)


def same_callable(first: Any, second: Any) -> bool:
    if first is second:
        return True
    return getattr(first, "__func__", first) is getattr(second, "__func__", second) and getattr(
        first, "__self__", None
    ) is getattr(second, "__self__", None)


def _increment_event_extra(event: Any, key: str, *, amount: int = 1) -> None:
    if event is None or amount <= 0:
        return
    current = 0
    getter = getattr(event, "get_extra", None)
    if callable(getter):
        try:
            current = int(getter(key, 0) or 0)
        except Exception:  # noqa: BLE001
            current = 0
    setter = getattr(event, "set_extra", None)
    if callable(setter):
        try:
            setter(key, current + amount)
            return
        except Exception:  # noqa: BLE001
            pass
    extras = getattr(event, "_extras", None)
    if isinstance(extras, dict):
        extras[key] = current + amount


def _safe_call(func: Any) -> Any:
    if not callable(func):
        return None
    try:
        return func()
    except Exception:  # noqa: BLE001
        return None


def _noop() -> None:
    return None


def _load_respond_stage_cls() -> type | None:
    try:
        from astrbot.core.pipeline.respond.stage import RespondStage
    except Exception:  # noqa: BLE001
        return None
    return RespondStage


def _load_message_components() -> tuple[type, type, type] | None:
    try:
        from astrbot.core.message.components import Node, Nodes, Plain
    except Exception:  # noqa: BLE001
        return None
    return Node, Nodes, Plain
