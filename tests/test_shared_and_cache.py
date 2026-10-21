"""Verify common learning, opt-in isolation and stable native request prefixes."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from astrbot.api.provider import ProviderRequest

from astrbot_plugin_ovenheart.settings import DEFAULTS, KINDS, SHARED_SCOPE
from tests.test_runtime import api_call, completion, event, sources

pytest_plugins = ["tests.test_runtime"]


@pytest.fixture
async def shared_plugin(plugin):
    result = await api_call(
        plugin.page_api.action,
        {
            "action": "settings",
            "payload": {
                "session_isolation": False,
                "guidance_delivery": "cached",
                "activation_mode": "auto",
            },
        },
    )
    assert result["status"] == "ok"
    return plugin


async def test_default_shared_profile_can_be_edited_before_first_message(shared_plugin):
    plugin = shared_plugin
    assert DEFAULTS["session_isolation"] is False
    assert DEFAULTS["guidance_delivery"] == "cached"
    response = await api_call(plugin.page_api.state)
    assert response["data"]["scopes"] == []
    assert response["data"]["scope"]["id"] == SHARED_SCOPE
    result = await api_call(
        plugin.page_api.action,
        {"action": "profile", "scope": "", "payload": {"identity": "共用的身份补充"}},
    )
    assert result["status"] == "ok"
    preview = await api_call(plugin.page_api.preview, {"scope": "", "query": "你好"})
    assert "共用的身份补充" in preview["data"]["guidance"]


async def test_shared_learning_accumulates_across_sources_without_mixing_raw_batches(
    shared_plugin,
):
    plugin = shared_plugin
    first = await sources(plugin)
    assert (await plugin.learn(first))["count"] == 4
    await plugin.capture(event("1", session="456"))
    await plugin.capture(event("2", "好家伙，还是你懂我", session="456"))
    scopes = (await plugin.store.call("state"))["scopes"]
    second = next(s["id"] for s in scopes if s["id"] != first)
    assert len(await plugin.store.call("batch", second, 30)) == 2
    result = await plugin.learn(second)
    assert result["status"] == "success" and result["count"] == 0
    traits = (await plugin.store.call("state", SHARED_SCOPE))["traits"]
    assert len(traits) == 4 and all(t["support_count"] == 2 for t in traits)
    assert {e["id"] for t in traits for e in t["evidence"]} == {1, 2, 3, 4}
    for scope in (first, second):
        assert not await plugin.store.call("batch", scope, 30)
        assert len((await plugin.store.call("state", scope))["runs"]) == 1
    report = (await api_call(plugin.page_api.state, query=f"scope={second}"))["data"]
    assert report["effect_scope"] == SHARED_SCOPE and report["active_count"] == 4
    assert {t["scope"] for t in report["traits"]} == {SHARED_SCOPE}


async def test_shared_habits_apply_to_another_persona_but_history_stays_local(
    shared_plugin,
):
    plugin = shared_plugin
    first = await sources(plugin)
    await plugin.learn(first)
    await api_call(
        plugin.page_api.action,
        {"action": "profile", "scope": first, "payload": {"identity": "共用身份补充"}},
    )
    await plugin.store.call("mutate", first, "profile", {"identity": "旧的独立补充"})
    plugin.context.persona_manager.resolve_selected_persona.return_value = (
        "another",
        {"prompt": "另一核心人格"},
        None,
        False,
    )
    selected_queries = []
    original = plugin.retriever.select

    async def record(*args, **kwargs):
        selected_queries.append(args)
        return await original(*args, **kwargs)

    plugin.retriever.select = record
    incoming = event("other", "好家伙", session="456")
    request = ProviderRequest(
        prompt="好家伙",
        system_prompt="另一核心人格",
        contexts=[{"role": "user", "content": "B_ONLY_HISTORY"}],
    )
    await plugin.apply_persona(incoming, request)
    assert "共用身份补充" in request.system_prompt
    assert "旧的独立补充" not in request.system_prompt
    assert request.system_prompt.startswith("另一核心人格")
    tail = request.extra_user_content_parts[-1]
    assert tail.model_dump_for_context()["_no_save"] and "简短反问" in tail.text
    assert selected_queries[-1][0] == SHARED_SCOPE
    assert "B_ONLY_HISTORY" in selected_queries[-1][2]
    assert "好家伙，还是你懂我" not in selected_queries[-1][2]
    await plugin.record_delivery(incoming)
    second, _, _ = await plugin.resolve_scope(incoming, request)
    assert (await plugin.store.call("state", second))["applications"]
    assert (
        sum(
            t["use_count"]
            for t in (await plugin.store.call("state", SHARED_SCOPE))["traits"]
        )
        > 0
    )
    await api_call(
        plugin.page_api.action,
        {"action": "settings", "payload": {"session_isolation": True}},
    )
    isolated = ProviderRequest(prompt="好家伙", system_prompt="另一核心人格")
    await plugin.apply_persona(event("isolated", "好家伙", session="456"), isolated)
    assert (
        isolated.system_prompt == "另一核心人格"
        and not isolated.extra_user_content_parts
    )
    # Returning to common mode restores the same shared profile and learned records.
    await api_call(
        plugin.page_api.action,
        {"action": "settings", "payload": {"session_isolation": False}},
    )
    restored = ProviderRequest(prompt="好家伙", system_prompt="另一核心人格")
    await plugin.apply_persona(event("restored", "好家伙", session="456"), restored)
    assert (
        "共用身份补充" in restored.system_prompt and restored.extra_user_content_parts
    )


async def test_cached_delivery_keeps_system_and_history_prefix_equal_across_selected_habits(
    shared_plugin,
):
    plugin = shared_plugin
    scope = await sources(plugin)
    await plugin.learn(scope)
    await api_call(
        plugin.page_api.action,
        {
            "action": "profile",
            "scope": scope,
            "payload": {"identity": "稳定的共用底稿"},
        },
    )
    traits = (await plugin.store.call("guidance", scope, True))["traits"]
    chosen = [
        next(t for t in traits if t["kind"] == kind)
        for kind in ("expression", "behavior")
    ]
    history = [
        {"role": "user", "content": "相同的历史问题"},
        {"role": "assistant", "content": "相同的历史回答"},
    ]
    requests = []
    for index, trait in enumerate(chosen):
        plugin.retriever.select = AsyncMock(
            return_value={"traits": [trait], "mode": "contextual", "message": "fixture"}
        )
        request = ProviderRequest(
            prompt=f"本轮输入{index}",
            system_prompt="固定宿主人格",
            contexts=json.loads(json.dumps(history)),
        )
        incoming = event(f"cached-{index}", request.prompt)
        await plugin.apply_persona(incoming, request)
        requests.append(request)
        assert incoming.get_extra("ovenheart_application")["ids"] == [trait["id"]]
        assert len(request.extra_user_content_parts) == 1
        assert trait["content"] in request.extra_user_content_parts[0].text
        assert trait["content"] not in request.system_prompt
        assert request.extra_user_content_parts[0].model_dump_for_context()["_no_save"]
        assert (
            len(request.system_prompt)
            - len("固定宿主人格")
            + len(request.extra_user_content_parts[0].text)
            <= plugin.settings["prompt_budget"] + 2
        )
    assert requests[0].system_prompt == requests[1].system_prompt
    assert requests[0].contexts == requests[1].contexts == history
    first, second = [await request.assemble_context() for request in requests]
    assert first != second
    assert "_no_save" in json.dumps(first)
    # The actual native user-message builder places changing parts after the current user text.
    assert "本轮输入0" in json.dumps(first, ensure_ascii=False)
    assert first["content"][0]["text"] == "本轮输入0"
    assert first["content"][-1]["text"] == requests[0].extra_user_content_parts[0].text


@pytest.mark.parametrize("start_isolated", [False, True])
async def test_mode_change_during_model_call_rejects_late_commit(
    plugin, start_isolated
):
    await api_call(
        plugin.page_api.action,
        {"action": "settings", "payload": {"session_isolation": start_isolated}},
    )
    scope = await sources(plugin)
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed(**kwargs):
        started.set()
        await release.wait()
        return await completion(**kwargs)

    plugin.context.llm_generate.side_effect = delayed
    task = asyncio.create_task(plugin.learn(scope))
    await started.wait()
    await api_call(
        plugin.page_api.action,
        {"action": "settings", "payload": {"session_isolation": not start_isolated}},
    )
    release.set()
    result = await task
    assert result["status"] == "error" and "隔离模式" in result["message"]
    assert len(await plugin.store.call("batch", scope, 30)) == 2
    assert (await plugin.store.call("library"))["total"] == 0


async def test_disabling_every_kind_during_generation_preserves_sources(plugin):
    scope = await sources(plugin)
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed(**kwargs):
        started.set()
        await release.wait()
        return await completion(**kwargs)

    plugin.context.llm_generate.side_effect = delayed
    task = asyncio.create_task(plugin.learn(scope))
    await started.wait()
    await api_call(
        plugin.page_api.action,
        {"action": "settings", "payload": {f"learn_{kind}": False for kind in KINDS}},
    )
    release.set()
    result = await task
    assert result["status"] == "error" and "全部学习类别" in result["message"]
    assert len(await plugin.store.call("batch", scope, 30)) == 2


async def test_common_mode_reads_legacy_active_habits_without_overwriting_local_profiles(
    shared_plugin,
):
    from tests.test_store import TRAIT

    plugin = shared_plugin
    scope = await sources(plugin)
    batch = await plugin.store.call("batch", scope, 30)
    run = await plugin.store.call("start_run", scope)
    settings = plugin.settings | {"session_isolation": True, "activation_mode": "auto"}
    await plugin.store.call(
        "finish_run", run, scope, batch, [TRAIT], settings, "legacy fixture"
    )
    await plugin.store.call("mutate", scope, "profile", {"identity": "旧的独立身份"})
    shared = await plugin.store.call("guidance", "b" * 24, True)
    assert len(shared["traits"]) == 1 and shared["scope"]["profile"]["identity"] == ""
    assert not (await plugin.store.call("guidance", "b" * 24))["traits"]
    assert (await plugin.store.call("guidance", scope))["scope"]["profile"][
        "identity"
    ] == "旧的独立身份"
    trait = shared["traits"][0]
    await plugin.store.call(
        "vectors",
        "signature",
        scope,
        [{"id": trait["id"], "digest": "fixture", "vector": [1.0, 0.0]}],
    )
    assert trait["id"] in await plugin.store.call("vectors", "signature", SHARED_SCOPE)
    assert not await plugin.store.call("vectors", "signature", "b" * 24)


async def test_commit_transaction_rechecks_mode_and_all_disabled_categories(
    shared_plugin,
):
    plugin = shared_plugin
    scope = await sources(plugin)
    batch = await plugin.store.call("batch", scope, 30)
    for patch, pattern in (
        ({"session_isolation": True}, "隔离模式"),
        ({f"learn_{kind}": False for kind in KINDS}, "全部学习类别"),
    ):
        await plugin.store.call(
            "settings",
            {"session_isolation": False, **{f"learn_{kind}": True for kind in KINDS}},
        )
        run = await plugin.store.call("start_run", scope)
        await plugin.store.call("settings", patch)
        with pytest.raises(ValueError, match=pattern):
            await plugin.store.call(
                "finish_run",
                run,
                scope,
                batch,
                [],
                plugin.settings,
                "",
                "",
                None,
                None,
                None,
                None,
                None,
                True,
                False,
            )
        assert len(await plugin.store.call("batch", scope, 30)) == 2


async def test_cached_selector_serializes_stable_candidates_before_changing_query(
    shared_plugin,
):
    from types import SimpleNamespace

    plugin = shared_plugin
    scope = await sources(plugin)
    await plugin.learn(scope)
    pool = (await plugin.store.call("guidance", scope, True))["traits"]
    plugin.context.llm_generate = AsyncMock(
        return_value=SimpleNamespace(completion_text='{"selected_ids":[]}')
    )
    for query, candidates in (
        ("本轮问题一", pool),
        ("本轮问题二", list(reversed(pool))),
    ):
        assert (
            await plugin.retriever.contextual_ids(
                candidates, query, plugin.settings, "fixture", "相同身份"
            )
            == []
        )
    first, second = [
        call.kwargs["prompt"] for call in plugin.context.llm_generate.await_args_list
    ]
    assert first.split('"conversation":')[0] == second.split('"conversation":')[0]
    assert [c["id"] for c in json.loads(first)["candidates"]] == sorted(
        t["id"] for t in pool
    )


async def test_stale_profile_form_cannot_write_into_a_different_effect_scope(
    shared_plugin,
):
    plugin = shared_plugin
    scope = await sources(plugin)
    await api_call(
        plugin.page_api.action,
        {"action": "settings", "payload": {"session_isolation": True}},
    )
    stale_common = await api_call(
        plugin.page_api.action,
        {
            "action": "profile",
            "scope": SHARED_SCOPE,
            "effect_scope": SHARED_SCOPE,
            "payload": {"identity": "过时的共用编辑"},
        },
    )
    assert stale_common["status"] == "error" and "作用范围" in stale_common["message"]
    await api_call(
        plugin.page_api.action,
        {"action": "settings", "payload": {"session_isolation": False}},
    )
    stale_local = await api_call(
        plugin.page_api.action,
        {
            "action": "profile",
            "scope": scope,
            "effect_scope": scope,
            "payload": {"identity": "过时的独立编辑"},
        },
    )
    assert stale_local["status"] == "error" and "作用范围" in stale_local["message"]
    assert (await plugin.store.call("guidance", scope, True))["scope"]["profile"][
        "identity"
    ] == ""
    assert (await plugin.store.call("guidance", scope))["scope"]["profile"][
        "identity"
    ] == ""
