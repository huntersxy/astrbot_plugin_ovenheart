"""Integration tests against the installed AstrBot SDK and native request bridge.

Install the local host checkout into the test environment before running these
tests. Provider completions and platform permissions are controlled test doubles;
events, decorators, provider requests and Web request contexts are real SDK types.
"""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("astrbot.api")

from astrbot.api.event import AstrMessageEvent  # noqa: E402
from astrbot.api.platform import (  # noqa: E402
    AstrBotMessage,
    Group,
    MessageMember,
    MessageType,
    PlatformMetadata,
)
from astrbot.api.provider import ProviderRequest  # noqa: E402
from astrbot.api.star import StarTools  # noqa: E402
from astrbot.api.web import PluginRequest, bind_request_context  # noqa: E402
from astrbot.core.message.components import Plain, Reply  # noqa: E402
from starlette.requests import Request  # noqa: E402

from astrbot_plugin_ovenheart.main import (  # noqa: E402
    ObservationFilter,
    PersonaPlugin,
    SessionPluginManager,
    SessionServiceManager,
)

POLICY_PATH = (
    Path(__file__).resolve().parents[2]
    / "astrbot_plugin_livingmemory/core/memory_scope.py"
)
PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def event(
    message_id="1",
    text="好家伙，你又看出来了",
    sender="alice",
    session="123",
    sender_name="测试用户",
):
    """Construct a real SDK group event without a running bot adapter.

    Args:
        message_id: Platform message identifier.
        text: Incoming plain text.
        sender: Platform user identity.
        session: Group session identity.
        sender_name: Display name used as the reply target label.

    Returns:
        An AstrBot message event.
    """
    message = AstrBotMessage()
    message.type = MessageType.GROUP_MESSAGE
    message.self_id = "bot"
    message.sender = MessageMember(sender, sender_name)
    message.group = Group(session, "周末闲聊")
    message.message_id = message_id
    message.message = []
    message.message_str = text
    return AstrMessageEvent(
        text, message, PlatformMetadata("aiocqhttp", "test", "qq"), session
    )


async def completion(**kwargs):
    """Return deterministic, evidence-linked candidates for all four kinds.

    Args:
        **kwargs: Real plugin request parameters.

    Returns:
        A completion-shaped object with valid structured output.
    """
    payload = json.loads(kwargs["prompt"])
    ids = [m["id"] for m in payload["observations"][:2]]
    return SimpleNamespace(
        completion_text=json.dumps(
            {
                "traits": [
                    {
                        "kind": kind,
                        "situation": "轻松聊天时",
                        "content": content,
                        "confidence": 0.95,
                        "evidence_ids": ids,
                    }
                    for kind, content in (
                        ("personality", "亲切并保持边界"),
                        ("expression", "用简短反问接住玩笑"),
                        ("jargon", "好家伙：轻松的惊讶"),
                        ("behavior", "先回应对方的情绪"),
                    )
                    if kind != "jargon"
                    or any("好家伙" in m["text"] for m in payload["observations"])
                ]
            }
        )
    )


@pytest.fixture
async def plugin(monkeypatch, tmp_path):
    monkeypatch.setattr(StarTools, "get_data_dir", lambda name: tmp_path)
    monkeypatch.setattr(
        SessionServiceManager, "is_session_enabled", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        SessionPluginManager,
        "is_plugin_enabled_for_session",
        AsyncMock(return_value=True),
    )
    context = SimpleNamespace(
        get_config=lambda umo: {
            "plugin_set": ["*"],
            "platform_settings": {},
            "wake_prefix": [],
        },
        get_registered_star=lambda name: None,
        conversation_manager=SimpleNamespace(
            get_curr_conversation_id=AsyncMock(return_value=None)
        ),
        persona_manager=SimpleNamespace(
            get_persona_v3_by_id=lambda name: {"prompt": "宿主人格锚点"},
            resolve_selected_persona=AsyncMock(
                return_value=("default", {"prompt": "宿主人格锚点"}, None, False)
            ),
        ),
        get_current_chat_provider_id=AsyncMock(return_value="test-provider"),
        llm_generate=AsyncMock(side_effect=completion),
        registered_web_apis=[],
    )
    context.register_web_api = lambda *args: context.registered_web_apis.append(args)
    instance = PersonaPlugin(
        context,
        {
            "selection_mode": "hybrid",
            "learning_review_enabled": False,
            "session_isolation": True,
            "guidance_delivery": "system",
        },
    )
    await instance.initialize()
    yield instance
    await instance.terminate()


async def sources(plugin):
    """Capture two source events through the real plugin entry point.

    Args:
        plugin: Initialized plugin instance.

    Returns:
        Scope key for the captured session.
    """
    await plugin.capture(event("1"))
    await plugin.capture(event("2", "好家伙，还是你懂我"))
    return (await plugin.store.call("state"))["scopes"][0]["id"]


async def api_call(handler, body=None, query=""):
    """Invoke a plugin API through the host's real request context.

    Args:
        handler: Registered API handler.
        body: Optional JSON request body.
        query: URL query text.

    Returns:
        Handler response.
    """
    raw = json.dumps(body).encode() if body is not None else b""

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST" if body is not None else "GET",
            "path": "/test",
            "headers": [],
            "query_string": query.encode(),
        },
        receive,
    )
    with bind_request_context(
        PluginRequest(
            request, plugin_name="astrbot_plugin_ovenheart", username="test-admin"
        )
    ):
        return await handler()


