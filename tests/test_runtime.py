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
from starlette.requests import Request  # noqa: E402

from astrbot_plugin_persona.main import (  # noqa: E402
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


def event(message_id="1", text="好家伙，你又看出来了", sender="alice", session="123"):
    """Construct a real SDK group event without a running bot adapter.

    Args:
        message_id: Platform message identifier.
        text: Incoming plain text.
        sender: Platform user identity.
        session: Group session identity.

    Returns:
        An AstrBot message event.
    """
    message = AstrBotMessage()
    message.type = MessageType.GROUP_MESSAGE
    message.self_id = "bot"
    message.sender = MessageMember(sender, "测试用户")
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
    instance = PersonaPlugin(context, {})
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
            request, plugin_name="astrbot_plugin_persona", username="test-admin"
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
    plugin.context.llm_generate.assert_awaited_once()


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
        name="astrbot_plugin_persona", root_dir_name=PLUGIN_ROOT.name
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
    assert len(plugin.context.registered_web_apis) == 3
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
    prompt = json.loads(plugin.context.llm_generate.await_args.kwargs["prompt"])
    assert prompt["memory_context"][0]["id"] == "m1"
    values["access_control.whitelist_enabled"] = True
    values["access_control.allowed_ids"] = "nobody"
    memories, message = await plugin.memory.recall(event(), "default", plugin.settings)
    assert memories == [] and "禁止" in message
    assert engine.search_memories.await_count == 1
