"""Summarize native planner action logs without changing tool results."""

import json
from contextlib import contextmanager
from contextvars import ContextVar

_scope = ContextVar("ovenheart_planner_result_logs", default=None)
_prefixes = tuple(
    f"Tool `{name}` Result: " for name in ("reply", "wait", "stop", "send_emoji")
)


@contextmanager
def compact_planner_logs():
    from astrbot.core.agent.runners import tool_loop_agent_runner

    scope = object()

    def summarize(record):
        if (
            _scope.get() is not scope
            or record.module != "tool_loop_agent_runner"
            or record.levelname != "INFO"
        ):
            return True
        message = record.getMessage()
        prefix = next((p for p in _prefixes if message.startswith(p)), None)
        if prefix is not None:
            record.msg = prefix + result_summary(message[len(prefix) :])
            record.args = ()
        return True

    # The host logger owns this output. A per-invocation filter changes only
    # records emitted inside our planner, including concurrent native runners.
    tool_loop_agent_runner.logger.addFilter(summarize)
    token = _scope.set(scope)
    try:
        yield
    finally:
        _scope.reset(token)
        tool_loop_agent_runner.logger.removeFilter(summarize)


def result_summary(payload):
    try:
        result = json.loads(payload)
    except json.JSONDecodeError:
        # The host may append execution-budget guidance to a tool response.
        return json.dumps(
            {"preview": payload[:240], "result_chars": len(payload)},
            ensure_ascii=False,
        )
    if not isinstance(result, dict):
        return json.dumps({"result_chars": len(payload)}, ensure_ascii=False)
    summary = {
        key: value[:120] if isinstance(value, str) else value
        for key, value in result.items()
        if key
        in {
            "send_hook",
            "target_sequence",
            "partial_text",
            "delivery_status",
            "delivery_failed",
            "delivery_uncertain",
            "wait_result",
            "error",
            "reason",
            "cancelled",
            "media_only",
        }
    }
    if "reply_text" in result:
        summary["reply_chars"] = len(result["reply_text"])
    if "recent_messages" in result:
        summary["recent_messages_count"] = len(result["recent_messages"])
    if isinstance(result.get("media"), dict):
        summary["media"] = {
            key: value[:120] if isinstance(value, str) else value
            for key, value in result["media"].items()
            if key in {"sent", "index", "error", "reason"}
        }
    return json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
