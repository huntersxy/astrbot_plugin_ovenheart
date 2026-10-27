"""Native send adapter for original semantic splitting and bounded typing delays."""

from __future__ import annotations

import asyncio
import copy

from astrbot.api import logger
from astrbot.core.message.components import At, Image, Plain, Reply

from ..vendor.maibot.response_host import bind_response
from ..vendor.maibot.response_segments import (
    ProcessedResponseSegment,
    _merge_processed_segments_to_max_count,
)
from ..vendor.maibot.response_splitter import split_text_with_llm
from ..vendor.maibot.typing_time import calculate_typing_time


class ReplyRhythm:
    def __init__(self, plugin):
        self.plugin = plugin

    def bind(self, event):
        if event.get_extra("ovenheart_reply_rhythm_bound"):
            return
        original = event.send

        async def send(message):
            settings = self.plugin.settings
            plan = event.get_extra("ovenheart_decision")
            native = (
                self.plugin.context.get_config(event.unified_msg_origin)
                .get("platform_settings", {})
                .get("segmented_reply", {})
            )
            if not plan or not settings["reply_rhythm_enabled"] or native.get("enable"):
                return await original(message)
            parts = message.chain
            if any(not isinstance(part, (At, Reply, Plain, Image)) for part in parts):
                return await original(message)
            texts = [part.text for part in parts if isinstance(part, Plain)]
            image_only = (
                bool(parts)
                and not texts
                and any(isinstance(part, Image) for part in parts)
            )
            if len(texts) != 1 or any(isinstance(part, Image) for part in parts):
                if image_only:
                    await self.pause(event, "", True)
                return await original(message)
            text = texts[0]
            sentences = [text]
            if len(text) >= 40:

                async def generate(prompt):
                    provider = (
                        settings["reply_split_provider_id"]
                        or settings["planner_provider_id"]
                        or await self.plugin.context.get_current_chat_provider_id(
                            event.unified_msg_origin
                        )
                    )
                    response = await self.plugin.auxiliary.generate(
                        "reply_split", chat_provider_id=provider, prompt=prompt
                    )
                    if getattr(response, "role", "") == "err":
                        raise ValueError("断句模型返回了错误响应。")
                    return response.completion_text

                try:
                    with bind_response(generate, settings["reply_typing_speed"]):
                        split = await asyncio.wait_for(
                            split_text_with_llm(text), settings["planner_timeout"]
                        )
                    segments = _merge_processed_segments_to_max_count(
                        [
                            ProcessedResponseSegment(part, separator=separator)
                            for part, separator in split
                            if part.strip()
                        ],
                        settings["reply_split_max_messages"],
                    )
                    sentences = [segment.text for segment in segments] or [text]
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "语义断句未完成，使用原文发送 (%s)", type(exc).__name__
                    )
            result = None
            sent = event.get_extra("ovenheart_actual_text_segments", [])
            for index, sentence in enumerate(sentences):
                await self.pause(event, sentence, False)
                chain = copy.copy(message)
                chain.chain = [
                    *[
                        part
                        for part in parts
                        if index == 0 and isinstance(part, (At, Reply))
                    ],
                    Plain(sentence),
                ]
                try:
                    result = await original(chain)
                    if result is False:
                        event.set_extra("ovenheart_text_delivery_incomplete", True)
                        break
                except BaseException:
                    event.set_extra("ovenheart_text_delivery_incomplete", True)
                    raise
                sent.append(sentence)
                event.set_extra("ovenheart_actual_text_segments", sent)
            return result

        event.send = send
        event.set_extra("ovenheart_reply_rhythm_bound", True)

    async def pause(self, event, text, image):
        with bind_response(None, self.plugin.settings["reply_typing_speed"]):
            delay = (
                min(5.0, calculate_typing_time(text, is_image=image))
                if self.plugin.settings["reply_typing_speed"] > 0
                else 0
            )
        if delay > 0:
            await asyncio.sleep(delay)
        if not await self.plugin.check_participation(event):
            raise asyncio.CancelledError
