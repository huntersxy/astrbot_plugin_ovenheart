"""Native authenticated AstrBot Panel API; no additional HTTP server."""

from __future__ import annotations

import json

from astrbot.api import logger
from astrbot.api.web import request

from .learning import build_guidance
from .settings import PROFILE_DEFAULTS, validate_settings


class PageAPI:
    """Expose state and explicit workshop operations through the host bridge."""

    def __init__(self, plugin):
        self.plugin = plugin

    def register(self):
        """Register routes using the same plugin namespace as the Panel view."""
        for route, handler, method in (
            ("state", self.state, "GET"),
            ("action", self.action, "POST"),
            ("preview", self.preview, "POST"),
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
        state = await self.plugin.store.call("state", request.query.get("scope"))
        state.update(
            settings=self.plugin.settings,
            memory=self.plugin.memory.status(),
            busy=list(self.plugin.busy),
            retrieval=self.plugin.retriever.status.get(
                request.query.get("scope"),
                {
                    "mode": "idle",
                    "message": "真实回复后显示检索状态；免费预览使用文字相关性。",
                },
            ),
        )
        return {"status": "ok", "data": state}

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
                patch = validate_settings(payload)
                merged = self.plugin.settings | patch
                if merged["min_messages"] > merged["batch_size"]:
                    raise ValueError("自动学习的素材门槛不能大于每批素材数量。")
                if merged["min_evidence"] > merged["batch_size"]:
                    raise ValueError("最少证据数量不能大于每批素材数量。")
                await self.plugin.store.call("settings", patch)
                self.plugin.settings = merged
                return {"status": "ok", "data": {"message": "学习设置已保存。"}}
            scope = body.get("scope")
            if not isinstance(scope, str) or len(scope) != 24:
                raise ValueError("请先选择会话。")
            state = await self.plugin.store.call("state", scope)
            if not state["scope"]:
                raise ValueError("当前会话不存在。")
            if operation == "learn":
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
            return {"status": "ok", "data": {"message": "已保存。"}}
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        except Exception as exc:
            logger.error("Persona Panel action failed (%s)", type(exc).__name__)
            return {
                "status": "error",
                "message": "保存失败，请检查插件数据目录是否可写并重试。",
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
            state = await self.plugin.store.call("guidance", body["scope"])
            if not state["scope"]:
                raise ValueError("请先选择已有会话。")
            selection = await self.plugin.retriever.select(
                body["scope"],
                state["traits"],
                body.get("query", "")[:1200],
                self.plugin.settings,
                semantic=False,
            )
            included_ids = []
            guidance = build_guidance(
                state["scope"]["profile"],
                selection["traits"],
                self.plugin.settings,
                body.get("query", "")[:1200],
                selected=True,
                included_ids=included_ids,
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
