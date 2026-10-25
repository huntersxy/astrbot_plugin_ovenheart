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

from .vendor.maibot.emoji_tiles import _merge_emoji_tiles

_indices = count(1)


class EmojiActions:
    def __init__(self, plugin):
        self.plugin = plugin

    async def show(self, session):
        if not self.plugin.settings[
            "emoji_enabled"
        ] or not await self.plugin.image_memory.permitted(
            session.event, session.scope, session.persona
        ):
            return "当前表情选择未启用或无权访问。"
        available = await self.plugin.room_memory.call(
            session.scope, "image_candidates", event=session.event
        )
        if not available:
            return "当前会话尚无已识别的表情包；普通图片不能充当表情候选。"
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
            session.scope, "image_asset", choice["asset_id"], event=event
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
                "error": "表情发送未完成（"
                + type(exc).__name__
                + "）。文字回复已发送。",
            }
