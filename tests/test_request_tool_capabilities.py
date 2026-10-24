"""Keep avatar support visible in native light schemas without granting tools."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool import FunctionTool, ToolSet

from astrbot_plugin_ovenheart import prompts
from astrbot_plugin_ovenheart.main import narrow_toolset
from tests.test_runtime import event

pytest_plugins = ["tests.test_participation"]


def image_tool(*, active=True, avatar=True):
    return FunctionTool(
        name="generate_image",
        description="生成或修改图片",
        active=active,
        parameters={
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                **(
                    {
                        "avatar_references": {
                            "type": "array",
                            "items": {"type": "string"},
                        }
                    }
                    if avatar
                    else {}
                ),
            },
        },
        handler=AsyncMock(),
    )


async def test_light_schema_describes_qq_avatar_without_mutating_registry(participant):
    incoming = event("avatar", text="你在参数里面填QQ号不行吗")
    incoming.is_at_or_wake_command = True
    native = image_tool()
    registered = ToolSet([native])
    before = deepcopy(native.parameters)
    request = ProviderRequest(prompt=incoming.message_str, func_tool=registered)
    await participant.apply_participation(incoming, request)
    local = request.func_tool.get_tool("generate_image")
    assert local is not native and request.func_tool is not registered
    assert local.handler is native.handler and local.parameters == before
    assert native.description == "生成或修改图片" and registered.tools == [native]
    light = request.func_tool.get_light_tool_set().get_tool("generate_image")
    assert (
        "QQ 号/用户 ID" in light.description
        and "avatar_references" in light.description
    )
    assert light.parameters["properties"] == {}
    assert "仅要求查看或识别头像" in light.description
    assert (
        incoming.get_extra("ovenheart_tool_capabilities")["avatar_references"] is True
    )
    participant.describe_request_tools(incoming, request)
    assert request.func_tool.get_tool("generate_image") is local
    assert local.description.count(prompts.AVATAR_TOOL_HINT) == 1
    native.handler.assert_not_awaited()


@pytest.mark.parametrize("case", ["missing", "inactive", "unsupported", "denied"])
async def test_missing_or_denied_capabilities_are_never_advertised(participant, case):
    native = image_tool(active=case != "inactive", avatar=case != "unsupported")
    registered = ToolSet([] if case == "missing" else [native])
    tools = (
        narrow_toolset(registered, set(), skills_like=True)
        if case == "denied"
        else registered
    )
    request = ProviderRequest(func_tool=tools)
    participant.describe_request_tools(event("limited"), request)
    assert request.func_tool is tools
    assert all(prompts.AVATAR_TOOL_HINT not in t.description for t in tools.tools)


async def test_request_copy_preserves_dynamic_autonomous_permission_gate(participant):
    native = image_tool()
    permitted = True
    tools = narrow_toolset(
        ToolSet([native]),
        {"generate_image"},
        skills_like=True,
        permit=lambda name: permitted,
    )
    request = ProviderRequest(func_tool=tools)
    participant.describe_request_tools(event("permitted"), request)
    assert request.func_tool.get_tool("generate_image")
    permitted = False
    assert request.func_tool.get_tool("generate_image") is None
    assert native.description == "生成或修改图片"


async def test_native_runner_keeps_hint_when_building_skills_like_request(participant):
    native = image_tool()
    request = ProviderRequest(
        prompt="用他的QQ头像做参考图", func_tool=ToolSet([native])
    )
    participant.describe_request_tools(event("runner"), request)
    runner = ToolLoopAgentRunner()
    await runner.reset(
        provider=SimpleNamespace(provider_config={}),
        request=request,
        run_context=SimpleNamespace(),
        tool_executor=SimpleNamespace(),
        agent_hooks=SimpleNamespace(),
        tool_schema_mode="skills_like",
    )
    light = request.func_tool.get_tool("generate_image")
    assert light.parameters["properties"] == {}
    assert prompts.AVATAR_TOOL_HINT in light.description
    raw = runner._skill_like_raw_tool_set.get_tool("generate_image")
    assert raw.handler is native.handler
    assert raw.parameters["properties"]["avatar_references"]["type"] == "array"
