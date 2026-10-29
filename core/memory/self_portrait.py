"""Persona-owned visual references, delivered through native temporary parts."""

import asyncio
import base64

import jsonschema
from astrbot.api.web import request
from astrbot.core.agent.message import ImageURLPart, TextPart

from ..settings import SHARED_SCOPE
from ..vendor.maibot.amemorix.image.asset_store import ImageAssetStore

MARKER = "[OvenHeart self portrait]\n"
SCHEMA = {
    "type": "object",
    "required": ["scope", "action"],
    "properties": {
        "scope": {"type": "string", "pattern": "^[0-9a-f]{24}$"},
        "action": {"enum": ["save", "clear"]},
        "description": {
            "type": "string",
            "minLength": 1,
            "maxLength": 1200,
            "pattern": r"\S",
        },
        "encoded": {"type": "string", "maxLength": 2800000},
    },
    "additionalProperties": False,
}


class SelfPortrait:
    def __init__(self, plugin):
        self.plugin = plugin

    async def persona(self, scope):
        if not scope or scope == SHARED_SCOPE:
            raise ValueError(
                "请选择要编辑的人格自画像。"
            )
        row = await self.plugin.store.read("scope_profile", scope)
        if row is None:
            raise ValueError("所选会话已不存在，请重新选择。")
        return row["persona"]

    async def read(self):
        try:
            persona = await self.persona(request.query.get("scope", ""))
            row = await self.plugin.store.read(
                "self_portrait", persona, None, request.query.get("image") == "true"
            )
            if row and "image" in row:
                image = row.pop("image")
                row["encoded"] = base64.b64encode(image).decode() if image else ""
            return {"status": "ok", "data": row}
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}

    async def save(self):
        try:
            body = await request.json()
            jsonschema.validate(body, SCHEMA)
            persona = await self.persona(body["scope"])
            if body["action"] == "clear":
                result = await self.plugin.store.call(
                    "self_portrait", persona, {"clear": True}
                )
            else:
                if not body.get("description", "").strip():
                    raise ValueError(
                        "请填写外观特征，帮助未开启识图的模型理解自己的形象。"
                    )
                old = await self.plugin.store.read("self_portrait", persona, None, True)
                payload = {
                    "description": body["description"].strip(),
                    "image": old.get("image") if old else None,
                    "mime_type": old.get("mime_type", "") if old else "",
                    "content_hash": old.get("content_hash", "") if old else "",
                }
                if body.get("encoded"):
                    image = base64.b64decode(body["encoded"], validate=True)
                    validator = ImageAssetStore(
                        self.plugin.store.path.parent,
                        max_bytes=2 * 1024 * 1024,
                        max_pixels=4_194_304,
                    )
                    inspected = await asyncio.to_thread(validator.inspect, image)
                    if inspected.frame_count != 1 or inspected.mime_type not in {
                        "image/png",
                        "image/jpeg",
                        "image/webp",
                    }:
                        raise ValueError("自画像请选择静态 PNG、JPG 或 WebP 图片。")
                    payload.update(
                        image=image,
                        mime_type=inspected.mime_type,
                        content_hash=validator.content_hash(image),
                    )
                result = await self.plugin.store.call("self_portrait", persona, payload)
            return {"status": "ok", "data": result}
        except (ValueError, jsonschema.ValidationError) as exc:
            return {
                "status": "error",
                "message": str(exc)
                if isinstance(exc, ValueError)
                else "自画像格式无效，请检查描述和图片大小。",
            }

    async def context(self, persona):
        row = await self.plugin.store.read("self_portrait", persona)
        return (
            {
                "description": row["description"],
                "has_reference_image": bool(row["has_image"]),
            }
            if row
            else None
        )

    async def apply(self, event, persona, req):
        if any(
            isinstance(part, TextPart) and part.text.startswith(MARKER)
            for part in req.extra_user_content_parts
        ):
            return
        row = await self.plugin.store.read("self_portrait", persona)
        if row is None:
            return
        req.extra_user_content_parts.append(
            TextPart(
                text=(
                    MARKER
                    + "这是管理员为当前人格设置的外观资料，仅描述你的形象，不替换宿主人格和身份。"
                    "来图中的人物不自动等于你；结合实际可见特征辨认，不能把其他人的形象、经历认作自己的。\n"
                    + row["description"]
                )
            ).mark_as_temp()
        )
        if not req.image_urls or not row["has_image"]:
            return
        provider_id = await self.plugin.context.get_current_chat_provider_id(
            event.unified_msg_origin
        )
        provider = self.plugin.context.get_provider_by_id(provider_id)
        if provider is None or "image" not in provider.provider_config.get(
            "modalities", []
        ):
            return
        row = await self.plugin.store.read("self_portrait", persona, None, True)
        if not row or not row.get("image"):
            return
        req.extra_user_content_parts.append(
            TextPart(
                text="下面附图是你的自画像参考，不是对方本轮发送的图片；对照它与来图辨认外观。"
            ).mark_as_temp()
        )
        req.extra_user_content_parts.append(
            ImageURLPart(
                image_url=ImageURLPart.ImageURL(
                    url=f"data:{row['mime_type']};base64,"
                    + base64.b64encode(row["image"]).decode()
                )
            ).mark_as_temp()
        )
