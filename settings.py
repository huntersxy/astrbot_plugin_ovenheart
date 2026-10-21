"""Runtime settings shared by the plugin and its Panel view."""

from __future__ import annotations

DEFAULTS = {
    "enabled": True,
    "learning_enabled": True,
    "apply_enabled": True,
    "activation_mode": "review",
    "auto_confidence": 0.9,
    "min_evidence": 2,
    "batch_size": 30,
    "min_messages": 12,
    "interval_seconds": 600,
    "provider_id": "",
    "memory_enabled": True,
    "memory_top_k": 3,
    "target_senders": [],
    "allowed_sessions": [],
    "learn_personality": True,
    "learn_expression": True,
    "learn_jargon": True,
    "learn_behavior": True,
    "prompt_budget": 3000,
    "max_traits": 8,
    "retention_days": 14,
    "max_messages_per_scope": 300,
}
KINDS = ("personality", "expression", "jargon", "behavior")
PROFILE_DEFAULTS = {
    "identity": "",
    "reply_style": "",
    "behavior": "",
    "learning_enabled": True,
    "apply_enabled": True,
}


def validate_settings(values: dict) -> dict:
    """Validate a settings patch without accepting unknown configuration keys.

    Args:
        values: Settings supplied by the operator or host configuration.

    Returns:
        A validated partial settings dictionary.

    Raises:
        ValueError: If a key or value is invalid.
    """
    if not isinstance(values, dict) or set(values) - DEFAULTS.keys():
        raise ValueError("配置包含未知字段，请刷新页面后重试。")
    bounds = {
        "auto_confidence": (0.5, 1.0),
        "min_evidence": (1, 10),
        "batch_size": (4, 100),
        "min_messages": (4, 100),
        "interval_seconds": (30, 86400),
        "memory_top_k": (1, 8),
        "prompt_budget": (500, 8000),
        "max_traits": (1, 24),
        "retention_days": (1, 90),
        "max_messages_per_scope": (100, 1000),
    }
    for key, value in values.items():
        default = DEFAULTS[key]
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise ValueError(f"{key} 必须为开关值。")
        elif key in bounds:
            low, high = bounds[key]
            numeric_type = (int, float) if isinstance(default, float) else (int,)
            if (
                isinstance(value, bool)
                or not isinstance(value, numeric_type)
                or not low <= value <= high
            ):
                raise ValueError(f"{key} 必须在 {low} 到 {high} 之间。")
        elif isinstance(default, list):
            if (
                not isinstance(value, list)
                or len(value) > 500
                or any(
                    not isinstance(v, str) or not v.strip() or len(v) > 256
                    for v in value
                )
            ):
                raise ValueError(f"{key} 必须为有效标识列表。")
        elif key == "activation_mode":
            if value not in ("review", "auto"):
                raise ValueError("生效方式必须为审核或自动。")
        elif not isinstance(value, str) or len(value) > 256:
            raise ValueError(f"{key} 必须为长度不超过 256 的字符串。")
    return values.copy()
