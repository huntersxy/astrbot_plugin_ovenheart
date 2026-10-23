"""Prefer the actual full local term over definitions of nested fragments."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot_plugin_ovenheart.settings import DEFAULTS
from tests.test_retrieval import TRAITS

pytest_plugins = ["tests.test_retrieval"]


async def test_full_term_survives_a_stronger_short_fragment_and_selector_choice(
    retrieval,
):
    retriever, _ = retrieval
    short = TRAITS[0] | {
        "id": 3,
        "kind": "jargon",
        "content": "典：这是什么意思的意思",
        "rating": 1,
        "evidence_count": 1000,
    }
    full = TRAITS[0] | {
        "id": 4,
        "kind": "jargon",
        "content": "典中典：归档案例的再次引用",
        "confidence": 0.6,
    }
    retriever.context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(completion_text='{"selected_ids":[3]}')
    )
    for semantic in (False, True):
        result = await retriever.select(
            "scope-a",
            [short, full],
            "典中典是什么意思？",
            DEFAULTS | {"max_traits": 1},
            semantic=semantic,
            literal_query="典中典是什么意思？",
            provider_id="chat",
        )
        assert [t["id"] for t in result["traits"]] == [4]
        assert result["shadowed_jargon_ids"] == [3]
    retriever.context.llm_generate.assert_awaited_once()
    independent = await retriever.select(
        "scope-a",
        [short, full],
        "典中典和典分别是什么意思？",
        DEFAULTS,
        semantic=False,
        literal_query="典中典和典分别是什么意思？",
    )
    assert {t["id"] for t in independent["traits"]} == {3, 4}
    assert "shadowed_jargon_ids" not in independent
    disabled = await retriever.select(
        "scope-a",
        [short, full | {"status": "disabled"}],
        "典中典",
        DEFAULTS,
        semantic=False,
        literal_query="典中典",
    )
    assert [t["id"] for t in disabled["traits"]] == [3]


async def test_width_ascii_boundaries_equal_terms_and_partial_overlap_are_preserved(
    retrieval,
):
    retriever, _ = retrieval
    traits = [
        TRAITS[0] | {"id": i, "kind": "jargon", "content": content}
        for i, content in (
            (1, "『ＡＩ』：人工智能"),
            (2, "AI助手：本群自动答题工具"),
            (3, "「AI助手」：工具在答题场景中的称呼"),
            (4, "A典：甲类归档"),
            (5, "典B：乙类归档"),
            (6, "LUX：本群报告"),
            (7, "deluxe：豪华版"),
        )
    ]
    result = await retriever.select(
        "scope-a",
        traits,
        "ＡＩ助手 deluxe A典B",
        DEFAULTS,
        semantic=False,
        literal_query="ＡＩ助手 deluxe A典B",
    )
    assert result["shadowed_jargon_ids"] == [1]
    assert {t["id"] for t in result["traits"]} == {2, 3, 4, 5, 7}


async def test_actual_body_precedes_stronger_quote_matches_without_losing_quote_only_recall(
    retrieval,
):
    retriever, _ = retrieval
    body = TRAITS[0] | {
        "id": 3,
        "kind": "jargon",
        "content": "LUX：本群改期记号",
        "confidence": 0.6,
    }
    quote = TRAITS[0] | {
        "id": 4,
        "kind": "jargon",
        "content": "青栈：这是什么意思的意思",
        "rating": 1,
        "evidence_count": 1000,
    }
    query = "LUX 是什么意思？\n青栈这是什么意思的意思？"
    retriever.context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(completion_text='{"selected_ids":[4]}')
    )
    result = await retriever.select(
        "scope-a",
        [body, quote],
        query,
        DEFAULTS | {"max_traits": 1},
        literal_query=query,
        literal_focus="LUX 是什么意思？",
        provider_id="chat",
    )
    assert [t["id"] for t in result["traits"]] == result["literal_focus_ids"] == [3]
    assert "正文所提" in result["message"]
    quote_only = await retriever.select(
        "scope-a",
        [quote],
        "这个是什么意思？\n青栈",
        DEFAULTS,
        semantic=False,
        literal_query="这个是什么意思？\n青栈",
        literal_focus="这个是什么意思？",
    )
    assert [t["id"] for t in quote_only["traits"]] == [4]
    assert quote_only["literal_focus_ids"] == []
