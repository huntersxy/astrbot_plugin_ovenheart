"""Scoped original contact sheets and explicit planner attachments after text sends."""

from __future__ import annotations

import asyncio
import base64
import json
from itertools import count
from pathlib import Path
from random import sample

from astrbot.core.message.components import Image
from astrbot.core.message.message_event_result import MessageChain
from mcp.types import CallToolResult, ImageContent, TextContent

from ..core.settings import SHARED_SCOPE
from ..vendor.maibot.emoji_tiles import (
    EmojiSelectionResult,
    _calculate_grid_shape,
    _merge_emoji_tiles,
)

_indices = count(1)


class EmojiActions:
    def __init__(self, plugin):
        self.plugin = plugin

    async def candidates(self, session, query=""):
        local = await self.plugin.room_memory.call(
            session.scope, "image_candidates", query, event=session.event
        )
        public = await self.plugin.room_memory.call(
            SHARED_SCOPE, "image_candidates", query, event=session.event
        )
        seen = {item["asset"]["content_hash"] for item in local}
        return [item | {"scope": session.scope} for item in local] + [
            item | {"scope": SHARED_SCOPE}
            for item in public
            if item["asset"]["content_hash"] not in seen
        ]

    async def show(self, session, query=""):
        if not self.plugin.settings[
            "emoji_enabled"
        ] or not await self.plugin.image_memory.permitted(
            session.event, session.scope, session.persona
        ):
            return "当前表情选择未启用或无权访问。"
        available = await self.candidates(session, query)
        if not available:
            return "公共图库与当前会话暂无可选表情；请在工坊的表情页上传或导入素材。普通图片不能充当表情候选。"
        selected = sample(available, min(6, len(available)))
        indices = [next(_indices) for _ in selected]
        captions, images = [], []
        for index, item in zip(indices, selected, strict=True):
            caption = next(
                (
                    row["text"]
                    for row in reversed(item["observations"])
                    if json.loads(row["evidence_json"]).get("is_emoji") is True
                ),
                "表情包",
            )
            session.emojis[index] = {
                "asset_id": item["asset"]["asset_id"],
                "caption": caption,
                "scope": item["scope"],
            }
            captions.append({"index": index, "description": caption[:500]})
            if self.plugin.settings["planner_visual_enabled"]:
                images.append(await asyncio.to_thread(Path(item["path"]).read_bytes))
        # Keep the current planner's bounded offered IDs; never accept a path
        # or global library ID invented by the model.
        session.emojis = dict(list(session.emojis.items())[-24:])
        await session.check_current()
        text = (
            "本次可选表情，仅供内部查看；reply.attach_emoji填写选中序号，文字发送后再发送该表情。\n"
            + json.dumps(captions, ensure_ascii=False)
        )
        content = [TextContent(type="text", text=text)]
        if images:
            collage = await asyncio.to_thread(_merge_emoji_tiles, images, indices[0])
            content.append(
                ImageContent(
                    type="image",
                    mimeType="image/png",
                    data=base64.b64encode(collage).decode(),
                )
            )
        return CallToolResult(content=content)

    async def send(self, event, session, index):
        choice = session.emojis.get(index)
        if not choice:
            return {"sent": False, "error": "该序号没有在当前 Planner 中提供。"}
        await session.check_current()
        if not self.plugin.settings[
            "emoji_enabled"
        ] or not await self.plugin.image_memory.permitted(
            event, session.scope, session.persona
        ):
            return {"sent": False, "error": "当前表情发送权限已撤销。"}
        selected = await self.plugin.room_memory.call(
            choice["scope"], "image_asset", choice["asset_id"], event=event
        )
        if not selected["success"]:
            return {"sent": False, "error": "选中的原始表情已删除或不可见。"}
        image = Image.fromFileSystem(selected["path"])
        # The observed bytes/caption are attached to this exact image object,
        # so a mixed tool chain cannot borrow another image's description.
        hints = event.get_extra("ovenheart_explicit_image_hints", {})
        hints[id(image)] = choice["caption"]
        event.set_extra("ovenheart_explicit_image_hints", hints)
        try:
            result = await event.send(MessageChain(chain=[image]))
            if result is False:
                return {"sent": False, "error": "原生适配器未接受表情发送。"}
            return {
                "sent": True,
                "index": index,
                "description": choice["caption"],
                "delivery_status": "native send returned; platform acknowledgement unverified",
            }

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return {
                "sent": False,
                "error": "表情发送未完成（" + type(exc).__name__ + "）。",
            }

    async def select(self, session):
        """Original visual sub-agent contract: recent twelve turns and one grid."""
        if not self.plugin.settings[
            "emoji_enabled"
        ] or not await self.plugin.image_memory.permitted(
            session.event, session.scope, session.persona
        ):
            return {"success": False, "error": "当前表情选择未启用或无权访问。"}
        provider = self.plugin.settings["emoji_provider_id"]
        if not provider and self.plugin.settings["planner_visual_enabled"]:
            provider = self.plugin.settings[
                "planner_provider_id"
            ] or await self.plugin.context.get_current_chat_provider_id(
                session.event.unified_msg_origin
            )
        if not provider:
            provider = (
                self.plugin.context.get_config(session.event.unified_msg_origin)
                .get("provider_ltm_settings", {})
                .get("image_caption_provider_id", "")
            )
        if not provider:
            return {
                "success": False,
                "error": "未配置表情选择视觉模型。请设置emoji_provider_id或宿主图片描述模型。",
            }
        available = await self.candidates(session)
        if not available:
            return {"success": False, "error": "当前会话没有可用表情包。"}
        selected = sample(available, min(6, len(available)))
        images = await asyncio.gather(
            *(asyncio.to_thread(Path(item["path"]).read_bytes) for item in selected)
        )
        collage = await asyncio.to_thread(_merge_emoji_tiles, images)
        rows, columns = _calculate_grid_shape(len(selected))
        template = (
            Path(__file__).parent.parent / "vendor/maibot/emoji_selection.txt"
        ).read_text(encoding="utf-8")
        await session.check_current()
        try:
            response = await asyncio.wait_for(
                self.plugin.auxiliary.generate(
                    "emoji_select",
                    chat_provider_id=provider,
                    system_prompt=template.format(
                        emoji_count=len(selected), grid_rows=rows, grid_columns=columns
                    ),
                    prompt=json.dumps(
                        [
                            {
                                "sender": row["sender"],
                                "text": row["text"],
                                "media": row.get("media", []),
                            }
                            for row in session.messages[-12:]
                        ],
                        ensure_ascii=False,
                    ),
                    image_urls=[
                        "data:image/png;base64," + base64.b64encode(collage).decode()
                    ],
                ),
                timeout=session.settings["planner_timeout"],
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return {
                "success": False,
                "error": "表情选择模型请求失败（" + type(exc).__name__ + "）。",
            }
        await session.check_current()
        fallback = False
        if getattr(response, "role", "") == "err":
            return {"success": False, "error": "表情选择模型返回了错误响应。"}
        try:
            choice = EmojiSelectionResult.model_validate_json(
                response.completion_text or ""
            )
            offset, reason = choice.emoji_index - 1, choice.reason.strip()
            if not 0 <= offset < len(selected):
                offset, fallback = 0, True
        except ValueError:
            offset, reason, fallback = 0, "", True
        chosen = selected[offset]
        caption = next(
            (
                item["text"]
                for item in reversed(chosen["observations"])
                if json.loads(item["evidence_json"]).get("is_emoji") is True
            ),
            "表情包",
        )
        index = next(_indices)
        session.emojis[index] = {
            "asset_id": chosen["asset"]["asset_id"],
            "caption": caption,
            "scope": chosen["scope"],
        }
        return {
            "success": True,
            "index": index,
            "reason": reason,
            "fallback_to_first": fallback,
        }
