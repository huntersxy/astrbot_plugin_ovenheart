"""Native authenticated AstrBot Panel API; no additional HTTP server."""

from __future__ import annotations

import asyncio
import json
import time

from astrbot.api import logger
from astrbot.api.web import request
from astrbot.core.agent.message import TextPart
from astrbot.core.star.session_llm_manager import SessionServiceManager
from astrbot.core.star.session_plugin_manager import SessionPluginManager

from . import prompts
from .learning import build_guidance, build_guidance_parts
from .settings import PROFILE_DEFAULTS, SHARED_SCOPE


class ModelPreviewError(ValueError):
    """Separate operator-facing preview failures from provider diagnostics."""


class PageAPI:
    """Expose state and explicit workshop operations through the host bridge."""

    def __init__(self, plugin):
        self.plugin = plugin
        self.model_preview_running = False

    def register(self):
        """Register routes using the same plugin namespace as the Panel view."""
        for route, handler, method in (
            ("state", self.state, "GET"),
            ("library", self.library, "GET"),
            ("action", self.action, "POST"),
            ("preview", self.preview, "POST"),
            ("preview/model", self.model_preview, "POST"),
            ("prompts", self.prompt_list, "GET"),
            ("prompts/save", self.prompt_save, "POST"),
        ):
            self.plugin.context.register_web_api(
                f"/astrbot_plugin_ovenheart/{route}",
                handler,
                [method],
                f"Persona workshop {route}",
            )

    async def state(self):
        """Return current settings, integration status and selected scope data.

        Returns:
            Standard AstrBot plugin API response.
        """
        if not self.plugin.ready:
            return {"status": "error", "message": "人格工坊正在初始化，请稍后刷新。"}
        state = await self.plugin.store.call(
            "state",
            request.query.get("scope"),
            self.plugin.settings["target_senders"],
            self.plugin.settings["blocked_senders"],
            not self.plugin.settings["session_isolation"],
        )
        tools = {
            "available": False,
            "items": [],
            "message": "暂时无法读取 AstrBot 工具列表，可刷新或手动补充名称。",
        }
        try:
            manager = self.plugin.context.get_llm_tool_manager()
            registered = manager.get_full_tool_set()
            entries = {
                tool.name: {
                    "name": tool.name,
                    "description": (tool.description or "")[:600],
                    "active": bool(tool.active),
                }
                for tool in registered.tools
            }
            builtins = getattr(manager, "iter_builtin_tools", None)
            if callable(builtins):
                for tool in builtins():
                    entries.setdefault(
                        tool.name,
                        {
                            "name": tool.name,
                            "description": (tool.description or "")[:600],
                            "active": bool(tool.active),
                        },
                    )
            tools = {
                "available": True,
                "items": [entries[name] for name in sorted(entries)[:1000]],
                "message": f"AstrBot 当前注册 {len(entries)} 个工具。此处选择只缩小自主回复的工具范围，实际调用仍受宿主会话、人格与工具权限限制。",
                "truncated": len(entries) > 1000,
            }
        except Exception as exc:
            logger.debug(
                "OvenHeart tool inventory unavailable (%s)", type(exc).__name__
            )
        state.update(
            settings=self.plugin.settings,
            settings_revision=self.plugin.settings_revision,
            tools=tools,
            prompts_revision=self.plugin.prompts_revision,
            memory=self.plugin.memory.status(),
            busy=list(self.plugin.busy),
            retrieval=self.plugin.retriever.status.get(
                request.query.get("scope")
                if self.plugin.settings["session_isolation"]
                else SHARED_SCOPE,
                {
                    "mode": "idle",
                    "message": "真实回复后显示检索状态；免费预览使用文字相关性。",
                },
            ),
        )
        return {"status": "ok", "data": state}

    async def prompt_list(self):
        """Return every template with its defaults, placeholders and overrides.

        Returns:
            A standard response the dedicated prompt list page renders.
        """
        if not self.plugin.ready:
            return {"status": "error", "message": "人格工坊正在初始化，请稍后刷新。"}
        return {
            "status": "ok",
            "data": {
                "revision": self.plugin.prompts_revision,
                "items": [
                    prompts.describe(key, self.plugin.prompts[key])
                    for key in prompts.KEYS
                ],
            },
        }

    async def prompt_save(self):
        """Validate and persist edited templates from the prompt list page.

        Returns:
            A standard response with an actionable validation error.
        """
        try:
            if not self.plugin.ready:
                raise ValueError("人格工坊正在初始化，请稍后刷新。")
            raw = await request.body()
            if len(raw) > 262144:
                raise ValueError("请求内容超过 256 KB，请缩短提示词。")
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeDecodeError) as exc:
                raise ValueError("请求不是有效的 JSON。") from exc
            if not isinstance(body, dict) or not isinstance(body.get("prompts"), dict):
                raise ValueError("请提交要保存的提示词。")
            await self.plugin.update_prompts(body["prompts"], body.get("revision"))
            return {
                "status": "ok",
                "data": {
                    "message": "提示词已保存并立即生效。",
                    "revision": self.plugin.prompts_revision,
                },
            }
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        except Exception as exc:
            logger.error("Persona prompt save failed (%s)", type(exc).__name__)
            return {
                "status": "error",
                "message": "保存失败，请检查插件数据目录是否可写后重试。",
            }

    async def library(self):
        """Return an authenticated, filtered page of learning entries.

        Returns:
            A standard response containing entries and their conversation owners.
        """
        if not self.plugin.ready:
            return {"status": "error", "message": "人格工坊正在初始化，请稍后刷新。"}
        try:
            result = await self.plugin.store.call(
                "library",
                request.query.get("scope", "all"),
                request.query.get("kind", "all"),
                request.query.get("status", "pending"),
                int(request.query.get("offset", "0")),
            )
            return {"status": "ok", "data": result}
        except (ValueError, TypeError):
            return {"status": "error", "message": "学习库筛选无效，请刷新后重试。"}

    async def action(self):
        """Validate and execute settings, review, learning or restoration actions.

        Returns:
            A standard plugin response with actionable validation errors.
        """
        try:
            if not self.plugin.ready:
                raise ValueError("人格工坊正在初始化，请稍后刷新。")
            raw = await request.body()
            if len(raw) > 65536:
                raise ValueError("请求内容超过 64 KB，请缩短内容。")
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeDecodeError) as exc:
                raise ValueError("请求不是有效的 JSON。") from exc
            if not isinstance(body, dict):
                raise ValueError("请求内容必须为对象。")
            operation = body.get("action")
            payload = body.get("payload", {})
            if not isinstance(payload, dict):
                raise ValueError("操作内容必须为对象。")
            if operation == "settings":
                await self.plugin.update_settings(
                    payload, body.get("settings_revision")
                )
                return {
                    "status": "ok",
                    "data": {"message": "学习设置已保存，并同步到 AstrBot 插件配置。"},
                }
            scope = (
                SHARED_SCOPE
                if operation == "profile"
                and not self.plugin.settings["session_isolation"]
                else body.get("scope")
            )
            if operation == "profile" and (
                ("effect_scope" in body and body["effect_scope"] != scope)
                or (self.plugin.settings["session_isolation"] and scope == SHARED_SCOPE)
            ):
                raise ValueError("人格作用范围已改变，请刷新底稿后重新编辑。")
            if not isinstance(scope, str) or len(scope) != 24:
                raise ValueError("请先选择会话。")
            state = await self.plugin.store.call("state", scope)
            if not state["scope"]:
                raise ValueError("当前会话不存在。")
            if operation == "learn":
                if scope == SHARED_SCOPE:
                    raise ValueError(
                        "请选择实际来源会话后开始学习；学习结果会按当前模式生效。"
                    )
                if self.plugin.busy:
                    raise ValueError("已有学习任务正在执行，请等待完成。")
                self.plugin.track(self.plugin.learn(scope))
                return {
                    "status": "ok",
                    "data": {"message": "已开始学习，可在学习记录中查看进度。"},
                }
            if operation == "profile":
                if set(payload) - PROFILE_DEFAULTS.keys():
                    raise ValueError("人格设置包含未知字段。")
                for key, value in payload.items():
                    if key.endswith("enabled"):
                        if not isinstance(value, bool):
                            raise ValueError("学习和应用开关必须为布尔值。")
                    elif not isinstance(value, str) or len(value) > 1000:
                        raise ValueError("每项人格设定最多 1000 字。")
            elif operation == "feedback":
                if (
                    set(payload) != {"id", "rating"}
                    or type(payload["id"]) is not int
                    or type(payload["rating"]) is not int
                    or payload["rating"] not in (-1, 0, 1)
                ):
                    raise ValueError("反馈必须包含有效条目与 -1、0、1 评价。")
            elif operation == "trait":
                if (
                    set(payload) - {"id", "status", "content", "situation"}
                    or type(payload.get("id")) is not int
                ):
                    raise ValueError("请选择有效的学习条目。")
                if "status" in payload and payload["status"] not in (
                    "pending",
                    "active",
                    "rejected",
                    "disabled",
                ):
                    raise ValueError("学习条目状态不正确。")
                for key, limit in (("content", 350), ("situation", 160)):
                    if key in payload and (
                        not isinstance(payload[key], str)
                        or not payload[key].strip()
                        or len(payload[key]) > limit
                    ):
                        raise ValueError("学习内容或情境为空或超长。")
            elif operation == "restore":
                if type(payload.get("id")) is not int:
                    raise ValueError("请选择有效的历史版本。")
            elif operation == "clear_messages":
                if scope in self.plugin.busy:
                    raise ValueError("正在学习时无法清除素材，请等待本次学习完成。")
            else:
                raise ValueError("不支持的操作，请刷新页面后重试。")
            await self.plugin.store.call("mutate", scope, operation, payload)
            if operation == "clear_messages":
                self.plugin.events.pop(scope, None)
                self.plugin.speaker_events = {
                    key: value
                    for key, value in self.plugin.speaker_events.items()
                    if key[0] != scope
                }
            if scope == SHARED_SCOPE and operation in ("profile", "restore"):
                self.plugin.learning_context.clear()
                self.plugin.participation.close()
            elif operation in ("clear_messages", "profile", "restore"):
                self.plugin.learning_context.pop(scope, None)
            if operation in ("profile", "restore"):
                window = self.plugin.participation.windows.get(scope)
                if window:
                    window["sequence"] += 1
                    window["changed"].set()
            return {"status": "ok", "data": {"message": "已保存。"}}
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        except Exception as exc:
            logger.error("Persona Panel action failed (%s)", type(exc).__name__)
            return {
                "status": "error",
                "message": "保存失败，请检查 AstrBot 配置与插件数据目录是否可写后重试。",
            }

    async def preview(self):
        """Preview effective supplementary guidance without sending or generating text.

        Returns:
            Effective prompt supplement for a sample message.
        """
        try:
            raw = await request.body()
            if len(raw) > 8192:
                raise ValueError("预览内容超过 8 KB。")
            body = json.loads(raw)
            if (
                not isinstance(body, dict)
                or not isinstance(body.get("scope"), str)
                or not isinstance(body.get("query", ""), str)
            ):
                raise ValueError("请选择会话并输入有效的预览文本。")
            state = await self.plugin.store.call(
                "guidance", body["scope"], not self.plugin.settings["session_isolation"]
            )
            if not state["scope"]:
                raise ValueError("请先选择已有会话。")
            selection = await self.plugin.retriever.select(
                body["scope"]
                if self.plugin.settings["session_isolation"]
                else SHARED_SCOPE,
                state["traits"],
                body.get("query", "")[:1200],
                self.plugin.settings,
                semantic=False,
                record_status=False,
                literal_query=body.get("query", "")[:1200],
            )
            included_ids = []
            guidance = build_guidance(
                state["scope"]["profile"],
                selection["traits"],
                self.plugin.settings,
                body.get("query", "")[:1200],
                selected=True,
                included_ids=included_ids,
                templates=self.plugin.prompts,
            )
            return {
                "status": "ok",
                "data": {
                    "guidance": guidance,
                    "length": len(guidance),
                    "retrieval": selection["message"],
                    "selected": [
                        {"id": t["id"], "reason": t["_reason"], "score": t["_score"]}
                        for t in selection["traits"]
                        if t["id"] in included_ids
                    ],
                },
            }
        except (ValueError, UnicodeDecodeError) as exc:
            return {
                "status": "error",
                "message": str(exc)
                if not isinstance(exc, json.JSONDecodeError)
                else "预览请求不是有效的 JSON。",
            }

    async def model_preview(self):
        """Generate an explicitly requested example without a chat event or tools.

        Returns:
            Example text and reference IDs, or a credential-free error. No
            platform send, conversation write or exposure record is performed.
        """
        if not self.plugin.ready or self.plugin.closing:
            return {"status": "error", "message": "人格工坊尚未就绪，请稍后刷新。"}
        if self.model_preview_running:
            return {"status": "error", "message": "已有示例正在生成，请等待本次完成。"}
        self.model_preview_running = True
        started = time.monotonic()
        try:
            raw = await request.body()
            if len(raw) > 8192:
                raise ModelPreviewError("示例输入超过 8 KB。")
            body = json.loads(raw)
            if (
                not isinstance(body, dict)
                or not isinstance(body.get("scope"), str)
                or not isinstance(body.get("query"), str)
                or not body["query"].strip()
                or len(body["query"]) > 1200
            ):
                raise ModelPreviewError("请选择真实来源会话，并输入 1–1200 字的示例。")
            scope, query = body["scope"], body["query"].strip()
            settings = self.plugin.settings.copy()
            revision = self.plugin.prompts_revision
            if not settings["enabled"] or not settings["apply_enabled"]:
                raise ModelPreviewError("人格应用已关闭，请先启用后再生成示例。")
            source = await self.plugin.store.call("state", scope)
            source = source["scope"]
            if source is None or scope == SHARED_SCOPE:
                raise ModelPreviewError(
                    "请先在顶部选择真实素材来源会话，以确定宿主人格和聊天模型。"
                )
            umo, persona = source["umo"], source["persona"]
            host_config = self.plugin.context.get_config(umo)
            plugin_set = host_config.get("plugin_set", ["*"])
            if (
                (
                    settings["allowed_sessions"]
                    and umo not in settings["allowed_sessions"]
                )
                or (
                    plugin_set != ["*"] and "astrbot_plugin_ovenheart" not in plugin_set
                )
                or not await SessionServiceManager.is_session_enabled(umo)
                or not await SessionPluginManager.is_plugin_enabled_for_session(
                    umo, "astrbot_plugin_ovenheart"
                )
            ):
                raise ModelPreviewError("当前来源会话或插件已停用，请恢复启用后重试。")
            state = await self.plugin.store.call(
                "guidance", scope, not settings["session_isolation"]
            )
            if not state["scope"] or not state["scope"]["profile"]["apply_enabled"]:
                raise ModelPreviewError("当前人格已暂停应用，请先启用。")
            anchor = self.plugin.context.persona_manager.get_persona_v3_by_id(persona)
            if not anchor and persona != "[%None]":
                raise ModelPreviewError("所选宿主人格已不存在，请重新选择来源会话。")
            host_prompt = anchor["prompt"] if anchor else ""
            provider_id = await self.plugin.context.get_current_chat_provider_id(umo)
            if not provider_id:
                raise ModelPreviewError("当前会话没有聊天模型，请先在 AstrBot 配置。")
            selection = await self.plugin.track(
                self.plugin.retriever.select(
                    scope if settings["session_isolation"] else SHARED_SCOPE,
                    state["traits"],
                    query,
                    settings,
                    provider_id=settings["provider_id"] or provider_id,
                    identity=json.dumps(
                        {
                            "host": host_prompt[:4000],
                            "profile": state["scope"]["profile"],
                        },
                        ensure_ascii=False,
                    ),
                    literal_query=query,
                    recent_expression_ids=state.get("recent_expression_ids", []),
                    record_status=False,
                )
            )
            latest = await self.plugin.store.call(
                "guidance", scope, not settings["session_isolation"]
            )
            if (
                self.plugin.settings != settings
                or self.plugin.prompts_revision != revision
                or latest["scope"] != state["scope"]
            ):
                raise ModelPreviewError("设置或人格已改变，旧预览已取消，请重新生成。")
            current = {t["id"]: t for t in latest["traits"]}
            selected = [
                current[t["id"]]
                for t in selection["traits"]
                if t["id"] in current
                and current[t["id"]]["content"] == t["content"]
                and current[t["id"]]["situation"] == t["situation"]
            ]
            system, temporary, included = build_guidance_parts(
                latest["scope"]["profile"],
                selected,
                settings,
                query,
                host_prompt=host_prompt,
                templates=self.plugin.prompts,
            )
            response = await asyncio.wait_for(
                self.plugin.track(
                    self.plugin.context.llm_generate(
                        chat_provider_id=provider_id,
                        prompt=query,
                        system_prompt=host_prompt + ("\n\n" + system if system else ""),
                        contexts=[],
                        tools=None,
                        extra_user_content_parts=[
                            TextPart(text=temporary).mark_as_temp()
                        ]
                        if temporary
                        else [],
                    )
                ),
                timeout=90,
            )
            final = await self.plugin.store.call(
                "guidance", scope, not settings["session_isolation"]
            )
            final_traits = {t["id"]: t for t in final["traits"]}
            final_anchor = self.plugin.context.persona_manager.get_persona_v3_by_id(
                persona
            )
            if (
                self.plugin.settings != settings
                or self.plugin.prompts_revision != revision
                or final["scope"] != state["scope"]
                or (final_anchor["prompt"] if final_anchor else "") != host_prompt
                or await self.plugin.context.get_current_chat_provider_id(umo)
                != provider_id
                or any(
                    t["id"] in included
                    and (
                        t["id"] not in final_traits
                        or any(
                            final_traits[t["id"]][key] != t[key]
                            for key in ("content", "situation")
                        )
                    )
                    for t in selected
                )
            ):
                raise ModelPreviewError(
                    "设置、人格或选中习惯已改变，旧预览已取消，请重新生成。"
                )
            reply = (response.completion_text or "").strip()
            if not reply:
                raise ModelPreviewError("模型没有生成文字，请重试或检查模型输出上限。")
            seconds = round(time.monotonic() - started, 2)
            self.plugin.debug(
                "model_preview",
                scope=scope,
                provider=provider_id,
                query=query,
                system=system,
                temporary_user=temporary,
                included_ids=included,
                reply=reply,
                seconds=seconds,
            )
            return {
                "status": "ok",
                "data": {
                    "reply": reply,
                    "seconds": seconds,
                    "selected_ids": included,
                    "retrieval": selection["message"],
                },
            }
        except ModelPreviewError as exc:
            return {"status": "error", "message": str(exc)}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {"status": "error", "message": "示例请求不是有效的 JSON 文本。"}
        except asyncio.TimeoutError:
            return {
                "status": "error",
                "message": "示例生成超时（生成阶段最多等待 90 秒），请检查模型连接或稍后重试。",
            }
        except Exception as exc:
            logger.warning("OvenHeart model preview failed (%s)", type(exc).__name__)
            return {
                "status": "error",
                "message": "示例生成失败，请检查当前聊天模型、模型接口连接及宿主人格配置。",
            }
        finally:
            self.model_preview_running = False
