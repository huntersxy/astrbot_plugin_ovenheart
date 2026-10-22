"""Native-SDK admission tests for participation, interruption and tool boundaries."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("astrbot.api")

from astrbot.api.provider import ProviderRequest  # noqa: E402
from astrbot.core.agent.tool import FunctionTool, ToolSet  # noqa: E402
from astrbot.core.db.po import Conversation  # noqa: E402

from astrbot_plugin_ovenheart.main import ParticipationFilter  # noqa: E402
from astrbot_plugin_ovenheart.participation import parse_plan  # noqa: E402
from astrbot_plugin_ovenheart.settings import DEFAULTS  # noqa: E402
from tests.test_runtime import (  # noqa: E402
    SessionServiceManager,
    api_call,
    event,
)

pytest_plugins = ["tests.test_runtime"]


def decision(action="reply", **patch):
    """Build a valid planner response for a controlled scenario.

    Args:
        action: Reply, wait or observe.
        **patch: Fields to replace.

    Returns:
        Completion-shaped planner output.
    """
    return SimpleNamespace(
        completion_text=json.dumps(
            {
                "action": action,
                "confidence": 0.9,
                "reason": "对方在向群内寻求安慰，简短参与有帮助。",
                "intent": "先接住情绪，再问是否愿意说说。",
                "wait_seconds": 1 if action == "wait" else 0,
                **patch,
            }
        )
    )


@pytest.fixture
async def participant(plugin, monkeypatch):
    plugin.settings.update(
        participation_mode="auto",
        participation_min_messages=1,
        participation_debounce=0,
    )
    monkeypatch.setattr(
        SessionServiceManager,
        "should_process_llm_request",
        AsyncMock(return_value=True),
    )
    plugin.context.llm_generate.side_effect = None
    plugin.context.llm_generate.return_value = decision()
    plugin.context.conversation_manager.new_conversation = AsyncMock(
        return_value="native-conv"
    )
    plugin.context.conversation_manager.get_conversation = AsyncMock(
        return_value=Conversation(
            platform_id="test",
            user_id="qq:GroupMessage:test",
            cid="native-conv",
            history='[{"role":"assistant","content":"上次聊到这里"}]',
            persona_id="default",
        )
    )
    return plugin


async def requests(plugin, incoming):
    """Collect only requests actually yielded by the native plugin handler.

    Args:
        plugin: Initialized plugin.
        incoming: Real SDK event.

    Returns:
        Handler outputs without executing a chat or sending platform messages.
    """
    return [request async for request in plugin.participate(incoming)]


async def test_off_is_passive_and_direct_wakes_are_not_intercepted(participant):
    incoming = event()
    participant.settings["participation_mode"] = "off"
    assert not ParticipationFilter().filter(incoming, {})
    assert await requests(participant, incoming) == []
    participant.settings["participation_mode"] = "auto"
    incoming.is_at_or_wake_command = True
    assert not ParticipationFilter().filter(incoming, {})
    assert await requests(participant, incoming) == []
    assert incoming.is_at_or_wake_command
    participant.context.llm_generate.assert_not_awaited()


async def test_positive_decision_yields_exactly_one_native_request(participant):
    incoming = event(text="今天又搞砸了，有点难过。")
    assert ParticipationFilter().filter(incoming, {})
    output = await requests(participant, incoming)
    assert len(output) == 1 and isinstance(output[0], ProviderRequest)
    assert output[0].prompt == incoming.get_message_str()
    assert output[0].conversation.cid == "native-conv"
    assert incoming.call_llm and not incoming.is_at_or_wake_command
    assert not incoming._has_send_oper
    scope = incoming.get_extra("ovenheart_decision")["scope"]
    state = await participant.store.call("state", scope)
    assert state["decisions"][0]["outcome"] == "admitted"
    assert state["scope"] and state["scope"]["pending"] == 0


async def test_observe_mode_records_decisions_but_never_yields_reply(participant):
    participant.settings["participation_mode"] = "observe"
    incoming = event()
    assert await requests(participant, incoming) == []
    scope = (await participant.store.call("state"))["scopes"][0]["id"]
    assert (await participant.store.call("state", scope))["decisions"][0][
        "outcome"
    ] == "observed"
    assert not incoming.call_llm and not incoming._has_send_oper


async def test_command_handlers_win_but_passive_listeners_can_coexist(participant):
    from astrbot.core.star.filter.command import CommandFilter

    command = event("command")
    command.set_extra(
        "activated_handlers", [SimpleNamespace(event_filters=[CommandFilter("ping")])]
    )
    assert await requests(participant, command) == []
    participant.context.llm_generate.assert_not_awaited()
    listener = event("listener")
    listener.set_extra("activated_handlers", [SimpleNamespace(event_filters=[])])
    assert len(await requests(participant, listener)) == 1


async def test_native_process_stage_invokes_agent_once_for_plugin_request(participant):
    from astrbot.core.pipeline.context_utils import call_handler
    from astrbot.core.pipeline.process_stage.stage import ProcessStage

    incoming = event()
    incoming.set_extra("activated_handlers", [SimpleNamespace(event_filters=[])])
    invoked = []

    class StarStage:
        async def process(self, event):
            async for output in call_handler(event, participant.participate):
                yield output

    class AgentStage:
        async def process(self, event):
            invoked.append(event.get_extra("provider_request"))
            yield None

    stage = ProcessStage()
    stage.ctx = SimpleNamespace(astrbot_config={"provider_settings": {"enable": True}})
    stage.star_request_sub_stage = StarStage()
    stage.agent_sub_stage = AgentStage()
    async for _ in stage.process(incoming):
        pass
    assert len(invoked) == 1 and isinstance(invoked[0], ProviderRequest)
    assert not incoming.is_at_or_wake_command


async def test_native_collection_reloads_bound_history_before_persona_application(
    participant,
):
    from astrbot.core.astr_main_agent import (
        MainAgentBuildConfig,
        collect_initial_request,
    )

    incoming = event()
    request = (await requests(participant, incoming))[0]
    participant.context.conversation_manager.get_conversation.return_value.history = (
        '[{"role":"assistant","content":"等待期间新增的上一轮回复"}]'
    )
    incoming.set_extra("provider_request", request)
    current, _ = await collect_initial_request(
        incoming, participant.context, MainAgentBuildConfig(tool_call_timeout=30)
    )
    assert current.conversation.cid == request.conversation.cid
    assert current.contexts[-1]["content"] == "等待期间新增的上一轮回复"
    assert current.conversation.persona_id == "default"


async def test_external_agent_runner_never_receives_automatic_request(participant):
    participant.context.get_config = lambda umo: {
        "plugin_set": ["*"],
        "agent_runner": {"runner_type": "dify"},
    }
    assert await requests(participant, event()) == []
    participant.context.llm_generate.assert_not_awaited()


@pytest.mark.parametrize(
    ("response", "failure", "expected"),
    [
        (SimpleNamespace(completion_text="not json"), None, "格式不正确"),
        (None, asyncio.TimeoutError(), "调用超时"),
        (None, ValueError("provider diagnostic contains credentials"), "调用失败"),
    ],
)
async def test_planner_failures_are_actionable_without_provider_text(
    participant, response, failure, expected
):
    participant.context.llm_generate.return_value = response
    participant.context.llm_generate.side_effect = failure
    assert await requests(participant, event()) == []
    scope = (await participant.store.call("state"))["scopes"][0]["id"]
    record = (await participant.store.call("state", scope))["decisions"][0]
    assert record["outcome"] == "error" and expected in record["reason"]
    assert "credentials" not in record["reason"]


async def test_burst_is_coalesced_onto_latest_live_event(participant):
    participant.settings.update(
        participation_debounce=0.1, participation_min_messages=3
    )
    inputs = [event(str(i), f"连着说的第 {i} 句话") for i in range(3)]
    outputs = await asyncio.gather(*(requests(participant, e) for e in inputs))
    assert outputs[0] == outputs[1] == [] and len(outputs[2]) == 1
    participant.context.llm_generate.assert_awaited_once()
    prompt = json.loads(participant.context.llm_generate.await_args.kwargs["prompt"])
    assert len(prompt["recent_messages"]) == 3
    assert outputs[2][0].prompt == inputs[2].get_message_str()


async def test_delayed_older_scope_resolution_does_not_cancel_latest_burst(
    participant, monkeypatch
):
    participant.settings.update(
        participation_min_messages=2, participation_debounce=0.5
    )
    older, latest = event("delayed-old", "较早的一条"), event("latest", "后发的一条")
    # Native wall-clock timestamps can tie when fixtures are created in one tick.
    latest.created_at = older.created_at + 0.01
    entered, release = asyncio.Event(), asyncio.Event()
    resolve = participant.resolve_scope

    async def delayed(event, req=None):
        result = await resolve(event, req)
        if event is older:
            entered.set()
            await release.wait()
        return result

    monkeypatch.setattr(participant, "resolve_scope", delayed)
    first = asyncio.create_task(requests(participant, older))
    await entered.wait()
    last = asyncio.create_task(requests(participant, latest))
    try:
        for _ in range(500):
            if any(
                any(m["source"] == "latest" for m in w["messages"])
                for w in participant.participation.windows.values()
            ):
                break
            await asyncio.sleep(0.001)
        else:
            pytest.fail("The latest native turn did not reach its debounce window")
        release.set()
        one, two = await asyncio.gather(first, last)
        assert one == [] and len(two) == 1
        payload = json.loads(
            participant.context.llm_generate.await_args.kwargs["prompt"]
        )
        assert [m["source"] for m in payload["recent_messages"]] == [
            "delayed-old",
            "latest",
        ]
    finally:
        release.set()
        for task in (first, last):
            task.cancel()
        await asyncio.gather(first, last, return_exceptions=True)


async def test_new_message_cancels_obsolete_inflight_planner(participant):
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def slow(**kwargs):
        entered.set()
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()

    participant.context.llm_generate.side_effect = slow
    old = asyncio.create_task(requests(participant, event("old")))
    await entered.wait()
    assert await requests(participant, event("new")) == []
    assert await old == [] and cancelled.is_set()
    assert participant.participation.inflight == 0


async def test_wait_replans_once_and_keeps_native_event_alive(participant):
    participant.context.llm_generate.side_effect = [decision("wait"), decision()]
    incoming = event()
    assert len(await requests(participant, incoming)) == 1
    assert participant.context.llm_generate.await_count == 2
    second = json.loads(participant.context.llm_generate.await_args.kwargs["prompt"])
    assert second["wait_elapsed"] is True
    scope = incoming.get_extra("ovenheart_decision")["scope"]
    decisions = (await participant.store.call("state", scope))["decisions"]
    assert [d["outcome"] for d in decisions] == ["admitted", "waiting"]


async def test_pause_during_planning_cancels_late_admission(participant):
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(**kwargs):
        entered.set()
        await release.wait()
        return decision()

    participant.context.llm_generate.side_effect = delayed
    work = asyncio.create_task(requests(participant, event()))
    await entered.wait()
    await api_call(
        participant.page_api.action,
        {"action": "settings", "payload": {"participation_mode": "off"}},
    )
    release.set()
    assert await work == []


async def test_native_tools_are_isolated_and_only_explicitly_allowed(participant):
    from astrbot.core.agent.message import (
        Message,
        TextPart,
        dump_messages_with_checkpoints,
    )

    incoming = event()
    request = (await requests(participant, incoming))[0]
    tools = ToolSet(
        [
            FunctionTool(
                name=name,
                description=name,
                parameters={"type": "object", "properties": {}},
            )
            for name in ("qq_group_info", "qq_group_member_manage")
        ]
    )
    request.func_tool = tools
    request.system_prompt = "宿主身份与规则"
    participant.settings["autonomous_tools"] = ["qq_group_info"]
    await participant.apply_participation(incoming, request)
    assert [t.name for t in request.func_tool.tools] == ["qq_group_info"]
    assert len(tools.tools) == 2
    assert request.system_prompt.startswith("宿主身份与规则")
    assert "先接住情绪" in request.system_prompt and request.extra_user_content_parts
    assert all(isinstance(p, TextPart) for p in request.extra_user_content_parts)
    context = await request.assemble_context()
    persisted = dump_messages_with_checkpoints([Message.model_validate(context)])
    assert "reply_goal" in str(context) and "reply_goal" not in str(persisted)
    assert incoming.get_message_str() in str(persisted)
    direct = ProviderRequest(func_tool=tools, system_prompt="宿主")
    await participant.apply_participation(event("direct"), direct)
    assert direct.func_tool is tools and direct.system_prompt == "宿主"


def build_tools():
    """Build a native tool set with two QQ-enhance style tools.

    Returns:
        Tool set the host would hand to the autonomous request.
    """
    return ToolSet(
        [
            FunctionTool(
                name=name,
                description=name,
                parameters={"type": "object", "properties": {}},
            )
            for name in ("qq_group_info", "qq_group_member_manage")
        ]
    )


def skills_like_config():
    """Build the host config shape used by a skills_like Agent runner.

    Returns:
        Config mapping matching AstrBot's agent_runner section.
    """
    return {
        "plugin_set": ["*"],
        "agent_runner": {"config": {"misc": {"tool_schema_mode": "skills_like"}}},
    }


def test_tool_schema_mode_follows_the_native_default_and_fallback():
    from astrbot_plugin_ovenheart.main import tool_schema_mode

    assert tool_schema_mode(None) == "full"
    assert tool_schema_mode({}) == "full"
    assert tool_schema_mode({"agent_runner": {}}) == "full"
    assert tool_schema_mode({"agent_runner": {"config": {"misc": {}}}}) == "full"
    unknown = {"agent_runner": {"config": {"misc": {"tool_schema_mode": "auto"}}}}
    assert tool_schema_mode(unknown) == "full"
    assert tool_schema_mode(skills_like_config()) == "skills_like"


async def test_skills_like_host_keeps_entries_but_exposes_only_allowed_tools(
    participant, monkeypatch
):
    incoming = event()
    request = (await requests(participant, incoming))[0]
    tools = build_tools()
    request.func_tool = tools
    request.system_prompt = "宿主身份与规则"
    participant.settings["autonomous_tools"] = ["qq_group_info"]
    monkeypatch.setattr(
        participant.context, "get_config", lambda umo: skills_like_config()
    )
    await participant.apply_participation(incoming, request)
    assert len(request.func_tool.tools) == 2
    assert bool(request.func_tool)
    assert [t.name for t in request.func_tool.tools if t.active] == ["qq_group_info"]
    assert request.func_tool.get_light_tool_set().names() == ["qq_group_info"]
    assert request.func_tool.get_param_only_tool_set().names() == ["qq_group_info"]
    assert [t.name for t in tools.tools] == ["qq_group_info", "qq_group_member_manage"]
    assert all(t.active for t in tools.tools)


async def test_skills_like_empty_allowlist_still_yields_a_truthy_tool_set(
    participant, monkeypatch
):
    incoming = event()
    request = (await requests(participant, incoming))[0]
    tools = build_tools()
    request.func_tool = tools
    request.system_prompt = "宿主身份与规则"
    participant.settings["autonomous_tools"] = []
    monkeypatch.setattr(
        participant.context, "get_config", lambda umo: skills_like_config()
    )
    await participant.apply_participation(incoming, request)
    assert len(request.func_tool.tools) == 2
    assert bool(request.func_tool)
    assert request.func_tool.get_light_tool_set().names() == []
    assert request.func_tool.get_param_only_tool_set().names() == []
    assert all(t.active for t in tools.tools)


async def test_new_input_cancels_an_admitted_request_waiting_for_host(participant):
    incoming = event("first")
    request = (await requests(participant, incoming))[0]
    assert await requests(participant, event("new-input")) == []
    await participant.apply_participation(incoming, request)
    assert incoming.is_stopped()
    scope = incoming.get_extra("ovenheart_decision")["scope"]
    assert (await participant.store.call("state", scope))["decisions"][0][
        "outcome"
    ] == "cancelled"


async def test_new_input_suppresses_a_generated_result_before_send(participant):
    incoming = event("generated")
    await requests(participant, incoming)
    incoming.set_result(incoming.plain_result("已经生成但还没发送的旧回复"))
    await requests(participant, event("new-topic"))
    await participant.guard_participation_result(incoming)
    assert incoming.is_stopped()
    # Stopping the event alone leaves the chain for the responder, and the process
    # stage yields more than once, so the same stale chain was delivered twice.
    assert incoming.get_result() is None
    # Re-entering the guard on the already discarded result must stay suppressed.
    await participant.guard_participation_result(incoming)
    assert incoming.is_stopped()
    assert incoming.get_result() is None


async def test_an_own_send_stage_does_not_cancel_its_other_segments(participant):
    incoming = event("segmented")
    await requests(participant, incoming)
    incoming.set_result(incoming.plain_result("第一段回复"))
    await participant.record_delivery(incoming)
    await participant.guard_participation_result(incoming)
    assert not incoming.is_stopped()


async def test_persistent_admission_reservation_cannot_race_past_quota(participant):
    scope, persona, label = await participant.resolve_scope(event())
    await participant.store.call(
        "touch_scope", scope, "qq:GroupMessage:test", persona, label
    )
    plan = json.loads(decision().completion_text)
    results = await asyncio.gather(
        *(
            participant.store.call(
                "decide", scope, plan, "auto", "admitted", participant.settings
            )
            for _ in range(2)
        )
    )
    assert sorted(r["outcome"] for r in results) == ["admitted", "cancelled"]
    await participant.store.call("initialize")
    assert await participant.store.call(
        "participation_gate", scope, participant.settings
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"action": "send_message"},
        {"confidence": True},
        {"confidence": float("nan")},
        {"reason": ""},
        {"intent": ""},
        {"wait_seconds": True},
        {"wait_seconds": 4},
    ],
)
def test_malformed_plans_do_not_admit_actions(patch):
    with pytest.raises(ValueError):
        parse_plan(decision(**patch).completion_text, DEFAULTS)


def test_low_confidence_and_repeated_wait_become_observation():
    assert (
        parse_plan(decision(confidence=0.6).completion_text, DEFAULTS)["action"]
        == "observe"
    )
    assert (
        parse_plan(decision("wait").completion_text, DEFAULTS, waited=True)["action"]
        == "observe"
    )


async def test_debug_planner_and_participation_injections_match_native_request(
    participant, monkeypatch
):
    from astrbot_plugin_ovenheart.main import logger

    records = []
    monkeypatch.setattr(logger, "info", lambda *args: records.append(args))
    participant.settings["debug_enabled"] = True
    incoming = event(text="我需要一点安慰")
    request = (await requests(participant, incoming))[0]
    before = request.system_prompt
    await participant.apply_participation(incoming, request)
    entries = [
        (args[1], json.loads(args[2]))
        for args in records
        if args[0].startswith("OvenHeart debug")
    ]
    injection = next(
        body for stage, body in entries if stage == "participation_injection"
    )
    assert request.system_prompt == before + injection["system"]
    assert injection["temporary_user"] == request.extra_user_content_parts[-1].text
    assert {stage for stage, _ in entries} >= {
        "planner_request",
        "planner_response",
        "participation_plan",
    }


async def test_cached_participation_keeps_goal_out_of_system_prefix(participant):
    participant.settings["guidance_delivery"] = "cached"
    incoming = event(text="请安慰一下我")
    request = (await requests(participant, incoming))[0]
    request.system_prompt = "稳定宿主系统提示词"
    await participant.apply_participation(incoming, request)
    assert request.system_prompt == "稳定宿主系统提示词"
    assert "OvenHeart participation" in request.extra_user_content_parts[-1].text
    assert "reply_goal" in request.extra_user_content_parts[-1].text
    assert request.extra_user_content_parts[-1].model_dump_for_context()["_no_save"]
