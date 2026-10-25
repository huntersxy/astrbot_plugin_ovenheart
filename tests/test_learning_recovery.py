"""Exercise durable retries and exact-input drafts across native plugin reloads."""

import asyncio
import json
import sqlite3
import time
from contextlib import closing
from types import SimpleNamespace

import pytest

from astrbot_plugin_ovenheart import main
from astrbot_plugin_ovenheart.main import PersonaPlugin
from tests.test_runtime import completion, event, sources

pytest_plugins = ["tests.test_runtime"]


async def test_review_timeout_resumes_only_review_after_reload_without_consuming_sources(
    plugin, monkeypatch
):
    scope = await sources(plugin)
    plugin.settings["learning_review_enabled"] = True
    provider = SimpleNamespace(
        provider_config={"model": "controlled", "api_key": "PRIVATE_KEY_SENTINEL"},
        get_model=lambda: "controlled",
    )
    plugin.context.get_provider_by_id = lambda identifier: provider
    monkeypatch.setattr(main, "LEARNING_TIMEOUT_SECONDS", 2)
    calls = []

    async def model(**kwargs):
        payload = json.loads(kwargs["prompt"])
        calls.append("review" if "candidates" in payload else "extraction")
        if "candidates" in payload:
            await asyncio.sleep(1.3)
            return SimpleNamespace(
                completion_text=json.dumps(
                    {
                        "approved_ids": [
                            c["candidate_id"] for c in payload["candidates"]
                        ],
                        "flagged": [],
                    }
                )
            )
        await asyncio.sleep(0.9)
        return await completion(**kwargs)

    plugin.context.llm_generate.side_effect = model
    result = await plugin.learn(scope)
    assert result["status"] == "error" and "自检超时" in result["message"]
    assert "短时提取草稿" in result["message"]
    state = await plugin.store.call("state", scope)
    assert not state["traits"] and state["scope"]["pending"] == 2
    assert state["runs"][0]["review"]["failure"]["phase"] == "review"
    with closing(sqlite3.connect(plugin.store.path)) as db:
        attempted, failures, key, draft = db.execute(
            "SELECT attempted,failures,draft_key,draft FROM learning_runtime WHERE scope=?",
            (scope,),
        ).fetchone()
    assert failures == 1 and key and draft != "[]"
    assert "PRIVATE_KEY_SENTINEL" not in key + draft
    await plugin.terminate()
    reloaded = PersonaPlugin(plugin.context, dict(plugin.settings))
    try:
        await reloaded.initialize()
        assert reloaded.last_attempt[scope] == attempted
        assert reloaded.failures[scope] == 1
        assert time.time() - attempted < reloaded.retry_delay(scope)
        reloaded.settings["interval_seconds"] = 7200
        assert reloaded.retry_delay(scope) == 7200
        reloaded.settings["interval_seconds"] = plugin.settings["interval_seconds"]
        result = await reloaded.learn(scope)
        assert result["status"] == "success" and result["count"] == 4
        assert calls == ["extraction", "extraction", "review", "review"]
        state = await reloaded.store.call("state", scope)
        assert state["scope"]["pending"] == 0
        assert state["runs"][0]["review"]["extraction_cache"]["hit"] is True
        assert state["runs"][0]["review"]["timing"]["review_budget_seconds"] > 1.5
        with closing(sqlite3.connect(reloaded.store.path)) as db:
            assert db.execute(
                "SELECT failures,draft_key,draft FROM learning_runtime WHERE scope=?",
                (scope,),
            ).fetchone() == (0, "", "[]")
    finally:
        await reloaded.terminate()


async def test_new_provider_settings_do_not_reuse_a_failed_review_draft(plugin):
    scope = await sources(plugin)
    plugin.settings["learning_review_enabled"] = True
    provider = SimpleNamespace(
        provider_config={"model": "one"}, get_model=lambda: "one"
    )
    plugin.context.get_provider_by_id = lambda identifier: provider
    calls = []

    async def model(**kwargs):
        payload = json.loads(kwargs["prompt"])
        if "candidates" in payload:
            calls.append("review")
            raise asyncio.TimeoutError
        calls.append("extraction")
        return await completion(**kwargs)

    plugin.context.llm_generate.side_effect = model
    assert (await plugin.learn(scope))["status"] == "error"
    provider.provider_config["api_key"] = "REPLACED_KEY_SENTINEL"
    assert (await plugin.learn(scope))["status"] == "error"
    assert calls == ["extraction", "extraction", "review"] * 2
    assert plugin.failures[scope] == 2
    recovered = await plugin.store.call("initialize")
    assert recovered[scope]["failures"] == 2
    assert plugin.retry_delay(scope) == min(
        plugin.settings["interval_seconds"] * 2, 3600
    )