async def test_passive_capture_does_not_wake_or_learn_bot_and_commands(plugin):
    incoming = event()
    assert (
        ObservationFilter(False).filter(incoming, plugin.context.get_config(""))
        is False
    )
    await asyncio.gather(*(t for t in plugin.tasks if t is not plugin.worker))
    assert incoming.is_wake is False
    await plugin.capture(incoming)
    await plugin.capture(event("bot", sender="bot"))
    await plugin.capture(event("command", text="/persona hello"))
    state = await plugin.store.call("state")
    assert state["scopes"][0]["pending"] == 1


async def test_full_learning_review_and_request_application(plugin):
    scope = await sources(plugin)
    result = await plugin.learn(scope)
    assert result["status"] == "success" and result["count"] == 4
    assert not json.loads(plugin.context.llm_generate.await_args.kwargs["prompt"])[
        "language_features"
    ]
    assert not json.loads(
        plugin.context.llm_generate.await_args_list[0].kwargs["prompt"]
    )["language_features"]
    state = await plugin.store.call("state", scope)
    assert all(t["status"] == "pending" for t in state["traits"])
    req = ProviderRequest(prompt="好家伙", system_prompt="原有系统人格")
    await plugin.apply_persona(event("3"), req)
    assert req.system_prompt == "原有系统人格"
    trait = next(t for t in state["traits"] if t["kind"] == "expression")
    result = await api_call(
        plugin.page_api.action,
        {
            "action": "trait",
            "scope": scope,
            "payload": {"id": trait["id"], "status": "active"},
        },
    )
    assert result["status"] == "ok"
    await plugin.apply_persona(event("4"), req)
    assert (
        req.system_prompt.startswith("原有系统人格") and "简短反问" in req.system_prompt
    )
    assert "先回应对方" not in req.system_prompt
    preview = await api_call(
        plugin.page_api.preview, {"scope": scope, "query": "好家伙"}
    )
    assert "简短反问" in preview["data"]["guidance"]
    assert plugin.context.llm_generate.await_count == 2


async def test_sessions_and_selected_personas_have_separate_learning(plugin):
    first = await sources(plugin)
    await plugin.capture(event("other", session="456"))
    plugin.context.persona_manager.resolve_selected_persona.return_value = (
        "another",
        {"prompt": "另一个人格"},
        None,
        False,
    )
    await plugin.capture(event("new-persona"))
    state = await plugin.store.call("state")
    assert len(state["scopes"]) == 3
    assert (await plugin.store.call("state", first))["scope"]["pending"] == 2


async def test_target_learning_uses_neighbors_without_persisting_or_learning_them(
    plugin,
):
    plugin.settings["target_senders"] = ["alice"]
    await plugin.capture(event("peer-1", "PEER_ONLY：现在只想吐槽，先别建议", "bob"))
    assert not (await plugin.store.call("state"))["scopes"]
    await plugin.capture(event("a-1", "收到，我先把耳朵借给你，慢慢说", "alice"))
    await plugin.capture(event("peer-2", "PEER_ONLY：现在请给我两个实际步骤", "bob"))
    await plugin.capture(
        event("a-2", "收到，那就落到两步，先写问题再选小动作", "alice")
    )
    await plugin.capture(
        event("foreign", "FOREIGN_SCOPE_CONTEXT", "bob", session="456")
    )
    scope = (await plugin.store.call("state"))["scopes"][0]["id"]
    assert len(await plugin.store.call("batch", scope, 100)) == 2
    result = await plugin.learn(scope)
    assert result["status"] == "success"
    payload = json.loads(plugin.context.llm_generate.await_args.kwargs["prompt"])
    assert {m["sender"] for m in payload["observations"]} == {"alice"}
    context = payload["conversation_context"]
    assert len(context) == 2 and {m["sender"] for m in context} == {"bob"}
    assert "FOREIGN_SCOPE_CONTEXT" not in json.dumps(context)
    assert all("id" not in m for m in context)
    ids = {m["id"] for m in payload["observations"]}
    assert all(set(m["for_evidence_ids"]) <= ids for m in context)
    state = await plugin.store.call("state", scope)
    assert {e["sender"] for t in state["traits"] for e in t["evidence"]} == {"alice"}
    assert "PEER_ONLY" not in json.dumps(state)
    cleared = await api_call(
        plugin.page_api.action, {"action": "clear_messages", "scope": scope}
    )
    assert cleared["status"] == "ok" and scope not in plugin.learning_context


