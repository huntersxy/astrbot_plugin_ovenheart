"""Verify document-only warming stays separate from committed learning state."""

import asyncio
import importlib
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("astrbot.api")

from astrbot_plugin_ovenheart.core.settings import DEFAULTS  # noqa: E402
from tests.test_retrieval import TRAITS  # noqa: E402
from tests.test_runtime import api_call, event, sources  # noqa: E402

pytest_plugins = ["tests.test_runtime", "tests.test_retrieval"]


@pytest.fixture
def warming_plugin(plugin):
    """Configure a native document provider without touching an external service.

    Args:
        plugin: Isolated native plugin fixture.

    Returns:
        Plugin and a deterministic document embedding provider.
    """
    provider = SimpleNamespace(
        get_model=lambda: "fixture-vector",
        provider_config={"type": "fixture"},
        get_embeddings=AsyncMock(side_effect=lambda texts: [[1.0, 0.0] for _ in texts]),
        get_embedding=AsyncMock(return_value=[1.0, 0.0]),
    )
    plugin.context.get_provider_by_id = lambda key: provider
    plugin.settings.update(embedding_provider_id="embed", selection_mode="hybrid")
    plugin.debug = Mock()
    return plugin, provider


async def test_document_only_warming_avoids_query_and_reuses_persisted_vectors(
    retrieval,
):
    retriever, provider = retrieval
    settings = DEFAULTS | {"selection_mode": "hybrid", "embedding_provider_id": "embed"}
    assert (
        await retriever.semantic_scores(
            "scope-a", TRAITS, "", settings, index_only=True
        )
        == {}
    )
    provider.get_embedding.assert_not_awaited()
    assert not retriever.query_cache
    assert (await retriever.select("scope-a", TRAITS, "心里堵得慌", settings))[
        "mode"
    ] == "hybrid"
    provider.get_embeddings.assert_awaited_once()
    provider.get_embedding.assert_awaited_once()


async def test_warmup_failure_cannot_turn_a_committed_learning_run_into_failure(
    warming_plugin,
):
    plugin, provider = warming_plugin
    plugin.settings["activation_mode"] = "auto"
    provider.get_embeddings.side_effect = ValueError("api_key=SECRET_WARMUP")
    scope = await sources(plugin)
    result = await plugin.learn(scope)
    await asyncio.gather(
        *(task for task in list(plugin.tasks) if task is not plugin.worker)
    )
    state = await plugin.store.call("state", scope)
    assert result["status"] == "success" and state["runs"][0]["status"] == "success"
    assert state["scope"]["pending"] == 0 and len(state["traits"]) == 4
    assert all(t["status"] == "active" for t in state["traits"])
    provider.get_embeddings.assert_awaited_once()
    provider.get_embedding.assert_not_awaited()
    assert "SECRET_WARMUP" not in str(plugin.debug.call_args_list)
    assert not plugin.prewarming


async def test_manual_approval_prewarms_only_the_now_active_habit(warming_plugin):
    plugin, provider = warming_plugin
    scope = await sources(plugin)
    assert (await plugin.learn(scope))["status"] == "success"
    await asyncio.gather(
        *(task for task in list(plugin.tasks) if task is not plugin.worker)
    )
    provider.get_embeddings.assert_not_awaited()
    trait = (await plugin.store.call("state", scope))["traits"][0]
    result = await api_call(
        plugin.page_api.action,
        {
            "action": "trait",
            "scope": scope,
            "payload": {"id": trait["id"], "status": "active"},
        },
    )
    assert result["status"] == "ok"
    await asyncio.gather(
        *(task for task in list(plugin.tasks) if task is not plugin.worker)
    )
    assert provider.get_embeddings.await_args.args[0] == [
        f"{trait['situation']}\n{trait['content']}"
    ]
    provider.get_embedding.assert_not_awaited()
    assert not (await plugin.store.call("state", scope))["applications"]


