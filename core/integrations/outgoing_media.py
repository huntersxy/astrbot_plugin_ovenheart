"""Record media after native sends, including meme_manager's separate sends."""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import inspect
import time
from pathlib import Path
from uuid import uuid4

from astrbot.api import logger
from astrbot.core.message.components import Image, Plain
from astrbot.core.utils.media_utils import MediaResolver, file_uri_to_path, is_file_uri


def file_digest(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


class OutgoingMediaBridge:
    def __init__(self, plugin):
        self.plugin = plugin
        self._owner = None
        self._hooks = []

    def disconnect(self):
        for owner, name, original, wrapper, local in self._hooks:
            if getattr(owner, name, None) is wrapper:
                if local:
                    setattr(owner, name, original)
                else:
                    delattr(owner, name)
        self._hooks.clear()
        self._owner = None

    @staticmethod
    def prepare(event):
        candidates = event.get_extra("meme_manager_semantic_candidates")
        selected = event.get_extra("meme_manager_semantic_selected_ids")
        if not isinstance(candidates, dict) or not isinstance(selected, list):
            return
        hints = []
        for key in selected[:4]:
            if not isinstance(key, str):
                continue
            value = candidates.get(key)
            if isinstance(value, dict):
                hints.append(
                    {
                        "id": str(key)[:100],
                        "caption": str(value.get("caption") or "").strip()[:1000],
                        "sha256": str(value.get("content_sha256") or ""),
                    }
                )
        # A new selection replaces old hints, including an explicit empty choice.
        event.set_extra("ovenheart_outgoing_meme_hints", hints)

    def install(self):
        meta = self.plugin.context.get_registered_star("meme_manager")
        owner = meta.star_cls if meta and meta.activated else None
        if owner is None:
            self.disconnect()
            return False
        if self._owner is owner and all(
            getattr(o, n, None) is w for o, n, _, w, _ in self._hooks
        ):
            return True
        self.disconnect()
        originals = {}
        for name, args, asynchronous in (
            ("_send_meme_image", ("event", "image"), True),
            ("_on_decorating_result_impl", ("event",), True),
            ("_scope_allows_llm_origin", ("event",), False),
        ):
            method = getattr(owner, name, None)
            if (
                not callable(method)
                or inspect.iscoroutinefunction(method) != asynchronous
                or getattr(method, "__self__", None) is not owner
                or tuple(inspect.signature(method).parameters) != args
            ):
                logger.warning("表情包发送接口形状不匹配，暂不安装独立发送记录适配")
                return False
            originals[name] = method

        async def decorate(event):
            self.prepare(event)
            return await originals["_on_decorating_result_impl"](event)

        async def send(event, image):
            self.prepare(event)
            result = await originals["_send_meme_image"](event, image)
            # Only a successful return is a send observation. Search/selection
            # and a failed send must not become a claim that we posted a meme.
            await self.capture(event, [image], meme=True)
            return result

        def allows(event):
            plan = event.get_extra("ovenheart_decision") or {}
            if plan.get("planner_session") and all(
                self.plugin.settings[key]
                for key in (
                    "enabled",
                    "apply_enabled",
                    "emoji_enabled",
                    "native_memory_enabled",
                    "image_memory_enabled",
                    "memory_enabled",
                )
            ):
                return False
            return originals["_scope_allows_llm_origin"](event)

        for name, wrapper in (
            ("_on_decorating_result_impl", decorate),
            ("_send_meme_image", send),
            ("_scope_allows_llm_origin", allows),
        ):
            self._hooks.append(
                (owner, name, owner.__dict__.get(name), wrapper, name in owner.__dict__)
            )
            setattr(owner, name, wrapper)
        self._owner = owner
        return True

    async def capture_result(self, event):
        # The host can invoke its after-send hook even after a component failed.
        # Retry only receipts observed through successful sends, never the whole
        # result chain (which may include an image that was not sent).
        await self.capture(event, event.get_extra("ovenheart_sent_images") or [])

    def bind_event(self, event):
        """Observe this event's actual native sends without changing a platform class."""
        if event.get_extra("ovenheart_media_send_bound"):
            return
        original = event.send

        async def failed():
            event.set_extra("ovenheart_text_delivery_incomplete", True)
            event.set_extra(
                "ovenheart_planner_receipt",
                {
                    "send_hook": False,
                    "reply_text": "\n".join(
                        event.get_extra("ovenheart_actual_text_segments", [])
                    ),
                    "delivery_uncertain": True,
                    "delivery_status": "native send failed; platform delivery unverified",
                },
            )
            # Another after-send hook may stop propagation after a platform
            # error. Persist the outcome here instead of leaving it prepared.
            try:
                await self.plugin.record_turn_trace(
                    event,
                    status="send_failed",
                    cancel_reason="平台发送失败，未确认全部内容送达。",
                )
            except Exception as exc:
                logger.warning("发送失败记录暂未写入 (%s)", type(exc).__name__)

        async def send(message):
            text = any(
                isinstance(part, Plain) and part.text.strip() for part in message.chain
            )
            wire = message
            if self.plugin.settings["emoji_static_to_gif"]:
                from .image_delivery import single_frame_gif

                parts = []
                for part in message.chain:
                    converted = None
                    if isinstance(part, Image):
                        try:
                            encoded = await asyncio.wait_for(part.convert_to_base64(), 3)
                            converted = await asyncio.to_thread(single_frame_gif, base64.b64decode(encoded))
                        except Exception as exc:
                            logger.warning("静态图转 GIF 未完成，使用原图发送 (%s)", type(exc).__name__)
                    parts.append(Image.fromBytes(converted) if converted is not None else part)
                wire = copy.copy(message)
                wire.chain = parts
                # Transport encoding does not create another library asset.
                # Keep captions and memory references tied to the original.
            try:
                result = await original(wire)
            except Exception:
                if text:
                    await failed()
                raise
            if result is False:
                if text:
                    await failed()
                return result
            if text and event.get_extra("ovenheart_first_text_send_seconds") is None:
                event.set_extra("ovenheart_first_text_send_seconds", round(max(0, time.time() - event.created_at), 3))
            images = [
                p for p in getattr(message, "chain", None) or [] if isinstance(p, Image)
            ]
            if images:
                receipts = event.get_extra("ovenheart_sent_images") or []
                event.set_extra("ovenheart_sent_images", (receipts + images)[-4:])
                await self.capture(event, images)
            return result

        event.send = send
        event.set_extra("ovenheart_media_send_bound", True)

    async def _reference(self, event, image, cid):
        meta = self.plugin.context.get_registered_star("astrbot_plugin_qq_enhance")
        owner = meta.star_cls if meta and meta.activated else None
        cfg = getattr(owner, "config", {}).get("context_images", {})
        cache = getattr(getattr(owner, "context_images", None), "group_cache", None)
        if not cache or not cfg.get("enabled") or not cfg.get("group_cache_enabled"):
            return None
        source = image.path or image.url or image.file

        # Reuse the installed store's real scoped references and its TTL/quota.
        async def retain():
            async with MediaResolver(source, media_type="image").as_path() as resolved:
                return await cache.put(
                    str(resolved.path),
                    str(event.get_platform_id()),
                    str(event.unified_msg_origin),
                    cid,
                )

        return await asyncio.wait_for(retain(), timeout=3)

    async def capture(self, event, images, *, meme=False):
        plugin = self.plugin
        if not images or plugin.closing:
            return
        if not plugin.conversation.active(event):
            await self.capture_native(event, images, meme=meme)
            return
        try:
            if not await plugin.permitted(event):
                return
            scope = event.get_extra("ovenheart_reply_scope")
            if not scope or (await plugin.resolve_scope(event))[0] != scope:
                return
            binding = event.get_extra("ovenheart_context_binding")
            if (
                not binding
                or binding[0] != scope
                or not await plugin.conversation.valid(event, scope, binding[1])
            ):
                return
            hints = event.get_extra("ovenheart_outgoing_meme_hints") or []
            cfg = plugin.context.get_config(event.unified_msg_origin).get(
                "provider_ltm_settings", {}
            )
            can_caption = bool(
                cfg.get("image_caption") and cfg.get("image_caption_provider_id")
            )
            seen = event.get_extra("ovenheart_outgoing_media_seen")
            if seen is None:
                seen = set()
                event.set_extra("ovenheart_outgoing_media_seen", seen)
            media, refs, pending, captured_ids = [], [], [], []
            for image in images[:4]:
                if id(image) in seen:
                    continue
                caption = event.get_extra("ovenheart_explicit_image_hints", {}).get(
                    id(image), ""
                )
                # Separate meme sends contain exactly the selected meme. For a
                # mixed chain match bytes, never assign a selection to another
                # tool's image just because it is the only candidate.
                if not caption and meme and len(hints) == 1:
                    caption = hints[0]["caption"]
                elif (
                    not caption
                    and hints
                    and image.file
                    and not image.file.startswith(("base64://", "http://", "https://"))
                ):
                    path = Path(
                        image.path
                        or (
                            file_uri_to_path(image.file)
                            if is_file_uri(image.file)
                            else image.file
                        )
                    )
                    digest = await asyncio.to_thread(file_digest, path)
                    caption = next(
                        (h["caption"] for h in hints if h["sha256"] == digest), ""
                    )
                reference = None
                try:
                    cid = plugin.participation.windows[scope].get("cid", "")
                    reference = await self._reference(event, image, cid)
                except Exception as exc:
                    logger.warning(
                        "自己的图片暂未取得可回看引用 (%s)", type(exc).__name__
                    )
                item = {"type": "image", "status": "ready" if caption else "pending"}
                if caption:
                    item.update(text=caption, description_source="meme_metadata")
                if reference:
                    refs.append(reference["image_ref"])
                    item["image_ref"] = reference["image_ref"]
                if not caption and can_caption:
                    # Capture temporary GIF bytes before the sender cleans them
                    # up. Description runs later, without delaying the next turn.
                    try:
                        encoded = await asyncio.wait_for(
                            image.convert_to_base64(), timeout=3
                        )
                        if encoded and len(encoded) <= 16 * 1024 * 1024:
                            pending.append(Image.fromBase64(encoded))
                        else:
                            item["status"] = "unavailable"
                    except Exception:
                        item["status"] = "unavailable"
                elif not caption:
                    item["status"] = "available"
                media.append(item)
                captured_ids.append(id(image))
            if not media:
                return
            if not await plugin.permitted(event) or not await plugin.conversation.valid(
                event, scope, binding[1]
            ):
                return
            source = "bot-media:" + uuid4().hex
            record = await plugin.conversation.delivered(
                event,
                scope,
                event.get_extra("ovenheart_decision"),
                "",
                False,
                media=media,
                image_refs=refs,
                source=source,
            )
            if record:
                seen.update(captured_ids)
                if plugin.settings["image_memory_enabled"]:
                    payloads = []
                    for image in images[:4]:
                        if id(image) in captured_ids:
                            encoded = await asyncio.wait_for(
                                image.convert_to_base64(), 3
                            )
                            payloads.append(base64.b64decode(encoded, validate=True))
                    _, persona, _ = await plugin.resolve_scope(event)
                    stored_media = [
                        item | {"is_emoji": bool(item.get("text"))} for item in media
                    ]
                    plugin.track(
                        plugin.image_memory.sent(
                            event, scope, persona, source, payloads, stored_media
                        )
                    )
            if record and pending and len(plugin.conversation.pending) < 16:
                key = scope, binding[1], source
                plugin.conversation.pending.add(key)
                plugin.track(
                    plugin.conversation._caption(
                        event, key, pending, media=copy.deepcopy(media)
                    )
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A diagnostic storage failure must never turn a completed platform
            # send into a retry of the actual message.
            logger.warning("自己的图片上下文登记失败 (%s)", type(exc).__name__)

    async def capture_native(self, event, images, *, meme=False):
        plugin = self.plugin
        try:
            scope, persona, _ = await plugin.resolve_scope(event)
            if scope != event.get_extra(
                "ovenheart_reply_scope"
            ) or not await plugin.permitted(event):
                return
            seen = event.get_extra("ovenheart_outgoing_media_seen", set())
            media, payloads, captured = [], [], []
            hints = event.get_extra("ovenheart_explicit_image_hints", {})
            meme_hints = event.get_extra("ovenheart_outgoing_meme_hints", [])
            for image in images[:4]:
                if id(image) in seen:
                    continue
                caption = hints.get(id(image), "")
                if not caption and meme and len(meme_hints) == 1:
                    caption = meme_hints[0]["caption"]
                item = {"type": "image", "status": "ready" if caption else "available"}
                if caption:
                    item |= {
                        "text": caption,
                        "is_emoji": True,
                        "description_source": "selected_emoji",
                    }
                encoded = await asyncio.wait_for(image.convert_to_base64(), 3)
                if len(encoded) > 14 * 1024 * 1024:
                    continue
                payloads.append(base64.b64decode(encoded, validate=True))
                media.append(item)
                captured.append(id(image))
            if not media:
                return
            source = "bot-media:" + uuid4().hex
            window = plugin.participation.windows.get(scope)
            if window:
                window["sequence"] += 1
                window["messages"].append(
                    {
                        "sequence": window["sequence"],
                        "source": source,
                        "sender": "机器人（自己）",
                        "sender_id": event.get_self_id(),
                        "message_id": "",
                        "self": True,
                        "text": "",
                        "media": media,
                        "time": time.time(),
                    }
                )
            seen.update(captured)
            event.set_extra("ovenheart_outgoing_media_seen", seen)
            if plugin.settings["image_memory_enabled"]:
                plugin.track(
                    plugin.image_memory.sent(
                        event, scope, persona, source, payloads, media
                    )
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("原生已发送图片上下文登记失败 (%s)", type(exc).__name__)