@pytest.mark.parametrize("chain_only", [False, True])
async def test_learning_and_review_receive_linked_quote_context_without_new_evidence(
    plugin, chain_only
):
    plugin.settings.update(
        learning_review_enabled=True,
        target_senders=["alice"] if chain_only else [],
    )

    async def generate(**kwargs):
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

    plugin.context.llm_generate.side_effect = generate
    for index in range(2):
        incoming = event(f"quoted-answer-{index}", text="我先听你说，慢慢来。")
        incoming.message_obj.message = [
            Reply(
                id=f"outside-{index}",
                sender_id="bob",
                message_str=""
                if chain_only
                else "QUOTED_ONLY：我只想倾诉，先不用建议。",
                chain=[Plain("QUOTED_ONLY：我只想倾诉，先不用建议。")]
                if chain_only
                else [],
            )
        ]
        await plugin.capture(incoming)
    scope, _, _ = await plugin.resolve_scope(incoming)
    batch = await plugin.store.call("batch", scope, 100)
    assert len(batch) == 2 and all("QUOTED_ONLY" not in m["text"] for m in batch)
    assert (await plugin.learn(scope))["status"] == "success"
    calls = [
        json.loads(call.kwargs["prompt"])
        for call in plugin.context.llm_generate.await_args_list
    ]
    assert any("candidates" in payload for payload in calls)
    for payload in calls:
        assert {m["sender"] for m in payload["observations"]} == {"alice"}
        links = [
            c
            for c in payload["conversation_context"]
            if c.get("relation") == "reply_to"
        ]
        assert len(links) == 2
        assert all(c["sender"] == "bob" and "QUOTED_ONLY" in c["text"] for c in links)
        assert all("id" not in c and len(c["for_evidence_ids"]) == 1 for c in links)
        assert {c["for_evidence_ids"][0] for c in links} == {m["id"] for m in batch}
    state = await plugin.store.call("state", scope)
    assert "QUOTED_ONLY" not in json.dumps(state)
    assert {e["id"] for t in state["traits"] for e in t["evidence"]} <= {
        m["id"] for m in batch
    }


@pytest.mark.parametrize("condition", ["blocked", "unknown_author", "foreign_scope"])
async def test_learning_quote_context_respects_author_and_scope(plugin, condition):
    plugin.settings["blocked_senders"] = (
        ["bob"] if condition == "blocked" else ["mallory"]
    )
    for index in range(2):
        incoming = event(
            f"quote-{index}",
            text="我先听你说。",
            session="456" if condition == "foreign_scope" else "123",
        )
        incoming.message_obj.message = [
            Reply(
                id=f"outside-{index}",
                sender_id="" if condition == "unknown_author" else "bob",
                message_str="EXCLUDED_QUOTE_CONTEXT",
            )
        ]
        await plugin.capture(incoming)
    if condition == "foreign_scope":
        scope = await sources(plugin)
    else:
        scope, _, _ = await plugin.resolve_scope(incoming)
    assert (await plugin.learn(scope))["status"] == "success"
    for call in plugin.context.llm_generate.await_args_list:
        payload = json.loads(call.kwargs["prompt"])
        assert "EXCLUDED_QUOTE_CONTEXT" not in json.dumps(payload)


async def test_changed_targets_filter_existing_pending_material_and_panel_counts(
    plugin,
):
    scope = await sources(plugin)
    await plugin.capture(event("bob-1", "好家伙，今天你又猜到了", "bob"))
    await plugin.capture(event("bob-2", "好家伙，还是你眼尖呀", "bob"))
    result = await api_call(
        plugin.page_api.action,
        {"action": "settings", "payload": {"target_senders": ["bob"]}},
    )
    assert result["status"] == "ok"
    panel = await api_call(plugin.page_api.state, query=f"scope={scope}")
    assert panel["data"]["scope"]["pending"] == 2
    result = await plugin.learn(scope)
    assert result["status"] == "success"
    payload = json.loads(plugin.context.llm_generate.await_args.kwargs["prompt"])
    assert {m["sender"] for m in payload["observations"]} == {"bob"}
    # Former targets remain pending; changing targets never consumes their rows.
    assert (await plugin.store.call("state", scope))["scope"]["pending"] == 2
    panel = await api_call(plugin.page_api.state, query=f"scope={scope}")
    assert panel["data"]["scope"]["pending"] == 0


async def test_target_change_during_generation_keeps_old_batch_unconsumed(plugin):
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
        {"action": "settings", "payload": {"target_senders": ["bob"]}},
    )
    release.set()
    result = await task
    assert result["status"] == "error" and "学习对象" in result["message"]
    state = await plugin.store.call("state", scope)
    assert not state["traits"] and state["scope"]["pending"] == 2


async def test_context_is_bounded_expiring_and_respects_scope_pause(plugin):
    plugin.settings["target_senders"] = ["alice"]
    await plugin.capture(event("old-peer", "这段旁人语境已经过期", "bob"))
    scope = await sources(plugin)
    for item in plugin.learning_context[scope]:
        item["created"] -= 301 if item["sender"] == "bob" else 2
    await plugin.learn(scope)
    payload = json.loads(plugin.context.llm_generate.await_args.kwargs["prompt"])
    assert not payload["conversation_context"]
    for i in range(70):
        await plugin.capture(event(f"peer-{i}", f"旁人上下文{i}", "bob"))
    assert len(plugin.learning_context[scope]) == 64
    await api_call(
        plugin.page_api.action,
        {"action": "profile", "scope": scope, "payload": {"learning_enabled": False}},
    )
    await plugin.capture(event("paused-peer", "这句话不能进入暂停的缓存", "bob"))
    assert scope not in plugin.learning_context


async def test_language_features_use_only_eligible_sender_evidence(plugin):
    plugin.settings["target_senders"] = ["alice"]
    await plugin.capture(event("peer-question", "旁人长问题？" * 40, "bob"))
    texts = [
        "好，我听着。",
        "那就先缓一缓。",
        "收到。\n我们慢慢来。",
        "1. 先喝水\n2. 再休息",
    ]
    for index, text in enumerate(texts):
        await plugin.capture(event(f"rhythm-{index}", text, "alice"))
    scope = (await plugin.store.call("state"))["scopes"][0]["id"]
    assert (await plugin.learn(scope))["status"] == "success"
    payload = json.loads(
        plugin.context.llm_generate.await_args_list[0].kwargs["prompt"]
    )
    assert len(payload["language_features"]) == 1
    features = payload["language_features"][0]
    assert features["sender"] == "alice" and features["messages"] == 4
    assert features["median_chars"] < 20 and features["max_chars"] < 30
    assert features["multiline_messages"] == 2 and features["numbered_messages"] == 1
    assert features["question_messages"] == 0
    assert set(features["evidence_ids"]) == {m["id"] for m in payload["observations"]}


