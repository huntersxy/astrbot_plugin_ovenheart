"""Experimental DeepSeek v4 request cleanup owned by OvenHeart.

The payload wrapper captures the current ``ProviderOpenAIOfficial``
``_finally_convert_payload``. Disconnect restores it only while this bridge
still owns that method.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger


@dataclass
class CleanResult:
    contexts: list[Any]
    removed_by_rule: dict[str, int] = field(default_factory=dict)

    @property
    def removed_count(self) -> int:
        return sum(self.removed_by_rule.values())


class ContextRule:
    name: str

    def should_remove(self, message: dict[str, Any]) -> bool:
        raise NotImplementedError


class EmptyAssistantRule(ContextRule):
    name = "empty_assistant"

    def should_remove(self, message: dict[str, Any]) -> bool:
        return (
            is_assistant(message)
            and not has_effective_tool_calls(message)
            and is_empty_content(message.get("content"))
            and not message.get("reasoning_content")
        )


class ReasoningOnlyAssistantRule(ContextRule):
    name = "reasoning_only_assistant"

    def should_remove(self, message: dict[str, Any]) -> bool:
        return (
            is_assistant(message)
            and not has_effective_tool_calls(message)
            and is_empty_content(message.get("content"))
            and bool(message.get("reasoning_content"))
        )


class ThinkOnlyAssistantRule(ContextRule):
    name = "think_only_assistant"

    def should_remove(self, message: dict[str, Any]) -> bool:
        return (
            is_assistant(message)
            and not has_effective_tool_calls(message)
            and is_think_only_content(message.get("content"))
        )


class DeepSeekLabBridge:
    """Drop assistant turns that trip DeepSeek v4, and fill reasoning_content."""

    _active: DeepSeekLabBridge | None = None
    _provider_cls: type | None = None
    _original_finally_convert_payload: Any = None
    _payload_wrapper: Any = None

    def __init__(self, plugin: Any):
        self.plugin = plugin
        self.rules: list[ContextRule] = [
            EmptyAssistantRule(),
            ReasoningOnlyAssistantRule(),
            ThinkOnlyAssistantRule(),
        ]
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
        provider_cls = _load_openai_provider_cls()
        if provider_cls is None:
            logger.warning("OvenHeart 未找到 OpenAI Provider，已跳过 DeepSeek v4 兼容补丁。")
            return False
        current = getattr(provider_cls, "_finally_convert_payload", None)
        if not callable(current):
            logger.warning("OvenHeart 未找到 OpenAI payload 转换入口，已跳过 DeepSeek v4 兼容补丁。")
            return False
        current = self._peel_own_wrapper(provider_cls, current)
        if not callable(current):
            logger.warning("OvenHeart DeepSeek v4 兼容补丁无法重建 payload 入口，已跳过。")
            return False
        cls = type(self)
        original = current
        cls._provider_cls = provider_cls
        cls._original_finally_convert_payload = original

        def ovenheart_finally_convert_payload(provider_self: Any, payloads: dict) -> None:
            original(provider_self, payloads)
            active = cls._active
            if active is None:
                return
            try:
                active.fix_reasoning_payload(provider_self, payloads)
            except Exception as exc:  # noqa: BLE001
                logger.warning("OvenHeart DeepSeek v4 payload 补齐失败: %s", exc)

        ovenheart_finally_convert_payload._ovenheart_deepseek_lab = True  # type: ignore[attr-defined]
        cls._payload_wrapper = ovenheart_finally_convert_payload
        provider_cls._finally_convert_payload = ovenheart_finally_convert_payload
        cls._active = self
        self._installed = True
        logger.info("OvenHeart 已启用 DeepSeek v4 兼容修复。")
        return True

    def disconnect(self) -> None:
        cls = type(self)
        if cls._active is self:
            self._restore_payload()
        self._installed = False

    def sanitize(self, req: Any) -> None:
        """Remove empty, reasoning-only, and think-only assistant contexts."""
        if req is None:
            return
        contexts = getattr(req, "contexts", None)
        if not isinstance(contexts, list) or not contexts:
            return
        result = self.clean_contexts(contexts)
        if result.removed_count <= 0:
            return
        req.contexts = result.contexts
        logger.info(
            "OvenHeart 已清理异常上下文: session=%s, removed=%s, rules=%s",
            _session_id(req),
            result.removed_count,
            result.removed_by_rule,
        )

    def fix_reasoning_payload(self, provider: Any, payloads: dict) -> None:
        if not should_fix_deepseek_v4_payload(provider, payloads):
            return
        fixed_count = 0
        for message in payloads.get("messages", []):
            if not isinstance(message, dict):
                continue
            if message.get("role") != "assistant":
                continue
            if "reasoning_content" in message:
                continue
            message["reasoning_content"] = ""
            fixed_count += 1
        if fixed_count:
            logger.debug(
                "OvenHeart 已为 DeepSeek v4 assistant 历史补充 reasoning_content: count=%s",
                fixed_count,
            )

    def clean_contexts(self, contexts: list[Any]) -> CleanResult:
        cleaned: list[Any] = []
        removed_by_rule: dict[str, int] = {}
        for message in contexts:
            rule_name = self._first_matched_rule(message)
            if rule_name:
                removed_by_rule[rule_name] = removed_by_rule.get(rule_name, 0) + 1
                continue
            cleaned.append(message)
        return CleanResult(contexts=cleaned, removed_by_rule=removed_by_rule)

    def _first_matched_rule(self, message: Any) -> str | None:
        if not isinstance(message, dict):
            return None
        for rule in self.rules:
            if rule.should_remove(message):
                return rule.name
        return None

    def _peel_own_wrapper(self, provider_cls: type, current: Any) -> Any:
        cls = type(self)
        wrapper = cls._payload_wrapper
        if wrapper is None or not same_callable(current, wrapper):
            return current
        underlying = cls._original_finally_convert_payload
        cls._payload_wrapper = None
        cls._original_finally_convert_payload = None
        cls._provider_cls = None
        cls._active = None
        if callable(underlying) and not same_callable(underlying, wrapper):
            provider_cls._finally_convert_payload = underlying
            return underlying
        return None

    def _restore_payload(self) -> None:
        cls = type(self)
        provider_cls = cls._provider_cls
        original = cls._original_finally_convert_payload
        wrapper = cls._payload_wrapper
        if provider_cls is not None and original is not None and wrapper is not None:
            current = getattr(provider_cls, "_finally_convert_payload", None)
            if same_callable(current, wrapper):
                provider_cls._finally_convert_payload = original
        cls._provider_cls = None
        cls._original_finally_convert_payload = None
        cls._payload_wrapper = None
        cls._active = None


def should_fix_deepseek_v4_payload(provider: Any, payloads: dict) -> bool:
    model = str(payloads.get("model", "") or "").lower()
    if "deepseek-chat" in model or "deepseek-reasoner" in model:
        return False
    markers = ("deepseek-v4", "deepseek-v4-pro", "deepseek-v4-flash")
    if any(marker in model for marker in markers):
        return True
    return "api.deepseek.com" in get_provider_base_url_host(provider)


def get_provider_base_url_host(provider: Any) -> str:
    client = getattr(provider, "client", None)
    base_url = getattr(client, "base_url", None)
    host = getattr(base_url, "host", "")
    return str(host or "").lower()


def is_assistant(message: dict[str, Any]) -> bool:
    return message.get("role") == "assistant"


def has_effective_tool_calls(message: dict[str, Any]) -> bool:
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list):
        return False
    return any(isinstance(tool_call, dict) and bool(tool_call) for tool_call in tool_calls)


def is_empty_content(content: Any) -> bool:
    if content is None:
        return True
    if isinstance(content, str):
        return content.strip() == ""
    if isinstance(content, list):
        return len(content) == 0
    return False


def is_think_only_content(content: Any) -> bool:
    if not isinstance(content, list) or not content:
        return False
    saw_think = False
    for part in content:
        if not isinstance(part, dict):
            return False
        part_type = part.get("type")
        if part_type == "think":
            saw_think = True
            continue
        if part_type == "text" and not str(part.get("text", "")).strip():
            continue
        if part_type in ("input_text", "output_text") and not str(part.get("text", "")).strip():
            continue
        return False
    return saw_think


def same_callable(first: Any, second: Any) -> bool:
    if first is second:
        return True
    return getattr(first, "__func__", first) is getattr(second, "__func__", second) and getattr(
        first, "__self__", None
    ) is getattr(second, "__self__", None)


def _session_id(req: Any) -> str:
    conversation = getattr(req, "conversation", None)
    cid = getattr(conversation, "cid", None)
    if cid:
        return str(cid)
    session_id = getattr(req, "session_id", None)
    if session_id:
        return str(session_id)
    return "unknown"


def _load_openai_provider_cls() -> type | None:
    try:
        from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
    except Exception:  # noqa: BLE001
        return None
    return ProviderOpenAIOfficial
