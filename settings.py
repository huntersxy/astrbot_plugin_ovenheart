"""Runtime settings shared by the plugin and its Panel view."""

from __future__ import annotations

DEFAULTS = {
    "enabled": True,
    "learning_enabled": True,
    "learning_review_enabled": True,
    "apply_enabled": True,
    "guidance_delivery": "system",
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
    "embedding_provider_id": "",
    "rerank_provider_id": "",
    "retrieval_timeout": 6,
    "selection_mode": "contextual",
    "selector_timeout": 12,
    "semantic_threshold": 0.25,
    "habit_half_life": 90,
    "participation_mode": "off",
    "participation_min_messages": 3,
    "participation_debounce": 2,
    "participation_cooldown": 90,
    "participation_hourly_limit": 12,
    "planner_provider_id": "",
    "planner_timeout": 20,
    "planner_reply_threshold": 0.7,
    "planner_max_wait": 30,
    "autonomous_tools": [],
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
        "retrieval_timeout": (1, 15),
        "selector_timeout": (3, 30),
        "semantic_threshold": (0, 1),
        "habit_half_life": (7, 730),
        "participation_min_messages": (1, 30),
        "participation_debounce": (0, 10),
        "participation_cooldown": (5, 3600),
        "participation_hourly_limit": (1, 120),
        "planner_timeout": (3, 90),
        "planner_reply_threshold": (0.5, 1),
        "planner_max_wait": (1, 120),
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
        elif key == "selection_mode":
            if value not in ("lexical", "hybrid", "contextual"):
                raise ValueError("检索方式必须为 lexical、hybrid 或 contextual。")
        elif key == "guidance_delivery":
            if value not in ("system", "dual"):
                raise ValueError("提示投递方式必须为 system 或 dual。")
        elif key == "participation_mode":
            if value not in ("off", "observe", "auto"):
                raise ValueError("参与方式必须为 off、observe 或 auto。")
        elif not isinstance(value, str) or len(value) > 256:
            raise ValueError(f"{key} 必须为长度不超过 256 的字符串。")
    return values.copy()
