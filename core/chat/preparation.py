"""Bound independent context reads to the lifetime of their native request."""

import asyncio
import time
from contextlib import asynccontextmanager
from functools import partial


@asynccontextmanager
async def context_reads(timings, **reads):
    def finished(name, started, _):
        timings[name] = round(time.monotonic() - started, 3)

    tasks = {}
    for name, read in reads.items():
        started = time.monotonic()
        # Direct task ownership also closes reads cancelled before their first step.
        task = asyncio.create_task(read)
        task.add_done_callback(partial(finished, name, started))
        tasks[name] = task
    try:
        yield tasks
    finally:
        for task in tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
