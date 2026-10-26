"""Schedule incremental summaries from retained, authorized native material."""

from __future__ import annotations

import asyncio
import json
import time

import jsonschema
from astrbot.api import logger

from .learning import decode_model_object
from .room_memory import MEMORY_SCHEMA

SUMMARY_SCHEMA = {
    "type": "object",
    "required": ["summary", "entities", "relations", "facts", "evidence"],
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string", "maxLength": 2400},
        "entities": {
            "type": "array",
            "maxItems": 32,
            "items": {
                "type": "string",
                "minLength": 1,
                "maxLength": 120,
                "pattern": r"\S",
            },
        },
        "relations": MEMORY_SCHEMA["properties"]["relations"],
        # Native image occurrences are not connected yet. Do not let a model
        # fabricate image provenance through this text-only input boundary.
        "facts": {"type": "array", "maxItems": 0},
        "evidence": {
            "type": "array",
            "maxItems": 50,
            "items": {
                "type": "object",
                "required": ["source_id", "quote"],
                "additionalProperties": False,
                "properties": {
                    "source_id": {"type": "string", "minLength": 1},
                    "quote": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 500,
                        "pattern": r"\S",
                    },
                },
            },
        },
    },
}


class MemoryConsolidation:
    def __init__(self, plugin):
        self.plugin = plugin
        self.busy = {}

    async def permitted(self, event, scope, persona):
        return self.plugin.settings[
            "memory_summary_enabled"
        ] and await self.plugin.room_memory.permitted(
            event, scope, persona, learning=True
        )

    async def inputs(self, scope):
        s = self.plugin.settings
        return [
            m
            for m in await self.plugin.store.call("room_memory_inputs", scope)
            if m["sender"] not in s["blocked_senders"]
            and (not s["target_senders"] or m["sender"] in s["target_senders"])
        ]

    async def observe(self, event, scope, persona):
        if self.busy or not await self.permitted(event, scope, persona):
            return
        messages = await self.inputs(scope)
        status = await self.plugin.room_memory.call(
            scope, "summary_status", [m["source_id"] for m in messages], event=event
        )
        if (
            time.time() - status["attempted"]
            < self.plugin.settings["memory_summary_interval"]
        ):
            return
        pending = [m for m in messages if m["source_id"] not in status["processed"]][
            :50
        ]
        summarize = len(pending) >= self.plugin.settings["memory_summary_min_messages"]
        if not summarize and not status["episode_pending"]:
            return
        # Reserve before yielding again so observation and polling share a job.
        self.busy[scope] = self.plugin.track(
            self.consolidate(event, scope, persona, pending if summarize else [])
        )

    async def consolidate(self, event, scope, persona, messages):
        revision = self.plugin.settings_revision
        phase = "permissions"
        try:
            if not await self.permitted(event, scope, persona):
                return
            await self.plugin.room_memory.call(scope, "summary_attempt")
            provider_id = (
                self.plugin.settings["memory_summary_provider_id"]
                or self.plugin.settings["provider_id"]
                or await self.plugin.context.get_current_chat_provider_id(
                    event.unified_msg_origin
                )
            )
            provider = self.plugin.context.get_provider_by_id(provider_id)
            model = provider.get_model() if provider else provider_id
            host_loop = asyncio.get_running_loop()

            async def generate_native(request, metadata):
                nonlocal phase
                if (
                    revision != self.plugin.settings_revision
                    or not await self.permitted(event, scope, persona)
                ):
                    raise ValueError("摘要来源或学习权限已改变。")
                is_summary = request.request_type == "A_Memorix.ChatSummarization"
                prompt = request.prompt
                if is_summary:
                    prompt += (
                        "\nAstrBot原文来源校验：额外输出 evidence 数组，每项为 {source_id, quote}，source_id 取本轮消息方括号中的原生ID，quote 必须是该消息原文。非空摘要必须有证据；空增量 evidence=[]。本轮只提供文字素材，facts=[]。输出结构：\n"
                        + json.dumps(SUMMARY_SCHEMA, ensure_ascii=False)
                    )
                async with self.plugin.room_memory.model_window(scope):
                    phase = "summary_model" if is_summary else "episode_model"
                    response = await self.plugin.auxiliary.generate(
                        "memory_summary" if is_summary else "memory_episode",
                        chat_provider_id=provider_id,
                        prompt=prompt,
                    )
                phase = "validation"
                if (
                    revision != self.plugin.settings_revision
                    or not await self.permitted(event, scope, persona)
                ):
                    raise ValueError("摘要生成期间来源或学习权限已改变。")
                if not is_summary:
                    phase = "episode_write"
                    return response.completion_text
                payload = decode_model_object(response.completion_text)
                jsonschema.validate(payload, SUMMARY_SCHEMA)
                current = {m["source_id"]: m for m in await self.inputs(scope)}
                if any(current.get(m["source_id"]) != m for m in messages):
                    raise ValueError("摘要素材已清除或来源已撤销。")
                proofs = []
                for citation in payload["evidence"]:
                    message = next(
                        (
                            m
                            for m in messages
                            if m["source_id"] == citation["source_id"]
                        ),
                        None,
                    )
                    if message is None or citation["quote"] not in message["text"]:
                        raise ValueError("摘要证据不属于本轮真实原文。")
                    proofs.append(
                        {
                            "source_id": message["source_id"],
                            "sender": message["sender"],
                            "quote": citation["quote"],
                            "created": message["created"],
                        }
                    )
                if bool(payload["summary"].strip()) != bool(proofs):
                    raise ValueError("有增量摘要须有原文证据，空增量不可附带证据。")
                metadata.update(
                    evidence=proofs, participants=sorted({m["sender"] for m in proofs})
                )
                phase = "summary_write"
                return json.dumps(payload, ensure_ascii=False)

            async def generate(request, metadata):
                future = asyncio.run_coroutine_threadsafe(
                    generate_native(request, metadata), host_loop
                )
                return await asyncio.wrap_future(future)

            phase = "queue_or_initialize"
            result = await asyncio.wait_for(
                self.plugin.room_memory.call(
                    scope,
                    "consolidate",
                    scope,
                    messages,
                    generate,
                    str(event.get_self_id()),
                    str(model),
                    event=event,
                ),
                timeout=180,
            )
            self.plugin.debug("memory_consolidated", scope=scope, **result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "群聊记忆整理未完成，保留已提交记忆与未处理素材 (%s, phase=%s)",
                type(exc).__name__,
                phase,
            )
        finally:
            self.busy.pop(scope, None)
