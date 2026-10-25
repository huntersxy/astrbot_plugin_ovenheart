"""Verify explicit model examples cannot become real conversations or exposures."""

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("astrbot.api")

from tests.test_runtime import api_call, sources  # noqa: E402

pytest_plugins = ["tests.test_runtime"]


@pytest.mark.parametrize("role", ["assistant", "err"])
async def test_preview_reports_and_accounts_actual_model_without_exposing_errors(
    plugin, role
):
    from astrbot.core.provider.entities import TokenUsage

    scope = await sources(plugin)
    plugin.settings["selection_mode"] = "lexical"
    plugin.context.llm_generate.side_effect = None
    plugin.context.llm_generate.return_value = SimpleNamespace(
        role=role,
        completion_text="正文" if role == "assistant" else "PRIVATE_PROVIDER_ERROR",
        raw_completion={"model": "actual-reply-model"},
        usage=TokenUsage(input_cached=4, input_other=7, output=3),
    )
    result = await api_call(
        plugin.page_api.model_preview, {"scope": scope, "query": "你好"}
    )
    if role == "assistant":
        assert result["data"]["model"] == "actual-reply-model"
        assert result["data"]["usage"] == {
            "input_cached": 4,
            "input_other": 7,
            "output": 3,
        }
        assert result["data"]["provider_id"]
    else:
        assert result["status"] == "error"
        assert "PRIVATE_PROVIDER_ERROR" not in str(result)
    await plugin.auxiliary.flush()
    row = next(
        row
        for row in await plugin.store.call("auxiliary_usage")
        if row["stage"] == "model_preview"
    )
    assert row["calls"] == 1 and row["reported"] == 1
    assert row["model"] == "actual-reply-model"
    assert row["errors"] == int(role == "err")


@pytest.mark.parametrize("delivery", ["system", "dual", "cached"])
async def test_model_preview_uses_live_delivery_parts_without_writes_or_tools(
    plugin, delivery
):
    scope = await sources(plugin)
    await plugin.learn(scope)
    expression = next(
        t
        for t in (await plugin.store.call("state", scope))["traits"]
        if t["kind"] == "expression"
    )
    await plugin.store.call(
        "mutate", scope, "trait", {"id": expression["id"], "status": "active"}
    )
    plugin.settings.update(selection_mode="lexical", guidance_delivery=delivery)
    plugin.retriever.status[scope] = {"mode": "runtime", "message": "正式回复状态"}
    plugin.context.llm_generate.side_effect = None
    plugin.context.llm_generate.return_value = SimpleNamespace(
        completion_text="<img src=x>这是纯文本示例"
    )
    plugin.context.llm_generate.reset_mock()
    before = await plugin.store.call("state", scope)
    history_reads = (
        plugin.context.conversation_manager.get_curr_conversation_id.await_count
    )
    result = await api_call(
        plugin.page_api.model_preview, {"scope": scope, "query": "好家伙，今天有点难受"}
    )
    assert result["status"] == "ok"
    assert result["data"]["selected_ids"] == [expression["id"]]
    assert result["data"]["reply"] == "<img src=x>这是纯文本示例"
    sent = plugin.context.llm_generate.await_args.kwargs
    assert sent["contexts"] == [] and sent["tools"] is None
    assert "宿主人格锚点" in sent["system_prompt"]
    parts = sent["extra_user_content_parts"]
    assert expression["content"] in sent["system_prompt"] + "".join(
        part.text for part in parts
    )
    if delivery == "cached":
        assert expression["content"] not in sent["system_prompt"]
    assert all(part.model_dump_for_context()["_no_save"] for part in parts)
    assert (await plugin.store.call("state", scope)) == before
    assert (
        plugin.context.conversation_manager.get_curr_conversation_id.await_count
        == history_reads
    )
    assert plugin.retriever.status[scope]["mode"] == "runtime"
    assert not plugin.events[scope]._has_send_oper
    plugin.context.llm_generate.assert_awaited_once()


async def test_model_preview_redacts_provider_failures_and_releases_its_slot(plugin):
    scope = await sources(plugin)
    plugin.settings["selection_mode"] = "lexical"
    plugin.context.llm_generate.side_effect = ValueError(
        "api_key=SECRET_PREVIEW_SENTINEL"
    )
    failed = await api_call(
        plugin.page_api.model_preview, {"scope": scope, "query": "你好"}
    )
    assert failed["status"] == "error" and "SECRET" not in str(failed)
    assert not plugin.page_api.model_preview_running
    count = plugin.context.llm_generate.await_count
    rejected = await api_call(
        plugin.page_api.model_preview, {"scope": "", "query": "你好"}
    )
    assert (
        rejected["status"] == "error"
        and plugin.context.llm_generate.await_count == count
    )
    plugin.context.llm_generate.side_effect = None
    plugin.context.llm_generate.return_value = SimpleNamespace(
        completion_text="重试成功"
    )
    retried = await api_call(
        plugin.page_api.model_preview, {"scope": scope, "query": "你好"}
    )
    assert retried["status"] == "ok" and retried["data"]["reply"] == "重试成功"


async def test_model_preview_rejects_overlap_and_discards_an_outdated_profile(plugin):
    scope = await sources(plugin)
    plugin.settings["selection_mode"] = "lexical"
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(**kwargs):
        entered.set()
        await release.wait()
        return SimpleNamespace(completion_text="这条旧回复不应显示")

    plugin.context.llm_generate.side_effect = delayed
    work = asyncio.create_task(
        api_call(plugin.page_api.model_preview, {"scope": scope, "query": "你好"})
    )
    try:
        await asyncio.wait_for(entered.wait(), 2)
        overlap = await api_call(
            plugin.page_api.model_preview, {"scope": scope, "query": "另一个示例"}
        )
        assert overlap["status"] == "error" and "正在生成" in overlap["message"]
        await plugin.store.call(
            "mutate", scope, "profile", {"identity": "更新后的人格"}
        )
        release.set()
        result = await work
        assert result["status"] == "error" and "已改变" in result["message"]
    finally:
        release.set()
        await asyncio.gather(work, return_exceptions=True)
    assert not plugin.page_api.model_preview_running