async def test_busy_retrieval_and_overlapping_warm_jobs_are_skipped(
    warming_plugin, monkeypatch
):
    plugin, provider = warming_plugin
    scope = await sources(plugin)
    await plugin.learn(scope)
    await asyncio.gather(
        *(task for task in list(plugin.tasks) if task is not plugin.worker)
    )
    trait = (await plugin.store.call("state", scope))["traits"][0]
    await plugin.store.call(
        "mutate", scope, "trait", {"id": trait["id"], "status": "active"}
    )
    spy = AsyncMock(wraps=plugin.store.call)
    monkeypatch.setattr(plugin.store, "call", spy)
    async with plugin.retriever.lock:
        await plugin.prewarm_embeddings(scope)
    spy.assert_not_awaited()
    await asyncio.gather(
        plugin.prewarm_embeddings(scope), plugin.prewarm_embeddings(scope)
    )
    provider.get_embeddings.assert_awaited_once()
    assert not plugin.prewarming


async def test_idle_poll_fills_later_batches_without_queries_or_learning(
    warming_plugin, monkeypatch
):
    plugin, provider = warming_plugin
    plugin.worker.cancel()
    await asyncio.gather(plugin.worker, return_exceptions=True)
    scope = await sources(plugin)
    with sqlite3.connect(plugin.store.path) as db:
        db.executemany(
            "INSERT INTO traits(scope,kind,situation,content,confidence,evidence,status,fingerprint,created,updated) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    scope,
                    "expression",
                    "日常交流",
                    f"已批准表达 {i}",
                    0.95,
                    "[]",
                    "active",
                    f"idle-{i}",
                    1,
                    1,
                )
                for i in range(130)
            ],
        )
    plugin.settings["learning_enabled"] = False
    module = importlib.import_module(type(plugin).__module__)
    ticks = [0]

    async def tick(seconds):
        """Advance the scheduler's clock without waiting in the test.

        Args:
            seconds: Existing scheduler sleep interval.

        Raises:
            asyncio.CancelledError: After four idle scheduling opportunities.
        """
        assert seconds == 15
        ticks[0] += 1
        if ticks[0] > 13:
            raise asyncio.CancelledError

    monkeypatch.setattr(
        module, "asyncio", SimpleNamespace(**(vars(asyncio) | {"sleep": tick}))
    )
    monkeypatch.setattr(
        module,
        "time",
        SimpleNamespace(time=time.time, monotonic=lambda: 1000 + ticks[0] * 15),
    )
    with pytest.raises(asyncio.CancelledError):
        await plugin.poll()
    assert [len(call.args[0]) for call in provider.get_embeddings.await_args_list] == [
        64,
        64,
        2,
    ]
    provider.get_embedding.assert_not_awaited()
    plugin.context.llm_generate.assert_not_awaited()
    assert not plugin.retriever.query_cache
    assert len(await plugin.store.call("batch", scope, 30)) == 2
    assert (await plugin.store.call("state", scope))["applications"] == []
    with sqlite3.connect(plugin.store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 130


async def test_idle_cursor_rotates_permitted_scopes_and_skips_busy_selection(
    warming_plugin, monkeypatch
):
    plugin, _ = warming_plugin
    plugin.worker.cancel()
    await asyncio.gather(plugin.worker, return_exceptions=True)
    for session in ("one", "two", "excluded"):
        await plugin.capture(event(session, session=session))
    state = await plugin.store.call("state")
    candidates = sorted(
        s["id"] for s in state["scopes"] if not s["umo"].endswith("excluded")
    )
    plugin.settings.update(
        learning_enabled=False,
        allowed_sessions=[s["umo"] for s in state["scopes"] if s["id"] in candidates],
    )
    plugin.prewarm_embeddings = AsyncMock()
    module = importlib.import_module(type(plugin).__module__)
    ticks = [0]

    async def tick(seconds):
        """Simulate one busy tick followed by ordinary idle visits.

        Args:
            seconds: Existing polling interval.

        Raises:
            asyncio.CancelledError: After three accepted scheduling visits.
        """
        ticks[0] += 1
        plugin.retriever.selecting = 2 if ticks[0] == 1 else 0
        if ticks[0] > 10:
            raise asyncio.CancelledError

    monkeypatch.setattr(
        module, "asyncio", SimpleNamespace(**(vars(asyncio) | {"sleep": tick}))
    )
    monkeypatch.setattr(
        module,
        "time",
        SimpleNamespace(time=time.time, monotonic=lambda: 1000 + ticks[0] * 15),
    )
    with pytest.raises(asyncio.CancelledError):
        await plugin.poll()
    assert [call.args[0] for call in plugin.prewarm_embeddings.await_args_list] == [
        candidates[0],
        candidates[1],
        candidates[0],
    ]
