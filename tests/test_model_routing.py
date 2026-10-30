"""Internal task choices never replace AstrBot's final reply provider."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

PARENT = Path(__file__).resolve().parents[2]
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from astrbot.core.astr_main_agent import _select_provider  # noqa: E402
from astrbot.core.provider.entities import LLMResponse  # noqa: E402

from astrbot_plugin_ovenheart.core.auxiliary import AuxiliaryUsage  # noqa: E402
from astrbot_plugin_ovenheart.core.chat.planner import PlannerSession  # noqa: E402
from astrbot_plugin_ovenheart.core.integrations.jev import host_settings  # noqa: E402
from astrbot_plugin_ovenheart.core.settings import DEFAULTS  # noqa: E402
from astrbot_plugin_ovenheart.core.store import Store  # noqa: E402
from astrbot_plugin_ovenheart.main import PersonaPlugin  # noqa: E402


class Provider:
    def __init__(self, identifier):
        self.provider_config = {"id": identifier}
        self.requests = []

    def meta(self):
        return SimpleNamespace(id=self.provider_config["id"])

    async def text_chat(self, **kwargs):
        self.requests.append(kwargs)
        return LLMResponse(role="assistant", completion_text="Native reply")


class Context:
    def __init__(self):
        self.host = Provider("astrbot-chat")
        self.providers = [self.host, Provider("planner-only"), Provider("review-only")]
        self.persona_manager = SimpleNamespace(
            get_persona_v3_by_id=lambda _: {"prompt": "Persona"}
        )

    def get_config(self, _):
        return {"provider_settings": {"datetime_system_prompt": False}}

    def get_all_providers(self):
        return self.providers

    async def get_using_provider_async(self, **_):
        return self.host

    async def get_current_chat_provider_id(self, _):
        return self.host.meta().id


@pytest.mark.parametrize(
    "planner,expected", [("planner-only", "planner-only"), ("", "astrbot-chat")]
)
async def test_actual_planner_uses_its_role_while_native_reply_keeps_host(
    planner, expected
):
    context = Context()
    usage = AuxiliaryUsage(context, None)
    # Only replace model transport and memory I/O; run real planning preparation.
    usage.tool_loop = AsyncMock(
        return_value=LLMResponse(role="assistant", completion_text="done")
    )
    plugin = SimpleNamespace(
        context=context,
        auxiliary=usage,
        emoji_actions=SimpleNamespace(proactive=AsyncMock(return_value={})),
    )
    extra = {}
    event = SimpleNamespace(
        unified_msg_origin="qq:GroupMessage:1",
        is_private_chat=lambda: False,
        get_extra=lambda _: None,
        set_extra=extra.__setitem__,
    )
    session = PlannerSession.__new__(PlannerSession)
    session.plugin = plugin
    session.settings = DEFAULTS | {
        "planner_provider_id": planner,
        "provider_id": "learning-only",
    }
    session.running_tools = 0
    session.tool_changed = asyncio.Event()
    session.actions = asyncio.Queue()
    session.persona = "persona"
    session.event = event
    session.messages = []
    session.expressions = {}
    session.direct = False
    recall_started, emoji_started = asyncio.Event(), asyncio.Event()

    async def recall(_):
        recall_started.set()
        await emoji_started.wait()
        return {"memory_context": ["complete evidence"]}

    async def proactive(_):
        emoji_started.set()
        await recall_started.wait()
        return {"candidates": [{"index": 1, "description": "real candidate"}]}

    session.recall = recall
    plugin.emoji_actions.proactive = proactive
    session.check_current = AsyncMock()

    await asyncio.wait_for(session.run({}), timeout=1)
    assert usage.tool_loop.call_args.kwargs["chat_provider_id"] == expected
    payload = json.loads(usage.tool_loop.call_args.kwargs["prompt"])
    assert payload["memory_context"] == ["complete evidence"]
    assert payload["emoji_policy"]["candidates"][0]["index"] == 1
    assert set(extra["ovenheart_planner_startup"]) == {"recall", "emoji"}
    assert context.host.provider_config == {"id": "astrbot-chat"}

    # Select the body provider through AstrBot's real native selection path.
    native = await _select_provider(event, context)
    assert native is context.host
    original = native.text_chat
    wrapped = usage.native_provider(native)
    await wrapped.text_chat(
        prompt="Body", model="astrbot-session-model", temperature=0.7
    )
    assert native.text_chat == original
    assert native.requests == [
        {"prompt": "Body", "model": "astrbot-session-model", "temperature": 0.7}
    ]
    assert usage.pending[-1]["stage"] == "native_reply"
    assert usage.pending[-1]["provider"] == "astrbot-chat"


async def test_auxiliary_roles_do_not_inherit_other_task_overrides():
    usage = AuxiliaryUsage(Context(), None)
    settings = DEFAULTS | {
        "provider_id": "learning-only",
        "planner_provider_id": "planner-only",
        "memory_summary_provider_id": "memory-only",
    }
    for key in (
        "learning_review_provider_id",
        "selector_provider_id",
        "memory_review_provider_id",
        "person_memory_provider_id",
        "reply_split_provider_id",
    ):
        assert (
            await usage.resolve_provider_id(settings, key, "qq:GroupMessage:1")
            == "astrbot-chat"
        )
    assert (
        await usage.resolve_provider_id(
            settings | {"selector_provider_id": "selector-only"},
            "selector_provider_id",
            "qq:GroupMessage:1",
        )
        == "selector-only"
    )


async def test_model_settings_validate_new_choices_and_preserve_host_and_saved_ids(
    tmp_path,
):
    store = Store(tmp_path / "workshop.sqlite3")
    await store.call("initialize")
    try:
        plugin = PersonaPlugin.__new__(PersonaPlugin)
        plugin.ready = True
        plugin.closing = False
        plugin.settings_lock = asyncio.Lock()
        plugin.settings = DEFAULTS | {"provider_id": "previously-saved-model"}
        plugin.config = dict(plugin.settings)
        plugin.host_settings = host_settings(plugin.config)
        plugin.settings_revision = "first"
        plugin.context = Context()
        plugin.store = store
        plugin.memory_consolidation = SimpleNamespace(busy={})
        plugin.learning_context = {}
        plugin.direct_pending = {}
        plugin.participation = SimpleNamespace(close=lambda: None)
        plugin.debug = lambda *_, **__: None

        with pytest.raises(ValueError, match="已启用的聊天模型"):
            await plugin.update_settings({"selector_provider_id": "missing"}, "first")
        assert plugin.settings["selector_provider_id"] == ""
        await plugin.update_settings(
            {
                "planner_provider_id": "planner-only",
                "learning_review_provider_id": "review-only",
            },
            "first",
        )
        assert plugin.config["planner_provider_id"] == "planner-only"
        assert plugin.config["learning_review_provider_id"] == "review-only"
        assert plugin.config["provider_id"] == "previously-saved-model"
        assert plugin.context.host.meta().id == "astrbot-chat"
        assert (await store.call("settings"))[
            "learning_review_provider_id"
        ] == "review-only"
    finally:
        await store.close()