async def test_language_features_keep_speakers_separate(plugin):
    for index in range(4):
        await plugin.capture(event(f"short-{index}", "好呀，我在听。", "alice"))
        await plugin.capture(event(f"long-{index}", "请解释这个复杂问题？" * 12, "bob"))
    scope = (await plugin.store.call("state"))["scopes"][0]["id"]
    assert (await plugin.learn(scope))["status"] == "success"
    payload = json.loads(
        plugin.context.llm_generate.await_args_list[0].kwargs["prompt"]
    )
    features = {f["sender"]: f for f in payload["language_features"]}
    assert features["alice"]["median_chars"] < 20
    assert features["bob"]["median_chars"] > 100
    assert features["alice"]["question_messages"] == 0
    assert features["bob"]["question_messages"] == 4
    assert not set(features["alice"]["evidence_ids"]) & set(
        features["bob"]["evidence_ids"]
    )


async def test_word_pass_is_independent_and_failure_rolls_back_whole_batch(plugin):
    scope = await sources(plugin)

    async def fail_word(**kwargs):
        payload = json.loads(kwargs["prompt"])
        if payload["enabled_kinds"] == ["jargon"]:
            assert not payload["language_features"]
            raise ValueError("synthetic word phase failure")
        assert "jargon" not in payload["enabled_kinds"]
        return await completion(**kwargs)

    plugin.context.llm_generate.side_effect = fail_word
    outcome = await plugin.learn(scope)
    assert outcome["status"] == "error"
    state = await plugin.store.call("state", scope)
    assert not state["traits"] and state["scope"]["pending"] == 2
    plugin.context.llm_generate.side_effect = completion
    assert (await plugin.learn(scope))["count"] == 4
    state = await plugin.store.call("state", scope)
    assert all(t["support_count"] == 1 for t in state["traits"])


@pytest.mark.parametrize("word_only", [True, False])
async def test_disabled_category_does_not_add_a_learning_request(plugin, word_only):
    scope = await sources(plugin)
    plugin.settings.update(
        {
            "learn_personality": not word_only,
            "learn_expression": not word_only,
            "learn_behavior": not word_only,
            "learn_jargon": word_only,
        }
    )
    outcome = await plugin.learn(scope)
    assert outcome["status"] == "success"
    assert plugin.context.llm_generate.await_count == 1
    assert outcome["count"] == (1 if word_only else 3)


async def test_expired_learning_budget_never_starts_a_provider_call(plugin):
    with pytest.raises(asyncio.TimeoutError):
        await plugin._learn_pass({"task": "synthetic"}, [], {}, [], "test-provider", 0)
    plugin.context.llm_generate.assert_not_awaited()


async def test_provider_errors_do_not_leak_details_or_consume_material(plugin):
    scope = await sources(plugin)
    plugin.context.llm_generate.side_effect = ValueError(
        "api_key=SECRET provider internal URL"
    )
    result = await plugin.learn(scope)
    state = await plugin.store.call("state", scope)
    assert result["status"] == "error" and "SECRET" not in json.dumps(state)
    assert state["scope"]["pending"] == 2 and not state["traits"]
    plugin.context.llm_generate.side_effect = None
    plugin.context.llm_generate.return_value = SimpleNamespace(
        completion_text='{"traits":[{"kind":"expression","situation":"test","content":"bad evidence","confidence":1,"evidence_ids":[999,1000]}]}'
    )
    result = await plugin.learn(scope)
    assert result["status"] == "error" and "证据" in result["message"]
    assert (await plugin.store.call("state", scope))["scope"]["pending"] == 2


async def test_ungrounded_word_aborts_the_entire_batch_without_format_repair(plugin):
    scope = await sources(plugin)

    async def generated(**kwargs):
        payload = json.loads(kwargs["prompt"])
        if payload["enabled_kinds"] == ["jargon"]:
            return SimpleNamespace(
                completion_text=json.dumps(
                    {
                        "traits": [
                            {
                                "kind": "jargon",
                                "situation": "聊天",
                                "content": "月港：改期",
                                "confidence": 0.99,
                                "evidence_ids": [
                                    m["id"] for m in payload["observations"]
                                ],
                            }
                        ]
                    }
                )
            )
        return await completion(**kwargs)

    plugin.context.llm_generate = AsyncMock(side_effect=generated)
    outcome = await plugin.learn(scope)
    assert outcome["status"] == "error" and "词汇名称" in outcome["message"]
    assert plugin.context.llm_generate.await_count == 2
    state = await plugin.store.call("state", scope)
    assert not state["traits"] and state["scope"]["pending"] == 2
    plugin.context.llm_generate = AsyncMock(side_effect=completion)
    assert (await plugin.learn(scope))["status"] == "success"
    assert (await plugin.store.call("state", scope))["scope"]["pending"] == 0


