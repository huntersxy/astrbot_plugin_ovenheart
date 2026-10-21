"""Validate evidence boundaries and application of approved habits."""

import json

import pytest

from astrbot_plugin_persona.learning import build_guidance, parse_candidates
from astrbot_plugin_persona.settings import (
    DEFAULTS,
    PROFILE_DEFAULTS,
    validate_settings,
)


def candidates(**patch):
    """Construct a valid model candidate with optional invalid fields.

    Args:
        **patch: Fields to replace for a test case.

    Returns:
        A serialized model response.
    """
    return json.dumps(
        {
            "traits": [
                {
                    "kind": "expression",
                    "situation": "被调侃时",
                    "content": "用简短反问接住玩笑",
                    "confidence": 0.95,
                    "evidence_ids": [1, 2],
                    **patch,
                }
            ]
        }
    )


MESSAGES = [
    {"id": i, "sender": "alice", "text": f"好家伙，又被你发现了{i}", "created": 1.0}
    for i in (1, 2)
]


def test_evidence_is_resolved_from_real_batch():
    result = parse_candidates("```json\n" + candidates() + "\n```", MESSAGES, DEFAULTS)
    assert result[0]["evidence"][0]["text"] == MESSAGES[0]["text"]
    assert result[0]["kind"] == "expression"


@pytest.mark.parametrize(
    "patch",
    [
        {"evidence_ids": [1, 999]},
        {"evidence_ids": [True, 2]},
        {"evidence_ids": "1,2"},
        {"confidence": float("nan")},
        {"confidence": True},
        {"confidence": 2},
        {"kind": "unknown"},
        {"content": ""},
        {"situation": "x" * 161},
    ],
)
def test_invalid_output_is_rejected(patch):
    with pytest.raises(ValueError):
        parse_candidates(candidates(**patch), MESSAGES, DEFAULTS)


def test_repeated_evidence_and_disabled_category_are_not_saved():
    assert parse_candidates(candidates(evidence_ids=[1, 1]), MESSAGES, DEFAULTS) == []
    assert (
        parse_candidates(candidates(), MESSAGES, DEFAULTS | {"learn_expression": False})
        == []
    )
    assert parse_candidates('{"traits":[]}', MESSAGES, DEFAULTS) == []


def test_only_active_traits_are_applied_within_budget():
    profile = PROFILE_DEFAULTS | {"identity": "我是一个耐心的朋友"}
    active = {
        "status": "active",
        "kind": "behavior",
        "situation": "难过",
        "content": "先倾听再安慰",
        "confidence": 0.9,
        "updated": 1,
    }
    pending = active | {"status": "pending", "content": "不能出现的未审核内容"}
    jargon = active | {
        "kind": "jargon",
        "situation": "跑图",
        "content": "无关的游戏黑话",
    }
    result = build_guidance(
        profile,
        [pending, active, jargon],
        DEFAULTS | {"prompt_budget": 500},
        "今天有点难过",
    )
    assert (
        "先倾听再安慰" in result and "不能出现" not in result and "黑话" not in result
    )
    assert len(result) <= 500
    assert (
        build_guidance(profile | {"apply_enabled": False}, [active], DEFAULTS, "难过")
        == ""
    )
    assert (
        build_guidance(profile, [active], DEFAULTS | {"apply_enabled": False}, "难过")
        == ""
    )
    assert build_guidance(PROFILE_DEFAULTS, [], DEFAULTS, "你好") == ""


@pytest.mark.parametrize(
    "patch",
    [
        {"batch_size": True},
        {"min_evidence": 0},
        {"activation_mode": "invalid"},
        {"enabled": "false"},
        {"target_senders": "123"},
        {"provider_id": 99},
        {"unknown": True},
    ],
)
def test_configuration_rejects_invalid_types_and_fields(patch):
    with pytest.raises(ValueError):
        validate_settings(patch)
