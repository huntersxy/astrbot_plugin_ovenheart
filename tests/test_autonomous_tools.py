"""Verify native tool discovery and both autonomous tool policies."""

import json

import pytest
from astrbot.api.provider import ProviderRequest
from astrbot.core.provider.func_tool_manager import FunctionToolManager

from astrbot_plugin_ovenheart.settings import DEFAULTS, validate_settings
from tests.test_participation import build_tools, requests, skills_like_config
from tests.test_runtime import api_call, event

pytest_plugins = ["tests.test_participation"]


@pytest.mark.parametrize("skills_like", [False, True])
@pytest.mark.parametrize(
    "policy,names,expected",
    [
        ("allowlist", [], []),
        ("allowlist", ["qq_group_info", "not_present"], ["qq_group_info"]),
        ("denylist", ["qq_group_member_manage"], ["qq_group_info"]),
        ("denylist", [], ["qq_group_info", "qq_group_member_manage"]),
        ("denylist", ["qq_group_info", "qq_group_member_manage"], []),
    ],
)
async def test_policies_only_narrow_the_native_request(
    participant, skills_like, policy, names, expected
):
    participant.settings.update(
        autonomous_tool_policy=policy,
        autonomous_tools=names,
        autonomous_tool_denylist=names,
    )
    if skills_like:
        original = participant.context.get_config
        participant.context.get_config = lambda umo: (
            original(umo) | skills_like_config()
        )
    incoming = event("tool-policy")
    request = (await requests(participant, incoming))[0]
    original_tools = build_tools()
    request.func_tool = original_tools
    await participant.apply_participation(incoming, request)
    visible = [tool.name for tool in request.func_tool.tools if tool.active]
    assert visible == expected
    assert len(original_tools.tools) == 2 and all(
        t.active for t in original_tools.tools
    )
    direct = ProviderRequest(func_tool=original_tools)
    await participant.apply_participation(event("direct"), direct)
    assert direct.func_tool is original_tools


@pytest.mark.parametrize("skills_like", [False, True])
async def test_empty_blacklist_never_activates_a_disabled_tool(
    participant, skills_like
):
    participant.settings["autonomous_tool_policy"] = "denylist"
    if skills_like:
        original = participant.context.get_config
        participant.context.get_config = lambda umo: (
            original(umo) | skills_like_config()
        )
    incoming = event("inactive-tool")
    request = (await requests(participant, incoming))[0]
    tools = build_tools()
    tools.tools[1].active = False
    request.func_tool = tools
    await participant.apply_participation(incoming, request)
    assert [t.name for t in request.func_tool.tools if t.active] == ["qq_group_info"]
    assert tools.tools[1].active is False


async def test_native_inventory_includes_plugins_builtins_and_inactive_tools(plugin):
    manager = FunctionToolManager()
    manager.func_list = build_tools().tools
    manager.func_list[1].active = False
    plugin.context.get_llm_tool_manager = lambda: manager
    response = (await api_call(plugin.page_api.state))["data"]["tools"]
    assert response["available"] is True
    tools = {t["name"]: t for t in response["items"]}
    assert tools["qq_group_info"]["active"] is True
    assert tools["qq_group_member_manage"]["active"] is False
    assert len(tools) > 2
    assert all(set(t) == {"name", "description", "active"} for t in tools.values())


async def test_missing_manager_keeps_settings_readable(plugin):
    response = await api_call(plugin.page_api.state)
    assert response["status"] == "ok"
    assert response["data"]["tools"]["available"] is False


async def test_both_lists_persist_in_native_settings_and_survive_mode_switch(plugin):
    for policy in ("allowlist", "denylist", "allowlist"):
        result = await api_call(
            plugin.page_api.action,
            {
                "action": "settings",
                "payload": {
                    "autonomous_tool_policy": policy,
                    "autonomous_tools": ["qq_group_info"],
                    "autonomous_tool_denylist": [
                        "qq_group_member_manage",
                        "missing_tool",
                    ],
                },
            },
        )
        assert result["status"] == "ok"
        assert plugin.config["autonomous_tools"] == ["qq_group_info"]
        assert plugin.config["autonomous_tool_denylist"] == [
            "qq_group_member_manage",
            "missing_tool",
        ]
    assert (
        DEFAULTS["autonomous_tool_policy"] == "allowlist"
        and DEFAULTS["autonomous_tools"] == []
    )
    assert validate_settings({"autonomous_tool_denylist": [" x ", "x"]}) == {
        "autonomous_tool_denylist": ["x"]
    }
    with pytest.raises(ValueError):
        validate_settings({"autonomous_tool_policy": "all"})


def test_schema_defaults_match_the_policy_defaults():
    from pathlib import Path

    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "_conf_schema.json").read_text(
            encoding="utf-8"
        )
    )
    for key in (
        "autonomous_tools",
        "autonomous_tool_policy",
        "autonomous_tool_denylist",
    ):
        assert schema[key]["default"] == DEFAULTS[key]
