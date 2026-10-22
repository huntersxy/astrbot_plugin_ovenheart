"""Verify editable prompts cannot publish superseded or half-applied results."""

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
from astrbot.api.provider import ProviderRequest

from astrbot_plugin_ovenheart.store import Store
from tests.test_runtime import api_call, completion, event, sources

pytest_plugins = ["tests.test_runtime"]


@pytest.mark.parametrize("phase", ["extract", "review"])
async def test_prompt_change_during_learning_preserves_material(plugin, phase):
    await plugin.update_settings({"learning_review_enabled": True})
    scope = await sources(plugin)
    entered, release = asyncio.Event(), asyncio.Event()

    async def generate(**kwargs):
        payload = json.loads(kwargs["prompt"])
        reviewing = "candidates" in payload
        if reviewing == (phase == "review"):
            entered.set()
            await release.wait()
        if reviewing:
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

    plugin.context.llm_generate.side_effect = generate
    work = asyncio.create_task(plugin.learn(scope))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await plugin.update_prompts({"learning": "新学习规则，继续引用真实证据。"})
    finally:
        release.set()
    result = await work
    assert result["status"] == "error" and "提示词" in result["message"]
    assert len(await plugin.store.call("batch", scope, 30)) == 2
    assert (await plugin.store.call("library", "all", "all", "all"))["total"] == 0


async def test_prompt_change_during_selector_wait_prevents_late_injection(plugin):
    await plugin.update_settings({"activation_mode": "auto"})
    scope = await sources(plugin)
    await plugin.learn(scope)
    trait = (await plugin.store.call("state", scope))["traits"][0]
    await plugin.update_settings({"selection_mode": "contextual"})
    entered, release = asyncio.Event(), asyncio.Event()

    async def select(**kwargs):
        entered.set()
        await release.wait()
        return SimpleNamespace(
            completion_text=json.dumps({"selected_ids": [trait["id"]]})
        )

    plugin.context.llm_generate.side_effect = select
    request = ProviderRequest(prompt="好家伙", system_prompt="原宿主人格")
    work = asyncio.create_task(plugin.apply_persona(event("late-selector"), request))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await plugin.update_prompts({"selector": "仅选择当下真正适用的习惯。"})
    finally:
        release.set()
    await work
    assert request.system_prompt == "原宿主人格"
    assert not request.extra_user_content_parts


async def test_prompt_save_rejects_a_superseded_editor(plugin):
    revision = plugin.prompts_revision
    first = await api_call(
        plugin.page_api.prompt_save,
        {
            "prompts": {"learning": "新的学习规则"},
            "revision": revision,
        },
    )
    assert first["status"] == "ok"
    stale = await api_call(
        plugin.page_api.prompt_save,
        {
            "prompts": {"learning": "旧页面的规则"},
            "revision": revision,
        },
    )
    assert stale["status"] == "error" and "另一页面" in stale["message"]
    assert plugin.prompts["learning"] == "新的学习规则"


async def test_unknown_prompt_key_cannot_be_reset_silently(plugin):
    result = await api_call(plugin.page_api.prompt_save, {"prompts": {"unknown": None}})
    assert result["status"] == "error"
    assert not await plugin.store.call("prompt_overrides")


async def test_cancelled_prompt_save_still_publishes_committed_wording(
    plugin, monkeypatch
):
    started, release = threading.Event(), threading.Event()
    original = Store.prompt_overrides

    def delayed(self, db, patch=None):
        if patch is not None:
            started.set()
            assert release.wait(5)
        return original(self, db, patch)

    monkeypatch.setattr(Store, "prompt_overrides", delayed)
    work = asyncio.create_task(plugin.update_prompts({"learning": "已保存的新规则"}))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        work.cancel()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await work
    assert plugin.prompts["learning"] == "已保存的新规则"
    assert plugin.prompt_overrides == await plugin.store.call("prompt_overrides")
    assert plugin.retriever.templates == plugin.prompts


async def test_commit_rechecks_persisted_prompt_snapshot(plugin, monkeypatch):
    scope = await sources(plugin)
    original = plugin.store.call

    async def before_commit(operation, *args):
        if operation == "finish_run" and len(args) >= 15 and not args[6]:
            await original("prompt_overrides", {"learning": "已入库的新规则"})
        return await original(operation, *args)

    monkeypatch.setattr(plugin.store, "call", before_commit)
    result = await plugin.learn(scope)
    assert result["status"] == "error" and "提示词" in result["message"]
    assert len(await plugin.store.call("batch", scope, 30)) == 2
    assert (await plugin.store.call("library", "all", "all", "all"))["total"] == 0
