"""Verify grounded expression samples from extraction through actual guidance."""

import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.api.provider import ProviderRequest

from astrbot_plugin_ovenheart.core.learning import habit_text, parse_candidates
from astrbot_plugin_ovenheart.core.settings import DEFAULTS
from tests.test_learning import MESSAGES, candidates
from tests.test_runtime import event, sources

pytest_plugins = ["tests.test_runtime", "tests.test_retrieval"]


@pytest.mark.parametrize("kind", ["personality", "expression", "behavior", "jargon"])
def test_optional_null_examples_is_absence_not_new_evidence(kind):
    result = parse_candidates(
        candidates(kind=kind, examples=None, content="好家伙：表示惊讶"),
        MESSAGES,
        DEFAULTS,
    )[0]
    assert result["examples"] == [] and len(result["evidence"]) == 2


def test_examples_must_be_literal_spans_of_this_candidates_own_evidence():
    valid = [{"evidence_id": 1, "text": "好家伙"}]
    result = parse_candidates(candidates(examples=valid * 2), MESSAGES, DEFAULTS)[0]
    assert result["examples"] == [
        {"text": "好家伙", "id": 1, "sender": "alice", "created": 1.0}
    ]
    assert len(result["evidence"]) == 2
    for invalid in (
        [{"evidence_id": 99, "text": "引用原文"}],
        [{"evidence_id": True, "text": "好家伙"}],
        [{"evidence_id": 1, "text": "模型改写的说法"}],
        [{"evidence_id": 1, "text": "x" * 61}],
        valid * 4,
        "好家伙",
    ):
        with pytest.raises(ValueError):
            parse_candidates(candidates(examples=invalid), MESSAGES, DEFAULTS)
    behavior = parse_candidates(
        candidates(kind="behavior", examples=valid), MESSAGES, DEFAULTS
    )
    assert behavior[0]["examples"] == []
    long = [MESSAGES[0] | {"text": "x" * 301 + "尾部片段"}, MESSAGES[1]]
    with pytest.raises(ValueError):
        parse_candidates(
            candidates(examples=[{"evidence_id": 1, "text": "尾部片段"}]),
            long,
            DEFAULTS,
        )


@pytest.mark.parametrize("approved", [True, False])
async def test_native_learning_reviews_examples_and_preserves_activation_policy(
    plugin, approved
):
    plugin.settings.update(
        learn_personality=False,
        learn_jargon=False,
        learn_behavior=False,
        learning_review_enabled=True,
        activation_mode="auto",
        guidance_delivery="cached",
        selection_mode="lexical",
    )
    scope = await sources(plugin)
    reviewed = []

    async def completion(**kwargs):
        """Supply literal extraction and a controlled independent review verdict.

        Args:
            **kwargs: Native model arguments.

        Returns:
            A valid learning or review completion.
        """
        payload = json.loads(kwargs["prompt"])
        if "candidates" in payload:
            reviewed.extend(payload["candidates"])
            body = {
                "approved_ids": [1] if approved else [],
                "flagged": []
                if approved
                else [{"candidate_id": 1, "reason": "该片段不适合当前使用条件"}],
            }
        else:
            ids = [m["id"] for m in payload["observations"]]
            body = json.loads(
                candidates(
                    evidence_ids=ids,
                    examples=[{"evidence_id": ids[-1], "text": "好家伙"}],
                )
            )
        return SimpleNamespace(completion_text=json.dumps(body))

    plugin.context.llm_generate.side_effect = completion
    assert (await plugin.learn(scope))["status"] == "success"
    state = await plugin.store.call("state", scope)
    trait = state["traits"][0]
    assert trait["examples"][0]["source_id"] == "2"
    assert reviewed[0]["examples"] == [
        {"evidence_id": trait["examples"][0]["id"], "text": "好家伙"}
    ]
    assert (
        state["runs"][0]["review"]["candidates"][0]["examples"]
        == reviewed[0]["examples"]
    )
    assert trait["status"] == ("active" if approved else "pending")
    assert plugin.context.llm_generate.await_count == 2
    request = ProviderRequest(prompt="你又猜中了", system_prompt="稳定前缀")
    await plugin.apply_persona(event(), request)
    assert request.system_prompt == "稳定前缀"
    dynamic = "\n".join(p.text for p in request.extra_user_content_parts)
    assert ("好家伙" in dynamic) is approved
    if approved:
        assert "原话片段（仅作风格参考）" in dynamic


async def test_examples_invalidate_vector_text_and_survive_approval_but_not_rewrite(
    retrieval,
):
    retriever, provider = retrieval
    provider.get_embeddings.side_effect = lambda texts: [[1.0, 0.0] for _ in texts]
    state = await retriever.store.call("state", "scope-a")
    trait = state["traits"][0]
    example = {"text": "原话片段", "id": 1, "sender": "alice", "created": 1}
    trait["examples"] = [example]
    with sqlite3.connect(retriever.store.path) as db:
        db.execute(
            "UPDATE traits SET examples=? WHERE id=?",
            (json.dumps([example]), trait["id"]),
        )
    cfg = DEFAULTS | {"embedding_provider_id": "embed"}
    await retriever.semantic_scores("scope-a", [trait], "", cfg, index_only=True)
    await retriever.semantic_scores("scope-a", [trait], "", cfg, index_only=True)
    assert provider.get_embeddings.await_count == 1
    assert "原话片段" in habit_text(trait)
    retriever.context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(completion_text='{"selected_ids":[]}')
    )
    await retriever.contextual_ids([trait], "轻松接话", cfg, "chat", "原有人格")
    offered = json.loads(retriever.context.llm_generate.await_args.kwargs["prompt"])[
        "candidates"
    ][0]
    assert offered["examples"] == ["原话片段"]
    assert "alice" not in json.dumps(offered)
    await retriever.store.call(
        "mutate",
        "scope-a",
        "trait",
        {
            "id": trait["id"],
            "status": "active",
            "content": trait["content"],
            "situation": trait["situation"],
        },
    )
    approved = (await retriever.store.call("guidance", "scope-a"))["traits"][0]
    assert approved["examples"] == [example]
    await retriever.store.call(
        "mutate", "scope-a", "trait", {"id": trait["id"], "content": "人工改写"}
    )
    rewritten = next(
        t
        for t in (await retriever.store.call("guidance", "scope-a"))["traits"]
        if t["id"] == trait["id"]
    )
    assert not rewritten["examples"]
    await retriever.semantic_scores("scope-a", [rewritten], "", cfg, index_only=True)
    assert provider.get_embeddings.await_count == 2
    revision = (await retriever.store.call("state", "scope-a"))["revisions"][0]
    await retriever.store.call("mutate", "scope-a", "restore", {"id": revision["id"]})
    restored = next(
        t
        for t in (await retriever.store.call("guidance", "scope-a"))["traits"]
        if t["id"] == trait["id"]
    )
    assert restored["examples"] == [example]
