"""Aggregate actual helper usage without storing prompts or delaying responses."""

from __future__ import annotations

import asyncio
import time

from astrbot.api import logger


class AuxiliaryUsage:
    def __init__(self, context, store):
        self.context = context
        self.store = store
        self.pending = []
        self.lock = asyncio.Lock()
        self.dropped = 0
        self.on_record = None

    async def generate(self, stage, **kwargs):
        started = time.monotonic()
        response = None
        status = "error"
        try:
            response = await self.context.llm_generate(**kwargs)
            status = "success"
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
                    "system_chars": len(kwargs.get("system_prompt") or ""),
                    "prompt_chars": len(kwargs.get("prompt") or ""),
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
