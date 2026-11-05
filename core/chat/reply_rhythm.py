"""Reuse host word splitting, optional MaiBot semantics, and bounded send delays."""

from __future__ import annotations

import asyncio
import copy
import random
import re

from astrbot.api import logger
from astrbot.core.message.components import At, Image, Plain, Reply

from ..platform_capabilities import claim_reply_slot, reply_segment_limit
from ..vendor.maibot.response_host import bind_response
from ..vendor.maibot.response_segments import (
    ProcessedResponseSegment,
    _merge_processed_segments_to_max_count,
)
from ..vendor.maibot.response_splitter import split_text_with_llm
from ..vendor.maibot.typing_time import calculate_typing_time
from .reply_boundary import DSML_START, clean_reply, clean_reply_stream

# One QQ bubble should not keep model/merge paragraph breaks; blank lines look
# like a second “segment” inside the same gray box after max-count compression.
_BUBBLE_NEWLINE = re.compile(r"[\t\x0b\x0c\r ]*\n[\t\x0b\x0c\r ]*")


# NapCat drops packets that leave within the same second; the bubble pace keeps
# this floor even when the persona typing simulation is switched off.
_BUBBLE_MIN_INTERVAL = 0.8
_BUBBLE_JITTER = 0.4
# Transient platform failures worth another attempt. Anything else (bad
# payload, permission) would fail again and only delay the error log.
_SEND_RETRY_DELAYS = (1.0, 2.0)
_RETRYABLE_SEND_ERRORS = frozenset(
    {
        "ActionFailed",
        "ApiNotAvailable",
        "ConnectionError",
        "NetworkError",
        "OSError",
        "ReadTimeout",
        "TimeoutError",
        "WriteTimeout",
    }
)


def retryable_send_error(exc: BaseException) -> bool:
    """Whether a failed send is worth retrying."""
    if isinstance(exc, (asyncio.TimeoutError, OSError)):
        return True
    return type(exc).__name__ in _RETRYABLE_SEND_ERRORS


def finalize_bubble_text(text: str) -> str:
    """Flatten newlines left inside a single outgoing rhythm bubble."""
    return _BUBBLE_NEWLINE.sub("", text).strip()