async def test_schema_drift_gets_one_correction_with_original_evidence(plugin):
    scope = await sources(plugin)

    async def generated(**kwargs):
        payload = json.loads(kwargs["prompt"])
        if payload["enabled_kinds"] != ["jargon"] and "format_error" not in payload:
            return SimpleNamespace(
                completion_text='说明：```json\n{"profile_updates": []}\n```'
            )
        return await completion(**kwargs)

    plugin.context.llm_generate.side_effect = generated
    result = await plugin.learn(scope)
    assert result["status"] == "success" and result["count"] == 4
    assert plugin.context.llm_generate.await_count == 3
    first, second = [
        json.loads(call.kwargs["prompt"])
        for call in plugin.context.llm_generate.await_args_list
        if json.loads(call.kwargs["prompt"])["enabled_kinds"] != ["jargon"]
    ]
    assert first["task"] and second["observations"] == first["observations"]
    assert "profile_updates" in second["format_error"]
    phase = [
        call.kwargs
        for call in plugin.context.llm_generate.await_args_list
        if json.loads(call.kwargs["prompt"])["enabled_kinds"] != ["jargon"]
    ]
    assert first["task"] == second["task"] and second["correction_task"]
    assert phase[0]["system_prompt"] == phase[1]["system_prompt"]
    assert phase[1]["prompt"].startswith(phase[0]["prompt"][:-1] + ",")


async def test_repeated_bad_format_is_bounded_and_preserves_material(plugin):
    scope = await sources(plugin)
    plugin.context.llm_generate.side_effect = None
    plugin.context.llm_generate.return_value = SimpleNamespace(
        completion_text="无法提供结果"
    )
    result = await plugin.learn(scope)
    assert result["status"] == "error" and "自动纠正" in result["message"]
    requests = [
        json.loads(c.kwargs["prompt"])
        for c in plugin.context.llm_generate.await_args_list
    ]
    assert 2 <= len(requests) <= 4
    for word_pass in (False, True):
        phase = [r for r in requests if (r["enabled_kinds"] == ["jargon"]) == word_pass]
        assert len(phase) <= 2
        assert sum("format_error" in r for r in phase) <= 1
    assert (await plugin.store.call("state", scope))["scope"]["pending"] == 2


async def test_fabricated_evidence_never_triggers_format_repair(plugin):
    scope = await sources(plugin)
    plugin.context.llm_generate.side_effect = None
    plugin.context.llm_generate.return_value = SimpleNamespace(
        completion_text='{"traits":[{"kind":"expression","situation":"日常","content":"简短","confidence":0.9,"evidence_ids":[999]}]}'
    )
    result = await plugin.learn(scope)
    assert result["status"] == "error" and "证据" in result["message"]
    requests = [
        json.loads(c.kwargs["prompt"])
        for c in plugin.context.llm_generate.await_args_list
    ]
    assert 1 <= len(requests) <= 2
    assert not any("format_error" in r for r in requests)


async def test_application_is_not_usage_until_host_send_stage(plugin):
    scope = await sources(plugin)
    await plugin.learn(scope)
    trait = next(
        t
        for t in (await plugin.store.call("state", scope))["traits"]
        if t["kind"] == "expression"
    )
    await plugin.store.call(
        "mutate", scope, "trait", {"id": trait["id"], "status": "active"}
    )
    incoming = event("4")
    await plugin.apply_persona(
        incoming, ProviderRequest(prompt="好家伙", system_prompt="")
    )
    state = await plugin.store.call("state", scope)
    assert not state["applications"]
    await plugin.record_delivery(incoming)
    await plugin.record_delivery(incoming)
    state = await plugin.store.call("state", scope)
    assert len(state["applications"]) == 1
    assert next(t for t in state["traits"] if t["id"] == trait["id"])["use_count"] == 1
    result = await api_call(
        plugin.page_api.action,
        {
            "action": "feedback",
            "scope": scope,
            "payload": {"id": trait["id"], "rating": -1},
        },
    )
    assert result["status"] == "ok"
    assert (
        next(
            t
            for t in (await plugin.store.call("state", scope))["traits"]
            if t["id"] == trait["id"]
        )["rating"]
        == -1
    )


async def test_pause_in_flight_rejects_late_output_and_retains_material(plugin):
    scope = await sources(plugin)
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(**kwargs):
        entered.set()
        await release.wait()
        return await completion(**kwargs)

    plugin.context.llm_generate.side_effect = delayed
    task = asyncio.create_task(plugin.learn(scope))
    await entered.wait()
    assert (await plugin.learn(scope))["status"] == "busy"
    plugin.settings["learning_enabled"] = False
    release.set()
    assert (await task)["status"] == "error"
    state = await plugin.store.call("state", scope)
    assert state["scope"]["pending"] == 2 and not state["traits"]


async def test_profile_edit_in_flight_rejects_outdated_learning(plugin):
    """Keep new manual identity anchors authoritative over pending generation.

    Args:
        plugin: Initialized native-SDK test instance.
    """
    scope = await sources(plugin)
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(**kwargs):
        entered.set()
        await release.wait()
        return await completion(**kwargs)

    plugin.context.llm_generate.side_effect = delayed
    task = asyncio.create_task(plugin.learn(scope))
    await entered.wait()
    await plugin.store.call(
        "mutate", scope, "profile", {"identity": "人工修改的新底稿"}
    )
    release.set()
    result = await task
    state = await plugin.store.call("state", scope)
    assert result["status"] == "error" and "底稿" in result["message"]
    assert state["scope"]["profile"]["identity"] == "人工修改的新底稿"
    assert state["scope"]["pending"] == 2 and not state["traits"]


