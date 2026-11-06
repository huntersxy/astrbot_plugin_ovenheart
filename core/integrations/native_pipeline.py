"""Finish stopped native generators before their message trace ends."""

from collections.abc import AsyncGenerator

# Streaming completion is not a second outbound message. RespondStage ignores it.
_STREAMING_RESULTS = frozenset({"STREAMING_RESULT", "STREAMING_FINISH"})


def pending_outbound_result(event) -> bool:
    """Whether this yield still holds a message the responder has not consumed."""
    getter = getattr(event, "get_result", None)
    result = getter() if callable(getter) else None
    chain = getattr(result, "chain", None)
    if not chain:
        return False
    content_type = getattr(result, "result_content_type", None)
    return getattr(content_type, "name", "") not in _STREAMING_RESULTS


def _native_turn(event) -> bool:
    getter = getattr(event, "get_extra", None)
    return bool(callable(getter) and getter("ovenheart_native_task"))


async def drive_stages(scheduler, event, from_stage=0):
    """Run one scheduler slice, including stopped yields that still have a reply."""
    for index in range(from_stage, len(scheduler.stages)):
        source = scheduler.stages[index].process(event)
        if isinstance(source, AsyncGenerator):
            try:
                async for _ in source:
                    if defer_downstream(event):
                        break
                    await scheduler._process_stages(event, index + 1)
                    if defer_downstream(event):
                        break
                if event.is_stopped() and _native_turn(event):
                    # Resume the onion's post-yield work, including
                    # agent/handler completion and native history commit.
                    # aclose() alone injects GeneratorExit into the host.
                    # A later command yield can still carry a chain.
                    async for _ in source:
                        if defer_downstream(event):
                            continue
                        await scheduler._process_stages(event, index + 1)
            finally:
                if _native_turn(event):
                    await source.aclose()
        else:
            await source
        if (
            event.is_stopped()
            and (not isinstance(source, AsyncGenerator) or _native_turn(event))
            and defer_downstream(event)
        ):
            return


def defer_downstream(event) -> bool:
    """Skip later stages only when a stopped turn has nothing left to send.

    Slash commands call ``stop_event()`` and then yield their reply. The host
    still delivers that chain. Clearing the result is what suppresses a reply;
    a native-task pipeline must not treat the stop flag alone as "drop the yield".
    """
    stopped = (
        event.is_stopped() if callable(getattr(event, "is_stopped", None)) else False
    )
    if not stopped:
        return False
    return not (_native_turn(event) and pending_outbound_result(event))


class NativePipelineBridge:
    def __init__(self):
        self.original = None
        self.wrapper = None

    def install(self):
        from astrbot.core.pipeline.scheduler import PipelineScheduler

        if self.wrapper is not None:
            return
        self.original = PipelineScheduler._process_stages

        async def process(scheduler, event, from_stage=0):
            await drive_stages(scheduler, event, from_stage)

        self.wrapper = process
        PipelineScheduler._process_stages = process

    def disconnect(self):
        from astrbot.core.pipeline.scheduler import PipelineScheduler

        if (
            self.wrapper is not None
            and PipelineScheduler._process_stages is self.wrapper
        ):
            PipelineScheduler._process_stages = self.original
        self.original = self.wrapper = None
