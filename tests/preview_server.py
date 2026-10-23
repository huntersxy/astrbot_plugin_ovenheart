"""Local visual acceptance fixture using the real plugin and simulated model.

Run from the plugin checkout with:
    uv run --no-sync python -m uvicorn tests.preview_server:app --host 127.0.0.1 --port 8766

This binds only when explicitly launched and never starts with the plugin.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
os.environ["ASTRBOT_ROOT"] = str(
    Path(tempfile.gettempdir()) / "persona-preview-runtime"
)

from astrbot.api import AstrBotConfig  # noqa: E402
from astrbot.api.star import StarTools  # noqa: E402
from astrbot.api.web import PluginRequest, bind_request_context  # noqa: E402
from astrbot.core.agent.tool import FunctionTool, ToolSet  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, Response  # noqa: E402

from astrbot_plugin_ovenheart.main import (  # noqa: E402
    PersonaPlugin,
    SessionPluginManager,
    SessionServiceManager,
)
from astrbot_plugin_ovenheart.settings import DEFAULTS, SHARED_SCOPE  # noqa: E402


@asynccontextmanager
async def lifespan(app):
    """Seed explicitly illustrative data in a temporary database.

    Args:
        app: FastAPI acceptance fixture.

    Yields:
        Initialized fixture lifetime.
    """
    with tempfile.TemporaryDirectory(prefix="persona-preview-") as directory:
        original = StarTools.get_data_dir
        original_session = SessionServiceManager.is_session_enabled
        original_plugin = SessionPluginManager.is_plugin_enabled_for_session
        StarTools.get_data_dir = lambda name: Path(directory)
        SessionServiceManager.is_session_enabled = AsyncMock(return_value=True)
        SessionPluginManager.is_plugin_enabled_for_session = AsyncMock(
            return_value=True
        )

        async def generate(**kwargs):
            if "tools" in kwargs:
                return SimpleNamespace(
                    completion_text="先别急着否定自己。愿意的话，和我说说今天最让你难受的那件事，我听着。"
                )
            body = json.loads(kwargs["prompt"])
            if "candidates" in body:
                candidates = body["candidates"]
                result = (
                    {
                        "approved_ids": [item["candidate_id"] for item in candidates],
                        "flagged": [],
                    }
                    if candidates and "candidate_id" in candidates[0]
                    else {"selected_ids": [item["id"] for item in candidates[:1]]}
                )
                return SimpleNamespace(completion_text=json.dumps(result))
            return SimpleNamespace(
                completion_text=json.dumps(
                    {
                        "traits": [
                            {
                                "kind": "expression",
                                "situation": "朋友轻松调侃时",
                                "content": "用一句简短反问接住玩笑，保留轻松的语气",
                                "confidence": 0.96,
                                "evidence_ids": [
                                    m["id"] for m in body["observations"][:2]
                                ],
                            }
                        ]
                    }
                )
            )

        context = SimpleNamespace(
            registered_web_apis=[],
            get_registered_star=lambda name: None,
            get_config=lambda umo: {"plugin_set": ["*"], "platform_settings": {}},
            get_current_chat_provider_id=AsyncMock(return_value="fixture"),
            llm_generate=generate,
            get_llm_tool_manager=lambda: SimpleNamespace(
                get_full_tool_set=lambda: ToolSet(
                    [
                        FunctionTool(
                            name=name,
                            description=description,
                            parameters={"type": "object", "properties": {}},
                            active=active,
                        )
                        for name, description, active in (
                            ("qq_group_info", "查看当前群的简介与基本资料", True),
                            ("qq_group_member_list", "读取当前群成员列表", True),
                            ("qq_send_message", "向指定的群或用户发送消息", True),
                            (
                                "qq_group_member_manage",
                                "管理群成员；演示中由宿主停用",
                                False,
                            ),
                            (
                                "astrbot_file_read_tool",
                                "读取宿主允许目录中的文本文件",
                                True,
                            ),
                        )
                    ]
                )
            ),
            persona_manager=SimpleNamespace(
                get_persona_v3_by_id=lambda name: {"prompt": "演示人格"}
            ),
        )
        context.register_web_api = lambda *args: context.registered_web_apis.append(
            args
        )
        config = AstrBotConfig(
            str(Path(directory) / "ovenheart_config.json"),
            default_config=DEFAULTS
            | {
                "learning_enabled": False,
                "autonomous_tools": ["qq_group_info", "legacy_tool_missing"],
                "autonomous_tool_denylist": ["qq_group_member_manage"],
            },
        )
        plugin = PersonaPlugin(context, config)
        await plugin.initialize()
        app.state.plugin = plugin
        scope = "a" * 24
        observations = [
            "今天又被你猜中了",
            "好家伙，还是你懂我",
            "晚点一起散步吗",
            "这话说得，我都不好意思了",
        ]
        for i, text in enumerate(observations):
            await plugin.store.call(
                "capture",
                scope,
                "qq:GroupMessage:demo",
                "日常的自己",
                "周末闲聊",
                str(i),
                "演示用户",
                text,
                plugin.settings | {"learning_enabled": True},
            )
        await plugin.store.call(
            "mutate",
            SHARED_SCOPE,
            "profile",
            {
                "identity": "温和、有自己的主见，喜欢轻松自然的日常聊天。",
                "reply_style": "简短、日常，接得住玩笑，不刻意寻找话题。",
                "behavior": "先倾听，再回应。对方心情低落时少说教，拿不准就先澄清。",
            },
        )
        batch = await plugin.store.call("batch", scope, 30)
        traits = [
            {
                "kind": kind,
                "situation": situation,
                "content": content,
                "confidence": confidence,
                "evidence": [
                    {
                        "id": m["id"],
                        "sender": "演示用户",
                        "text": m["text"],
                        "created": m["created"],
                    }
                    for m in batch[:2]
                ],
            }
            for kind, situation, content, confidence in (
                (
                    "expression",
                    "被朋友轻松调侃时",
                    "用简短的反问接住玩笑，不急着解释自己",
                    0.94,
                ),
                (
                    "behavior",
                    "对方显得失落时",
                    "先接住情绪，给对方一点说话的空间，再问是否需要建议",
                    0.92,
                ),
                (
                    "personality",
                    "熟悉的朋友日常相处时",
                    "保持温和但有主见，愿意表达不同意见",
                    0.89,
                ),
                (
                    "jargon",
                    "朋友用「好家伙」表示惊讶时",
                    "好家伙：轻松、带点夸张的惊讶，不表示责备",
                    0.96,
                ),
            )
        ]
        run = await plugin.store.call("start_run", scope)
        traits[0]["examples"] = [
            {
                "text": "好家伙",
                "id": batch[1]["id"],
                "sender": "演示用户",
                "source_id": batch[1]["source_id"],
                "created": batch[1]["created"],
                "scope": scope,
            }
        ]
        review = {"enabled": True, "passed": 3, "flagged": 1, "candidates": []}
        for index, trait in enumerate(traits, 1):
            approved = trait["kind"] != "personality"
            trait["_review_approved"] = approved
            review["candidates"].append(
                {
                    "candidate_id": index,
                    "approved": approved,
                    "reason": "演示自检：本批只有日常接话，尚不足以确认稳定的人格倾向。"
                    if not approved
                    else "",
                    **{key: trait[key] for key in ("kind", "situation", "content")},
                }
            )
        await plugin.store.call(
            "finish_run",
            run,
            scope,
            batch,
            traits,
            plugin.settings | {"learning_enabled": True},
            "演示：本批未使用长期记忆",
            "",
            None,
            None,
            review,
            None,
            None,
            True,
            False,
        )
        for i, text in enumerate(observations, start=10):
            await plugin.store.call(
                "capture",
                scope,
                "qq:GroupMessage:demo",
                "日常的自己",
                "周末闲聊",
                str(i),
                "演示用户",
                text,
                plugin.settings | {"learning_enabled": True},
            )
        for action, reason, intent in (
            ("observe", "朋友们已经商量好周末安排，无需插话。", ""),
            ("wait", "对方还在整理经过，先等后续输入。", ""),
            (
                "reply",
                "对方向群内寻求陪伴，简短回应有帮助。",
                "先接住失落情绪，再问是否愿意继续聊。",
            ),
        ):
            await plugin.store.call(
                "decide",
                scope,
                {
                    "action": action,
                    "confidence": 0.91,
                    "reason": reason,
                    "intent": intent,
                },
                "observe",
                "observed",
                plugin.settings,
            )
        for trait in (await plugin.store.call("state", scope, None, None, True))[
            "traits"
        ]:
            if trait["kind"] != "personality":
                await plugin.store.call(
                    "mutate",
                    trait["scope"],
                    "trait",
                    {"id": trait["id"], "status": "active"},
                )
        trace_traits = (await plugin.store.call("guidance", scope, True))["traits"]
        trace_ids = [trait["id"] for trait in trace_traits][:3]
        await plugin.store.call(
            "trace_turn",
            scope,
            "demo-turn",
            "send_hook",
            {
                "route": "quote",
                "target_sender": "演示用户",
                "target_sender_id": "demo-user",
                "target_message_id": "demo-message",
                "reason": "演示：引用上一条回答，直接应答。",
                "selected_ids": trace_ids,
                "included_ids": trace_ids[:2],
                "habit_chars": 1420,
                "habit_budget": 1500,
                "max_traits": 8,
                "context_count": 4,
                "context_limit": 12,
                "context_chars": 1060,
                "context_budget": 12000,
                "temporary_chars": 2570,
                "selector_cache_hit": True,
                "rerank_cache_hit": False,
                "retrieval_mode": "contextual",
                "retrieval_message": "演示：情境筛选选中了三条参考，最终按字符预算组装。",
            },
        )
        second_scope = "b" * 24
        await plugin.store.call(
            "touch_scope",
            second_scope,
            "qq:GroupMessage:demo2",
            "工作时的自己",
            "烘焙讨论",
        )
        for i, text in enumerate(("烘焙先说关键步骤就好", "容易出错的地方最后补充")):
            await plugin.store.call(
                "capture",
                second_scope,
                "qq:GroupMessage:demo2",
                "工作时的自己",
                "烘焙讨论",
                f"bake-{i}",
                "烘焙演示用户",
                text,
                plugin.settings | {"learning_enabled": True},
            )
        second_batch = await plugin.store.call("batch", second_scope, 30)
        second_run = await plugin.store.call("start_run", second_scope)
        await plugin.store.call(
            "finish_run",
            second_run,
            second_scope,
            second_batch,
            [
                traits[0]
                | {
                    "evidence": [
                        {k: m[k] for k in ("id", "sender", "text", "created")}
                        | {
                            "scope": second_scope,
                            "label": "烘焙讨论",
                            "persona": "工作时的自己",
                        }
                        for m in second_batch
                    ],
                    "situation": "讨论烘焙步骤时",
                    "content": "先说明关键步骤，再补充容易出错的地方",
                    "examples": [
                        {
                            "text": "先说关键步骤",
                            "id": second_batch[0]["id"],
                            "sender": "烘焙演示用户",
                            "source_id": second_batch[0]["source_id"],
                            "created": second_batch[0]["created"],
                            "scope": second_scope,
                        },
                        {
                            "text": "容易出错的地方",
                            "id": second_batch[1]["id"],
                            "sender": "烘焙演示用户",
                            "source_id": second_batch[1]["source_id"],
                            "created": second_batch[1]["created"],
                            "scope": second_scope,
                        },
                    ],
                }
            ],
            plugin.settings | {"learning_enabled": True},
            "演示数据",
            "",
            None,
            None,
            None,
            None,
            None,
            True,
            False,
        )
        try:
            yield
        finally:
            await plugin.terminate()
            StarTools.get_data_dir = original
            SessionServiceManager.is_session_enabled = original_session
            SessionPluginManager.is_plugin_enabled_for_session = original_plugin


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def index():
    """Serve the production entry with an explicit fixture banner.

    Returns:
        HTML fixture entry document.
    """
    html = (ROOT / "views/workshop/index.html").read_text(encoding="utf-8")  # noqa: ASYNC240
    html = html.replace(
        '<script type="module"',
        '<script src="/fixture-bridge.js"></script><script type="module"',
    )
    html = html.replace(
        '<main id="workshop">',
        '<main id="workshop"><p class="muted" style="margin-bottom:16px">本地验收 · 演示素材与模拟模型</p>',
    )
    return HTMLResponse(html)


@app.get("/fixture-bridge.js")
async def bridge():
    """Expose a test-only replacement for the host message bridge.

    Returns:
        JavaScript test bridge.
    """
    return Response(
        """
    window.AstrBotPluginView = {
      ready: async () => ({isDark: new URLSearchParams(location.search).get('theme') === 'dark'}),
      onContext: () => {},
      apiGet: async (endpoint, params) => (await fetch('/fixture-api/' + endpoint + '?' + new URLSearchParams(params))).json(),
      apiPost: async (endpoint, body) => (await fetch('/fixture-api/' + endpoint, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)})).json()
    };
    """,
        media_type="application/javascript",
    )


@app.api_route("/fixture-api/{endpoint:path}", methods=["GET", "POST"])
async def api(endpoint: str, request: Request):
    """Invoke the real native page handler with a real host request context.

    Args:
        endpoint: Allowed plugin endpoint.
        request: FastAPI request.

    Returns:
        Native plugin response.
    """
    if endpoint == "decoration-simulation":
        return await decoration_simulation(
            request.query_params.get("decoration", "quote_mention"),
            request.query_params.get("body", "好的，@汐兮雨 你说"),
        )
    handlers = {
        "state": "state",
        "library": "library",
        "action": "action",
        "preview": "preview",
        "preview/model": "model_preview",
        "prompts": "prompt_list",
        "prompts/save": "prompt_save",
    }
    if endpoint not in handlers:
        return {"status": "error", "message": "Unknown test endpoint"}
    with bind_request_context(
        PluginRequest(
            request, plugin_name="astrbot_plugin_ovenheart", username="fixture"
        )
    ):
        return await getattr(app.state.plugin.page_api, handlers[endpoint])()


async def decoration_simulation(decoration: str, body: str) -> dict:
    """Run the real planner parser, delivery hook and host decorator.

    Args:
        decoration: none, quote, mention or quote_mention.
        body: Model-generated reply text for the simulated result.

    Returns:
        The parsed plan choice and the final chain after host decoration.
    """
    from astrbot.core.message.components import At, Plain, Reply
    from astrbot.core.pipeline.result_decorate.stage import ResultDecorateStage

    from astrbot_plugin_ovenheart.participation import parse_plan
    from tests.test_runtime import event

    messages = [
        {
            "sequence": 1,
            "self": False,
            "sender": "汐兮雨",
            "sender_id": "alice",
            "message_id": "older",
            "text": "我要看你的",
        },
        {
            "sequence": 2,
            "self": False,
            "sender": "小鲸",
            "sender_id": "whale",
            "message_id": "newest",
            "text": "咕噜？",
        },
    ]
    plan = parse_plan(
        json.dumps(
            {
                "action": "reply",
                "confidence": 0.9,
                "reason": "对方在等回应。",
                "intent": "回应汐兮雨。",
                "target_sequence": 1,
                "decoration": decoration,
                "wait_seconds": 0,
            },
            ensure_ascii=False,
        ),
        DEFAULTS,
        messages=messages,
    )
    target = messages[0]
    plan = plan | {"scope": "a" * 24, "messages": messages, "target": target}
    newest = event("newest", text="咕噜？", sender="whale", sender_name="小鲸")
    newest.set_result(newest.plain_result(body))
    plugin = app.state.plugin
    plugin.apply_planned_decoration(newest, plan["decoration"], target)
    plugin.realize_result_mentions(newest, plan)

    stage = ResultDecorateStage()
    stage.reply_prefix = ""
    stage.content_safe_check_reply = False
    stage.enable_segmented_reply = False
    stage.only_llm_result = True
    stage.words_count_threshold = 100
    stage.split_mode = "regex"
    stage.split_words = []
    stage.split_words_pattern = None
    stage.content_cleanup_rule = ""
    stage.show_reasoning = False
    stage.tts_trigger_probability = 0
    stage.reply_with_mention = True
    stage.reply_with_quote = True
    stage.forward_threshold = 1000
    stage.t2i_word_threshold = 150
    stage.t2i_use_network = False
    stage.t2i_active_template = ""
    stage.ctx = SimpleNamespace(
        plugin_manager=SimpleNamespace(
            context=SimpleNamespace(
                get_using_tts_provider_async=AsyncMock(return_value=None)
            )
        ),
        astrbot_config={
            "provider_tts_settings": {
                "enable": False,
                "use_file_service": False,
                "dual_output": False,
            },
            "callback_api_base": "",
            "t2i": False,
        },
    )
    async for _ in stage.process(newest):
        pass

    def render(item) -> dict:
        if isinstance(item, Reply):
            return {"type": "Reply", "id": str(item.id)}
        if isinstance(item, At):
            return {"type": "At", "qq": str(item.qq), "name": item.name}
        if isinstance(item, Plain):
            return {"type": "Plain", "text": item.text}
        return {"type": type(item).__name__}

    chain = [render(item) for item in newest.get_result().chain]
    return {
        "decoration": plan["decoration"],
        "body": body,
        "chain": chain,
        "reply_count": sum(item["type"] == "Reply" for item in chain),
        "at_count": sum(item["type"] == "At" for item in chain),
        "plain_has_at": any(
            item["type"] == "Plain" and "@" in item["text"] for item in chain
        ),
    }


@app.get("/{asset}")
def asset(asset: str):
    """Serve only the two production static assets.

    Args:
        asset: Static filename.

    Returns:
        CSS or JavaScript response.
    """
    if asset not in ("style.css", "app.js"):
        return Response(status_code=404)
    return FileResponse(
        ROOT / "views/workshop" / asset, headers={"Cache-Control": "no-store"}
    )