async def test_access_switches_apply_to_passive_capture_and_requests(
    plugin, monkeypatch
):
    incoming = event()
    monkeypatch.setattr(
        SessionServiceManager, "is_session_enabled", AsyncMock(return_value=False)
    )
    await plugin.capture(incoming)
    assert not (await plugin.store.call("state"))["scopes"]
    monkeypatch.setattr(
        SessionServiceManager, "is_session_enabled", AsyncMock(return_value=True)
    )
    cfg = {
        "platform_settings": {"enable_id_white_list": True, "id_whitelist": ["another"]}
    }
    await plugin.capture(incoming, cfg)
    assert not (await plugin.store.call("state"))["scopes"]
    plugin.settings["allowed_sessions"] = ["not-this-session"]
    req = ProviderRequest(system_prompt="original")
    await plugin.apply_persona(incoming, req)
    assert req.system_prompt == "original"


async def test_native_view_discovery_and_localized_title():
    """Verify the host discovers the packaged view and localization format."""
    from astrbot.core.star.star import StarMetadata
    from astrbot.core.star.star_manager import PluginManager
    from astrbot.dashboard.services.plugin_page_service import PluginPageService

    manager = SimpleNamespace(plugin_store_path=str(PLUGIN_ROOT.parent))
    metadata = StarMetadata(
        name="astrbot_plugin_ovenheart", root_dir_name=PLUGIN_ROOT.name
    )
    service = PluginPageService(manager)
    pages = await service.discover_plugin_pages(metadata)
    assert [page.name for page in pages] == ["workshop"]
    i18n = PluginManager._load_plugin_i18n(str(PLUGIN_ROOT))
    assert i18n["zh-CN"]["views"]["workshop"]["title"] == "人格工坊"
    html = service.process_plugin_page_html(
        (PLUGIN_ROOT / "views/workshop/index.html").read_text(encoding="utf-8"),
        theme="light",
    )  # noqa: ASYNC240
    assert "/api/plugin/page/bridge-sdk.js" in html


async def test_background_learning_rechecks_current_host_plugin_set(plugin):
    """Keep retained material inaccessible after its host configuration disables us.

    Args:
        plugin: Initialized native-SDK test instance.
    """
    scope = await sources(plugin)
    plugin.context.get_config = lambda umo: {"plugin_set": ["another-plugin"]}
    result = await plugin.learn(scope)
    assert result["status"] == "error" and "未启用" in result["message"]
    plugin.context.llm_generate.assert_not_awaited()
    assert (await plugin.store.call("state", scope))["scope"]["pending"] == 2


async def test_native_page_validation_and_teardown(plugin):
    assert len(plugin.context.registered_web_apis) == 6
    scope = await sources(plugin)
    assert (await api_call(plugin.page_api.state, query=f"scope={scope}"))[
        "status"
    ] == "ok"
    invalid = await api_call(
        plugin.page_api.action,
        {"action": "settings", "payload": {"min_messages": 100, "batch_size": 4}},
    )
    assert invalid["status"] == "error"
    good = await api_call(
        plugin.page_api.action,
        {"action": "settings", "payload": {"activation_mode": "auto"}},
    )
    assert good["status"] == "ok"
    await plugin.learn(scope)
    assert all(
        t["status"] == "active"
        for t in (await plugin.store.call("state", scope))["traits"]
    )
    await plugin.terminate()
    assert not plugin.tasks and not plugin.context.registered_web_apis