class ReplyRhythm:
    def __init__(self, plugin):
        self.plugin = plugin

    def bind(self, event):
        if event.get_extra("ovenheart_reply_rhythm_bound"):
            return
        original = event.send
        original_streaming = event.send_streaming

        async def deliver(message):
            if not claim_reply_slot(event):
                logger.warning("QQ 官方机器人本条消息的回复次数已达到平台上限。")
                return False
            return await self.deliver_with_retry(event, original, message)

        async def deliver_stream(stream, use_fallback=False):
            if not claim_reply_slot(event):
                if hasattr(stream, "aclose"):
                    await stream.aclose()
                logger.warning("QQ 官方机器人本条消息的回复次数已达到平台上限。")
                return False
            guarded = clean_reply_stream(stream)
            try:
                return await original_streaming(guarded, use_fallback)
            finally:
                await guarded.aclose()

        async def send(message):
            if message is not None and any(
                isinstance(part, Plain) and DSML_START.search(part.text)
                for part in message.chain
            ):
                chain, blocked = [], False
                for part in message.chain:
                    if isinstance(part, Plain):
                        if blocked:
                            continue
                        blocked = bool(DSML_START.search(part.text))
                        text = clean_reply(part.text)
                        if text:
                            chain.append(Plain(text))
                    else:
                        chain.append(part)
                if message.chain and not any(
                    not isinstance(part, (At, Reply)) for part in chain
                ):
                    return False
                message.chain = chain
            settings = self.plugin.settings
            plan = event.get_extra("ovenheart_decision")
            native = (
                self.plugin.context.get_config(event.unified_msg_origin)
                .get("platform_settings", {})
                .get("segmented_reply", {})
            )
            if event.get_extra("oven_multi_segmented"):
                if plan and not await self.plugin.check_participation(event):
                    raise asyncio.CancelledError
                return await deliver(message)
            if not settings["reply_rhythm_enabled"] or native.get("enable"):
                return await deliver(message)
            if not plan:
                result = event.get_result()
                if (
                    result is None
                    or not result.is_llm_result()
                    or message.chain is not result.chain
                    or not settings["apply_enabled"]
                    or not await self.plugin.permitted(event)
                ):
                    return await deliver(message)
                scope, _, _ = await self.plugin.resolve_scope(event)
                profile = await self.plugin.store.read("scope_profile", scope)
                if not profile or not profile["profile"]["apply_enabled"]:
                    return await deliver(message)
            parts = message.chain
            if any(not isinstance(part, (At, Reply, Plain, Image)) for part in parts):
                return await deliver(message)
            texts = [part.text for part in parts if isinstance(part, Plain)]
            image_only = (
                bool(parts)
                and not texts
                and any(isinstance(part, Image) for part in parts)
            )
            if len(texts) != 1 or any(isinstance(part, Image) for part in parts):
                if image_only:
                    await self.pause(event, "", True)
                return await deliver(message)
            sentences = await self.split(event, texts[0], native)
            result = None
            sent = event.get_extra("ovenheart_actual_text_segments", [])
            delivered = 0
            for sentence in sentences:
                sentence = finalize_bubble_text(sentence)
                if not sentence:
                    continue
                if delivered:
                    await self.pause(event, sentence, False)
                elif plan and not await self.plugin.check_participation(event):
                    raise asyncio.CancelledError
                chain = copy.copy(message)
                chain.chain = [
                    *[
                        part
                        for part in parts
                        if delivered == 0 and isinstance(part, (At, Reply))
                    ],
                    Plain(sentence),
                ]
                try:
                    result = await deliver(chain)
                    if result is False:
                        event.set_extra("ovenheart_text_delivery_incomplete", True)
                        break
                except BaseException:
                    event.set_extra("ovenheart_text_delivery_incomplete", True)
                    raise
                sent.append(sentence)
                event.set_extra("ovenheart_actual_text_segments", sent)
                delivered += 1
            return result

        event.send = send
        event.send_streaming = deliver_stream
        event.set_extra("ovenheart_reply_rhythm_bound", True)

    async def deliver_with_retry(self, event, sender, message):
        """Send one bubble, retrying failures the platform may recover from.

        NapCat answers ActionFailed (retcode 1200) when its kernel drops
        sendMsg; the bubble never reaches the group, so retrying shortly after
        is the only way to keep the reply.  Non-transient errors are logged and
        swallowed here: the caller only needs to know that the send failed, and
        the flag lets the remaining bubbles be skipped.
        """
        attempts = len(_SEND_RETRY_DELAYS) + 1
        for attempt in range(1, attempts + 1):
            try:
                return await sender(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                if attempt < attempts and retryable_send_error(exc):
                    delay = _SEND_RETRY_DELAYS[attempt - 1]
                    logger.warning(
                        "发送失败，%.1fs 后重试（%s，umo=%s）",
                        delay,
                        type(exc).__name__,
                        event.unified_msg_origin,
                    )
                    await asyncio.sleep(delay)
                    continue
                event.set_extra("ovenheart_text_delivery_incomplete", True)
                logger.error(
                    "发送失败，放弃本条消息（%s，umo=%s）",
                    type(exc).__name__,
                    event.unified_msg_origin,
                )
                return False
        return False

    async def split(self, event, text, native):
        settings = self.plugin.settings
        max_messages = reply_segment_limit(
            event, settings["reply_split_max_messages"]
        )
        if (
            max_messages == 1
            or len(text.strip()) < 2
            or re.search(
                r"```|~~~|\$\$|^[ \t]*(?:[-*+]|\d+[.)、])[ \t]+",
                text,
                re.MULTILINE,
            )
        ):
            return [text]

        from astrbot.core.pipeline.result_decorate.stage import ResultDecorateStage

        stage = ResultDecorateStage()
        stage.split_words = list(
            dict.fromkeys(
                [*native.get("split_words", ["。", "？", "！", "~", "…"]), "\n"]
            )
        )
        stage.split_words_pattern = re.compile(
            f"(.*?({'|'.join(sorted(map(re.escape, stage.split_words), key=len, reverse=True))})|.+$)",
            re.DOTALL,
        )
        chunks = stage._split_text_by_words(text)
        # The host removes delimiters. Recover its boundaries in the original
        # text so punctuation and whitespace survive both splitting and merging.
        starts = []
        position = 0
        for chunk in chunks:
            start = text.find(chunk, position)
            if start < 0:
                return [text]
            starts.append(start)
            position = start + len(chunk)
        # Keep only boundaries outside a complete bracket/quote/kaomoji span.
        # A protected fragment must not disable splitting the rest of the reply.
        pairs = {
            "(": ")",
            "（": "）",
            "[": "]",
            "【": "】",
            "{": "}",
            "“": "”",
            "‘": "’",
            "「": "」",
            "『": "』",
            '"': '"',
            "`": "`",
        }
        quotes = {'"', "“", "‘", "「", "『", "`"}
        stack, protected = [], []
        for position, character in enumerate(text):
            if stack and character == pairs[stack[-1][0]]:
                _, start = stack.pop()
                if not stack:
                    protected.append((start, position + 1))
            elif stack and stack[-1][0] in quotes:
                continue
            elif character in pairs:
                stack.append((character, position))
        if stack:
            protected.append((stack[0][1], len(text)))
        boundaries = [
            0,
            *(
                start
                for start in starts[1:]
                if not any(left < start < right for left, right in protected)
            ),
            len(text),
        ]
        segments = []
        for start, end in zip(boundaries, boundaries[1:], strict=False):
            piece = text[start:end].strip()
            if piece:
                segments.append(ProcessedResponseSegment(piece))
        if settings["reply_split_mode"] == "llm" and (len(segments) > 1 or not protected):

            async def generate(prompt):
                provider = await self.plugin.auxiliary.resolve_provider_id(
                    settings, "reply_split_provider_id", event.unified_msg_origin
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
                        split_text_with_llm(text), settings["reply_split_timeout"]
                    )
                semantic_segments = [
                    ProcessedResponseSegment(part, separator=separator)
                    for part, separator in split
                    if part.strip()
                ]
                # Semantic cuts must obey the same protected spans as rules.
                position = 0
                for segment in semantic_segments[:-1]:
                    end = text.find(segment.text, position) + len(segment.text)
                    if any(left < end < right for left, right in protected):
                        raise ValueError("语义断句切入了括号或引用内部。")
                    position = end
                segments = semantic_segments
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "语义断句未完成，使用本地规则分段 (%s)", type(exc).__name__
                )
        merged = _merge_processed_segments_to_max_count(
            segments, max_messages
        )
        bubbles = [
            finalize_bubble_text(segment.text)
            for segment in merged
            if finalize_bubble_text(segment.text)
        ]
        return bubbles or [finalize_bubble_text(text) or text]

    async def pause(self, event, text, image):
        with bind_response(None, self.plugin.settings["reply_typing_speed"]):
            delay = (
                min(5.0, calculate_typing_time(text, is_image=image))
                if self.plugin.settings["reply_typing_speed"] > 0
                else 0
            )
        # The floor is a platform limit rather than a persona setting: a
        # burst of bubbles within one second is what NapCat rejects.
        delay = max(delay, _BUBBLE_MIN_INTERVAL + random.uniform(0, _BUBBLE_JITTER))
        await asyncio.sleep(delay)
        if event.is_stopped() or (
            event.get_extra("ovenheart_decision")
            and not await self.plugin.check_participation(event)
        ):
            raise asyncio.CancelledError
