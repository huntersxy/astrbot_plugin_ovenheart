"""Compose OvenHeart's experimental context patches.

``sync`` reads a settings dict. Recognized keys default to off:

- ``image_history``: replace historical base64 images with a placeholder
- ``tool_history``: collapse finished tool results
- ``long_reply``: keep the captured assistant text when history is shorter
- ``deepseek_v4``: drop empty / reasoning-only / think-only assistant turns
  and fill DeepSeek v4 ``reasoning_content``
- ``forward_retry``: split a failed aiocqhttp forward send and retry
- ``forward_hard_limit``: per-node hard character limit, default 1200
"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger

from .lab_deepseek import DeepSeekLabBridge
from .lab_forward import FORWARD_NODE_HARD_LIMIT, ForwardLabBridge
from .lab_history import HistoryLabBridge
from .lab_long_reply import LongReplyLabBridge


class ContextLabBridge:
    """Install, sanitize, and tear down the experimental context labs together."""

    def __init__(self, plugin: Any):
        self.plugin = plugin
        self.history = HistoryLabBridge(plugin)
        self.deepseek = DeepSeekLabBridge(plugin)
        self.long_reply = LongReplyLabBridge(plugin, self.history)
        self.forward = ForwardLabBridge(plugin)

    def sync(self, settings: dict | None = None) -> None:
        values = settings if isinstance(settings, dict) else {}
        long_reply = _flag(values, "long_reply")
        image = _flag(values, "image_history")
        tool = _flag(values, "tool_history")
        self.long_reply.sync(long_reply)
        self.history.sync(image=image, tool=tool)
        self.deepseek.sync(_flag(values, "deepseek_v4"))
        hard_limit = values.get("forward_hard_limit", FORWARD_NODE_HARD_LIMIT)
        self.forward.sync(_flag(values, "forward_retry"), hard_limit=hard_limit)

    def sanitize_request(self, event: Any, req: Any) -> None:
        """Clean the outgoing request. ``event`` is accepted for call-site context."""
        del event
        if req is None:
            return
        try:
            self.history.sanitize_request(req)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OvenHeart 请求历史清理失败: %s", exc)
        try:
            self.deepseek.sanitize(req)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OvenHeart DeepSeek 上下文清理失败: %s", exc)

    def disconnect(self) -> None:
        self.forward.disconnect()
        self.long_reply.disconnect()
        self.history.disconnect()
        self.deepseek.disconnect()


def _flag(settings: dict, key: str) -> bool:
    value = settings.get(key, False)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