async def test_memory_bridge_uses_livingmemory_policy_and_bounded_context(
    plugin, monkeypatch
):
    policy_path = POLICY_PATH
    if not policy_path.exists():
        pytest.skip("LivingMemory reference checkout not available")
    spec = importlib.util.spec_from_file_location(
        "lm_fixture.core.memory_scope", policy_path
    )
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    monkeypatch.setitem(sys.modules, spec.name, policy)
    scope = await sources(plugin)
    engine = SimpleNamespace(
        search_memories=AsyncMock(
            return_value=[SimpleNamespace(doc_id="m1", content="长期上下文")]
        )
    )
    manager = SimpleNamespace(filtering_settings={"use_persona_filtering": True})
    values = {"filtering_settings.memory_scope_mode": "session"}
    manager.get = lambda key, default=None: values.get(key, default)
    memory_plugin = type("MemoryPlugin", (), {"__module__": "lm_fixture.main"})()
    memory_plugin.initializer = SimpleNamespace(
        is_initialized=True, memory_engine=engine
    )
    memory_plugin.config_manager = manager
    metadata = SimpleNamespace(activated=True, star_cls=memory_plugin)
    plugin.context.get_registered_star = lambda name: metadata
    await plugin.learn(scope)
    call = engine.search_memories.await_args.kwargs
    assert (
        call["session_id"] == event().unified_msg_origin
        and call["persona_id"] == "default"
    )
    assert call["k"] == 3
    assert "好家伙，你又看出来了" in call["query"]
    assert "好家伙，还是你懂我" in call["query"]
    prompt = json.loads(plugin.context.llm_generate.await_args.kwargs["prompt"])
    assert prompt["memory_context"][0]["id"] == "m1"
    quoted_batch = [
        {
            "id": 77,
            "sender": "alice",
            "text": "这个我听懂了",
            "reply_to": {"sender": "bob", "text": "QUOTE_MEMORY_LOOKUP：想先倾诉"},
        }
    ]
    await plugin.memory.recall(
        event(), "default", plugin.settings, observations=quoted_batch
    )
    assert "QUOTE_MEMORY_LOOKUP" in engine.search_memories.await_args.kwargs["query"]
    await plugin.memory.recall(
        event(),
        "default",
        plugin.settings | {"blocked_senders": ["bob"]},
        observations=quoted_batch,
    )
    assert (
        "QUOTE_MEMORY_LOOKUP" not in engine.search_memories.await_args.kwargs["query"]
    )
    for mode, session_filter, isolated, expected in (
        ("legacy", False, "", None),
        ("legacy", True, "", event().unified_msg_origin),
        ("global", True, "", "livingmemory:global"),
        ("user", True, "", "livingmemory:user:aiocqhttp:alice"),
        ("global", False, event().unified_msg_origin, event().unified_msg_origin),
        ("legacy", False, "another-session", "livingmemory:global"),
    ):
        values.update(
            {
                "filtering_settings.memory_scope_mode": mode,
                "filtering_settings.use_session_filtering": session_filter,
                "filtering_settings.isolated_sessions": isolated,
            }
        )
        await plugin.memory.recall(event(), "default", plugin.settings)
        assert engine.search_memories.await_args.kwargs["session_id"] == expected
    calls_before_denial = engine.search_memories.await_count
    values["access_control.whitelist_enabled"] = True
    values["access_control.allowed_ids"] = "nobody"
    memories, message = await plugin.memory.recall(event(), "default", plugin.settings)
    assert memories == [] and "禁止" in message
    assert engine.search_memories.await_count == calls_before_denial
    values.update(
        {
            "access_control.whitelist_enabled": False,
            "filtering_settings.memory_scope_mode": "session",
            "filtering_settings.isolated_sessions": "",
        }
    )
    observations = [
        {
            "id": index,
            "sender": "alice",
            "text": f"BATCH_TOPIC_{index} " + "原始素材" * 40,
        }
        for index in range(10)
    ]
    latest = event("later", text="UNRELATED_LATEST_INPUT", sender="bob")
    memories, _ = await plugin.memory.recall(
        latest, "default", plugin.settings, observations=observations
    )
    query = engine.search_memories.await_args.kwargs["query"]
    assert len(query) <= 500 and "BATCH_TOPIC_0" in query and "BATCH_TOPIC_9" in query
    assert "UNRELATED_LATEST_INPUT" not in query
    assert memories[0]["for_evidence_ids"] == list(range(10))
    values["filtering_settings.memory_scope_mode"] = "user"
    count = engine.search_memories.await_count
    memories, status = await plugin.memory.recall(
        latest, "default", plugin.settings, observations=observations
    )
    assert memories == [] and "不匹配" in status
    assert engine.search_memories.await_count == count
    mixed = [*observations, {"id": 77, "sender": "bob", "text": "BOB_BATCH_TOPIC"}]
    memories, _ = await plugin.memory.recall(
        latest, "default", plugin.settings, observations=mixed
    )
    assert engine.search_memories.await_args.kwargs["query"] == "BOB_BATCH_TOPIC"
    assert memories[0]["for_evidence_ids"] == [77]
    assert latest.get_message_str() == "UNRELATED_LATEST_INPUT"

    plugin.settings.update(batch_size=4, min_messages=4)
    for index in range(4):
        await plugin.capture(
            event(
                f"backlog-{index}",
                text=f"BACKLOG_TOPIC_{index}",
                session="789",
                sender="alice",
            )
        )
    await plugin.capture(
        event(
            "outside-backlog",
            text="OUTSIDE_SELECTED_BATCH",
            session="789",
            sender="bob",
        )
    )
    selected_scope, _, _ = await plugin.resolve_scope(event(session="789"))
    assert (await plugin.learn(selected_scope))["status"] == "success"
    call = engine.search_memories.await_args.kwargs
    assert call["session_id"] == "livingmemory:user:aiocqhttp:alice"
    assert "BACKLOG_TOPIC_0" in call["query"] and "BACKLOG_TOPIC_3" in call["query"]
    assert "OUTSIDE_SELECTED_BATCH" not in call["query"]
    for index in range(4):
        incoming = event(
            f"mutated-source-{index}",
            text="原始素材仍属于 Alice",
            session="999",
            sender="alice",
        )
        await plugin.capture(incoming)
    # Native reply decoration can rebind the event to an older addressee.
    incoming.message_obj.sender = MessageMember("bob", "Bob")
    mutated_scope, _, _ = await plugin.resolve_scope(incoming)
    count = engine.search_memories.await_count
    assert (await plugin.learn(mutated_scope))["status"] == "success"
    assert engine.search_memories.await_count == count


