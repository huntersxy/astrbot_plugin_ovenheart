"""Smoke tests for experimental context lab sanitizers."""

from __future__ import annotations

from types import SimpleNamespace

from core.chat.lab_deepseek import DeepSeekLabBridge
from core.chat.lab_history import (
    IMAGE_HISTORY_PLACEHOLDER,
    TOOL_HISTORY_PLACEHOLDER,
    sanitize_image_messages,
    sanitize_tool_messages,
)


def test_sanitize_image_messages_replaces_base64():
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,aaaa"},
                },
                {"type": "text", "text": "see"},
            ],
        }
    ]
    sanitized, changed = sanitize_image_messages(messages)
    assert changed
    assert sanitized[0]["content"][0] == {
        "type": "text",
        "text": IMAGE_HISTORY_PLACEHOLDER,
    }
    assert sanitized[0]["content"][1]["text"] == "see"


def test_sanitize_tool_messages_collapses_completed_group():
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_1", "type": "function", "function": {}}],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "huge payload"},
        {"role": "assistant", "content": "done"},
    ]
    sanitized, changed = sanitize_tool_messages(messages)
    assert changed
    assert sanitized[1]["content"] == TOOL_HISTORY_PLACEHOLDER
    assert sanitized[2]["content"] == "done"


def test_deepseek_removes_empty_and_reasoning_only():
    bridge = DeepSeekLabBridge(SimpleNamespace())
    cleaned = bridge.clean_contexts(
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": ""},
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "think",
            },
            {"role": "assistant", "content": "ok"},
        ]
    )
    assert cleaned.removed_count == 2
    assert cleaned.contexts == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"},
    ]