async def test_draft_storage_failure_does_not_block_review_or_commit(plugin):
    scope = await sources(plugin)
    plugin.settings["learning_review_enabled"] = True
    provider = SimpleNamespace(provider_config={}, get_model=lambda: "controlled")
    plugin.context.get_provider_by_id = lambda identifier: provider
    plugin.store.learning_draft = lambda *args: (_ for _ in ()).throw(
        OSError("Controlled failure")
    )

    async def model(**kwargs):
        payload = json.loads(kwargs["prompt"])
        if "candidates" in payload:
            return SimpleNamespace(
                completion_text=json.dumps(
                    {
                        "approved_ids": [
                            c["candidate_id"] for c in payload["candidates"]
                        ],
                        "flagged": [],
                    }
                )
            )
        return await completion(**kwargs)

    plugin.context.llm_generate.side_effect = model
    assert (await plugin.learn(scope))["status"] == "success"
    assert plugin.context.llm_generate.await_count == 3
    assert (await plugin.store.call("state", scope))["scope"]["pending"] == 0


@pytest.mark.parametrize("change", ["profile", "prompt", "source"])
async def test_changed_learning_inputs_cannot_resume_the_old_extraction(plugin, change):
    scope = await sources(plugin)
    plugin.settings["learning_review_enabled"] = True
    provider = SimpleNamespace(provider_config={}, get_model=lambda: "controlled")
    plugin.context.get_provider_by_id = lambda identifier: provider
    calls = []

    async def model(**kwargs):
        payload = json.loads(kwargs["prompt"])
        if "candidates" in payload:
            calls.append("review")
            raise asyncio.TimeoutError
        calls.append("extraction")
        return await completion(**kwargs)

    plugin.context.llm_generate.side_effect = model
    assert (await plugin.learn(scope))["status"] == "error"
    if change == "profile":
        await plugin.store.call("mutate", scope, "profile", {"reply_style": "保持温和"})
    elif change == "prompt":
        plugin.prompts["learning"] += "\n另一个学习要求。"
    else:
        await plugin.capture(event("new-source", "这条是新素材"))
    assert (await plugin.learn(scope))["status"] == "error"
    assert calls == ["extraction", "extraction", "review"] * 2
    assert not (await plugin.store.call("state", scope))["traits"]


async def test_legacy_attempt_time_expires_drafts_and_material_clear_purges_them(
    plugin,
):
    scope = await sources(plugin)
    run = await plugin.store.call("start_run", scope)
    await plugin.store.call(
        "finish_run",
        run,
        scope,
        [],
        [],
        plugin.settings,
        "",
        "Controlled provider failure",
    )
    run = await plugin.store.call("start_run", scope)
    await plugin.store.call(
        "finish_run",
        run,
        scope,
        [],
        [],
        plugin.settings,
        "",
        "插件卸载中断了学习，素材已保留。",
    )
    with closing(sqlite3.connect(plugin.store.path)) as db, db:
        db.execute("DELETE FROM learning_runtime WHERE scope=?", (scope,))
    recovered = await plugin.store.call("initialize")
    assert recovered[scope]["attempted"] > time.time() - 10
    assert recovered[scope]["failures"] == 1
    assert await plugin.store.call("learning_draft", scope, "exact", [{"id": 1}])
    assert await plugin.store.call("learning_draft", scope, "wrong") is None
    with closing(sqlite3.connect(plugin.store.path)) as db, db:
        db.execute(
            "UPDATE learning_runtime SET draft_created=? WHERE scope=?",
            (time.time() - 3601, scope),
        )
    assert await plugin.store.call("learning_draft", scope, "exact") is None
    await plugin.store.call("prune", plugin.settings["retention_days"])
    with closing(sqlite3.connect(plugin.store.path)) as db:
        assert (
            db.execute(
                "SELECT draft FROM learning_runtime WHERE scope=?", (scope,)
            ).fetchone()[0]
            == "[]"
        )
    await plugin.store.call("learning_draft", scope, "exact", [{"id": 2}])
    # The existing operator action must also remove derived, unreviewed source excerpts.
    await plugin.store.call("mutate", scope, "clear_messages", {})
    assert await plugin.store.call("learning_draft", scope, "exact") is None