async def test_blacklisted_qq_is_not_captured_or_used_as_neighbor_context(plugin):
    plugin.settings["target_senders"] = ["alice", "123456"]
    await plugin.capture(event("old", "历史待学文字", "123456"))
    scope = (await plugin.store.call("state"))["scopes"][0]["id"]
    await plugin.capture(event("peer", "不应成为学习语境", "123456"))
    result = await api_call(
        plugin.page_api.action,
        {
            "action": "settings",
            "payload": {"blocked_senders": ["123456"], "target_senders": ["alice"]},
        },
    )
    assert result["status"] == "ok" and not plugin.learning_context
    await plugin.capture(event("new", "拉黑之后的文字", "123456"))
    await sources(plugin)
    assert (await api_call(plugin.page_api.state, query=f"scope={scope}"))["data"][
        "scope"
    ]["pending"] == 2
    result = await plugin.learn(scope)
    assert result["status"] == "success"
    for call in plugin.context.llm_generate.await_args_list:
        payload = json.loads(call.kwargs["prompt"])
        assert {m["sender"] for m in payload["observations"]} == {"alice"}
        assert not payload["conversation_context"]
    # Old sources remain excluded rather than being consumed by an unrelated batch.
    await plugin.store.call("settings", {"blocked_senders": []})
    assert len(await plugin.store.call("batch", scope, 100, [], [])) == 2


async def test_blacklist_change_during_generation_prevents_late_learning(plugin):
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
        {"action": "settings", "payload": {"blocked_senders": ["alice"]}},
    )
    release.set()
    result = await task
    assert result["status"] == "error" and "黑名单" in result["message"]
    assert not (await plugin.store.call("library"))["traits"]
    await plugin.store.call("settings", {"blocked_senders": []})
    assert len(await plugin.store.call("batch", scope, 100, [], [])) == 2


async def test_library_native_api_filters_and_rejects_invalid_parameters(plugin):
    scope = await sources(plugin)
    await plugin.learn(scope)
    response = await api_call(plugin.page_api.library)
    assert response["status"] == "ok" and response["data"]["total"] == 4
    assert {t["scope"] for t in response["data"]["traits"]} == {scope}
    assert (await api_call(plugin.page_api.library, query="kind=jargon"))["data"][
        "total"
    ] == 1
    for query in ("offset=-1", "offset=bad", "status=bad", "scope=bad"):
        assert (await api_call(plugin.page_api.library, query=query))[
            "status"
        ] == "error"


async def test_debug_defaults_off_then_logs_exact_injection_without_host_config(
    plugin, monkeypatch
):
    from astrbot_plugin_ovenheart.main import logger

    records = []
    monkeypatch.setattr(logger, "info", lambda *args: records.append(args))
    scope = await sources(plugin)
    result = await plugin.learn(scope)
    assert result["status"] == "success" and records == []
    long_identity = "调试身份内容" * 140
    await plugin.store.call("mutate", scope, "profile", {"identity": long_identity})
    await api_call(
        plugin.page_api.action,
        {
            "action": "settings",
            "payload": {"debug_enabled": True, "guidance_delivery": "dual"},
        },
    )
    plugin.context.get_config = lambda umo: {
        "plugin_set": ["*"],
        "platform_settings": {},
        "api_key": "sensitive-config-must-not-be-logged",
    }
    records.clear()
    request = ProviderRequest(
        prompt="普通对话", system_prompt="宿主自己的提示词不作调试转储"
    )
    await plugin.apply_persona(event("debug-turn"), request)
    entries = [
        (args[1], json.loads(args[2]))
        for args in records
        if args[0].startswith("OvenHeart debug")
    ]
    injection = next(body for stage, body in entries if stage == "persona_injection")
    assert request.system_prompt == "宿主自己的提示词不作调试转储" + injection["system"]
    assert injection["temporary_user"] == request.extra_user_content_parts[-1].text
    assert long_identity in injection["system"] and len(injection["system"]) > 800
    assert any(stage == "retrieval" for stage, _ in entries)
    assert "sensitive-config-must-not-be-logged" not in str(records)
    await api_call(
        plugin.page_api.action,
        {"action": "settings", "payload": {"debug_enabled": False}},
    )
    records.clear()
    await plugin.apply_persona(
        event("debug-disabled"), ProviderRequest(prompt="普通对话")
    )
    assert records == []


async def test_debug_logs_learning_json_and_safe_provider_failure(plugin, monkeypatch):
    from astrbot_plugin_ovenheart.main import logger

    records = []
    monkeypatch.setattr(logger, "info", lambda *args: records.append(args))
    plugin.settings["debug_enabled"] = True
    scope = await sources(plugin)
    result = await plugin.learn(scope)
    assert result["status"] == "success"
    entries = [
        (args[1], json.loads(args[2]))
        for args in records
        if args[0].startswith("OvenHeart debug")
    ]
    assert sum(stage == "learning_request" for stage, _ in entries) == 2
    assert sum(stage == "learning_response" for stage, _ in entries) == 2
    assert any(stage == "learning_committed" for stage, _ in entries)
    for call in plugin.context.llm_generate.await_args_list:
        assert any(
            body.get("prompt") == json.loads(call.kwargs["prompt"])
            for stage, body in entries
            if stage == "learning_request"
        )
    await plugin.capture(event("failure-1", "第二批学习素材一"))
    await plugin.capture(event("failure-2", "第二批学习素材二"))
    plugin.context.llm_generate.side_effect = RuntimeError(
        "Authorization: Bearer secret-must-not-be-logged"
    )
    records.clear()
    assert (await plugin.learn(scope))["status"] == "error"
    assert "secret-must-not-be-logged" not in str(records)
    errors = [json.loads(args[2]) for args in records if args[1] == "learning_failed"]
    assert errors and errors[0]["sources_preserved"] is True
    assert errors[0]["error_type"] == "RuntimeError"
