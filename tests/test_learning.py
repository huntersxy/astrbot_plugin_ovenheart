"""Validate evidence boundaries and application of approved habits."""

import json

import pytest

from astrbot_plugin_ovenheart.learning import (
    LearningFormatError,
    build_guidance,
    jargon_term,
    mentions_jargon,
    parse_candidates,
)
from astrbot_plugin_ovenheart.settings import (
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


def test_refinement_requires_a_boolean_flag_and_an_explicit_known_target():
    known = [{"id": 8, "kind": "expression", "content": "旧习惯"}]
    assert parse_candidates(
        candidates(refine=True, merge_id=8), MESSAGES, DEFAULTS, known
    )[0]["refine"]
    assert not parse_candidates(candidates(refine=True), MESSAGES, DEFAULTS)[0][
        "refine"
    ]
    assert not parse_candidates(candidates(merge_id=8), MESSAGES, DEFAULTS, known)[0][
        "refine"
    ]
    with pytest.raises(LearningFormatError, match="布尔"):
        parse_candidates(
            candidates(refine="true", merge_id=8), MESSAGES, DEFAULTS, known
        )


@pytest.mark.parametrize(
    "wrapper",
    [
        "分析结果如下：\n```json\n%s\n```\n以上来自原始消息。",
        '<think>先分析证据，示例 {"wrong": true}</think>\n%s',
        "结果：%s\n分析完毕。",
    ],
)
def test_model_explanations_and_closed_reasoning_are_accepted(wrapper):
    result = parse_candidates(
        wrapper % candidates(content="用一句“{好家伙}”接话"), MESSAGES, DEFAULTS
    )
    assert result[0]["content"] == "用一句“{好家伙}”接话"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "<think>还没有生成结果",
        '{"traits": [',
        '{"profile_updates": [{"kind":"expression", "trait":"简短"}]}',
        '{"traits":[{"kind":"expression","trait":"简短"}]}',
        '{"outer": {"traits": []}',
        '{"traits": []}\n{"traits": []}',
        '```json\n{"traits":[]}\n```\n```json\n{"traits":[]}\n```',
    ],
)
def test_schema_drift_and_truncation_are_repairable_without_guessing(text):
    from astrbot_plugin_ovenheart.learning import LearningFormatError

    with pytest.raises(LearningFormatError):
        parse_candidates(text, MESSAGES, DEFAULTS)


def test_consolidation_targets_are_scoped_and_category_checked():
    known = [{"id": 8, "kind": "expression", "content": "简短", "situation": "日常"}]
    result = parse_candidates(candidates(merge_id=8), MESSAGES, DEFAULTS, known)
    assert result[0]["merge_id"] == 8
    # An unusable consolidation target drops that candidate only. Failing the
    # batch here would retry the same preserved sources on every cycle.
    for value in (True, 99, "8"):
        assert (
            parse_candidates(candidates(merge_id=value), MESSAGES, DEFAULTS, known)
            == []
        )
    assert (
        parse_candidates(
            candidates(kind="behavior", merge_id=8), MESSAGES, DEFAULTS, known
        )
        == []
    )


def test_different_jargon_words_cannot_be_merged():
    assert (
        parse_candidates(
            candidates(kind="jargon", content="绝绝子：称赞", merge_id=1),
            MESSAGES,
            DEFAULTS,
            [{"id": 1, "kind": "jargon", "content": "好家伙：惊讶"}],
        )
        == []
    )


@pytest.mark.parametrize(
    "word", ["好家伙", "“好家伙”", "「好家伙」", "`好家伙`", "‘“好家伙”’"]
)
def test_jargon_quotes_do_not_break_evidence_or_explicit_same_word_merge(word):
    result = parse_candidates(
        candidates(kind="jargon", content=f"{word}：轻松的惊讶", merge_id=1),
        MESSAGES,
        DEFAULTS,
        [{"id": 1, "kind": "jargon", "content": "『好家伙』：表示惊讶"}],
    )
    assert result[0]["merge_id"] == 1 and result[0]["content"].startswith(word)
    assert jargon_term(result[0]["content"]) == "好家伙"


@pytest.mark.parametrize("content", ["不存在的词：惊讶", "词语：惊讶", "AI：惊讶"])
def test_real_evidence_ids_do_not_authorize_an_absent_jargon_name(content):
    with pytest.raises(ValueError, match="词汇名称未出现"):
        parse_candidates(candidates(kind="jargon", content=content), MESSAGES, DEFAULTS)
    unrelated = MESSAGES + [MESSAGES[0] | {"id": 3, "text": content}]
    with pytest.raises(ValueError, match="词汇名称未出现"):
        parse_candidates(
            candidates(kind="jargon", content=content), unrelated, DEFAULTS
        )


@pytest.mark.parametrize("content", ["好家伙", "：惊讶", "好家伙：", "“”：惊讶"])
def test_jargon_format_is_repairable_but_ungrounded_content_is_not(content):
    with pytest.raises(LearningFormatError, match="词名"):
        parse_candidates(candidates(kind="jargon", content=content), MESSAGES, DEFAULTS)


@pytest.mark.parametrize(
    ("term", "text", "expected"),
    [
        ("AI：本群的安排", "AI今晚照旧", True),
        ("「ＡＩ」：本群的安排", "ai今晚照旧", True),
        ("ai：本群的安排", "ＡＩ今晚照旧", True),
        ("AI：本群的安排", "rain 和 brain 都不是那个词", False),
        ("AI：本群的安排", "wait_for_AI_result", False),
        ("C++：本群的约定", "今天又 C++ 了", True),
        ("C++：本群的约定", "abc++", False),
        ("「典」：表示典型", "典？", True),
    ],
)
def test_literal_jargon_matching_respects_ascii_word_boundaries(term, text, expected):
    assert mentions_jargon(term, text) is expected


def test_a_term_question_and_an_anaphoric_definition_can_support_the_same_word():
    messages = [
        MESSAGES[0] | {"text": "青栈是什么意思？"},
        MESSAGES[1] | {"text": "这个词表示改期，不是取消。"},
    ]
    assert parse_candidates(
        candidates(kind="jargon", content="青栈：计划改期"), messages, DEFAULTS
    )


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
