"""Aggregate actual helper usage without storing prompts or delaying responses."""

from __future__ import annotations

import asyncio
import copy
import time

from astrbot.api import logger
from astrbot.core.agent.message import Message, TextPart


class AuxiliaryUsage:
    def __init__(self, context, store):
        self.context = context
        self.store = store
        self.pending = []
        self.lock = asyncio.Lock()
        self.dropped = 0
        self.on_record = None

    async def generate(self, stage, **kwargs):
        return await self.measure(stage, self.context.llm_generate, kwargs)

    async def tool_loop(self, stage, **kwargs):
        """Meter each native transport call without changing the shared provider."""
        from astrbot.core.star.context import Context

        context = copy.copy(self.context)
        context.provider_manager = MeteredProviderManager(
            self.context.provider_manager, self, stage, kwargs["chat_provider_id"]
        )
        return await Context.tool_loop_agent(context, **kwargs)

    async def measure(self, stage, invoke, kwargs):
        started = time.monotonic()
        response = None
        status = "error"
        try:
            response = await invoke(**kwargs)
            status = "error" if getattr(response, "role", "") == "err" else "success"
            return response
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        finally:
            usage = getattr(response, "usage", None)
            raw = getattr(response, "raw_completion", None)
            model = (
                raw.get("model", "")
                if isinstance(raw, dict)
                else getattr(raw, "model", "")
            )
            system_chars = len(kwargs.get("system_prompt") or "")
            prompt_chars = len(kwargs.get("prompt") or "")
            for message in kwargs.get("contexts") or []:
                content = (
                    message.content
                    if isinstance(message, Message)
                    else message.get("content")
                ) or ""
                role = (
                    message.role
                    if isinstance(message, Message)
                    else message.get("role")
                )
                length = (
                    len(content)
                    if isinstance(content, str)
                    else sum(
                        len(
                            part.text
                            if isinstance(part, TextPart)
                            else part.get("text") or ""
                        )
                        for part in content
                        if isinstance(part, (TextPart, dict))
                    )
                )
                if role == "system":
                    system_chars += length
                else:
                    prompt_chars += length
            self.pending.append(
                {
                    "bucket": int(time.time() // 3600) * 3600,
                    "stage": stage,
                    "provider": str(kwargs.get("chat_provider_id") or ""),
                    "model": str(model or ""),
                    "calls": 1,
                    "successes": int(status == "success"),
                    "errors": int(status == "error"),
                    "cancelled": int(status == "cancelled"),
                    "reported": int(usage is not None),
                    "input_cached": int(getattr(usage, "input_cached", 0) or 0),
                    "input_other": int(getattr(usage, "input_other", 0) or 0),
                    "output": int(getattr(usage, "output", 0) or 0),
                    "system_chars": system_chars,
                    "prompt_chars": prompt_chars,
                    "seconds": round(time.monotonic() - started, 3),
                }
            )
            if len(self.pending) > 512:
                self.pending.pop(0)
                self.dropped += 1
            if self.on_record is not None:
                self.on_record()

    async def flush(self):
        async with self.lock:
            while self.pending:
                rows, self.pending = self.pending, []
                write = asyncio.create_task(self.store.call("auxiliary_usage", rows))
                try:
                    await asyncio.shield(write)
                except asyncio.CancelledError:
                    try:
                        await write
                    except Exception:
                        self.restore(rows)
                    raise
                except Exception as exc:
                    # A completed model response never waits for this background write.
                    self.restore(rows)
                    logger.warning(
                        "辅助用量统计暂未写入，将稍后重试 (%s)", type(exc).__name__
                    )
                    return

    def restore(self, rows):
        retry = rows + self.pending
        self.dropped += max(0, len(retry) - 512)
        self.pending = retry[-512:]


class MeteredProviderManager:
    """A single loop's native Context delegates everything except its transport."""

    def __init__(self, manager, usage, stage, provider_id):
        self.manager = manager
        self.usage = usage
        self.stage = stage
        self.provider_id = provider_id

    def __getattr__(self, name):
        return getattr(self.manager, name)

    async def get_provider_by_id(self, identifier):
        provider = await self.manager.get_provider_by_id(identifier)
        if provider is None:
            return None
        # The wrapper is scoped to this Context. Calls still execute on the
        # original client; no global provider method/config/lifecycle is patched.
        wrapped = copy.copy(provider)

        # chat_provider_id belongs to metering, not the provider transport.
        async def invoke(**kwargs):
            kwargs.pop("chat_provider_id", None)
            return await provider.text_chat(**kwargs)

        async def measured(**kwargs):
            return await self.usage.measure(
                self.stage, invoke, kwargs | {"chat_provider_id": self.provider_id}
            )

        wrapped.text_chat = measured
        return wrapped
