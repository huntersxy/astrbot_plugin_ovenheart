"""Finish stopped native generators before their message trace ends."""

from collections.abc import AsyncGenerator


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
            for index in range(from_stage, len(scheduler.stages)):
                source = scheduler.stages[index].process(event)
                if isinstance(source, AsyncGenerator):
                    try:
                        async for _ in source:
                            if event.is_stopped():
                                break
                            await scheduler._process_stages(event, index + 1)
                            if event.is_stopped():
                                break
                        if event.is_stopped() and event.get_extra(
                            "ovenheart_native_task"
                        ):
                            # Resume the onion's post-yield work, including
                            # agent/handler completion and native history commit.
                            # aclose() alone injects GeneratorExit into the host.
                            async for _ in source:
                                pass
                    finally:
                        if event.get_extra("ovenheart_native_task"):
                            await source.aclose()
                else:
                    await source
                if event.is_stopped() and (
                    not isinstance(source, AsyncGenerator)
                    or event.get_extra("ovenheart_native_task")
                ):
                    return

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
