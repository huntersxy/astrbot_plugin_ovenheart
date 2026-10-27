"""Read coroutine locations without collecting messages or frame locals."""

import asyncio
import gc
import sys
from pathlib import Path
from types import AsyncGeneratorType

from astrbot.api import logger


def await_path(current):
    chain = []
    for _ in range(48):
        frame = getattr(current, "cr_frame", None) or getattr(current, "ag_frame", None)
        if frame is not None:
            filename = frame.f_code.co_filename.replace("\\", "/")
            chain.append(
                f"{'/'.join(filename.split('/')[-4:])}:"
                f"{frame.f_lineno}:{frame.f_code.co_name}"
            )
        current = (
            getattr(current, "cr_await", None)
            or getattr(current, "ag_await", None)
            or next(
                (
                    item
                    for item in gc.get_referents(current)
                    if hasattr(item, "ag_frame")
                ),
                None,
            )
        )
        if current is None:
            break
    return chain


async def runtime_paths():
    paths = {}
    for task in asyncio.all_tasks():
        if task is asyncio.current_task():
            continue
        key = tuple(await_path(task.get_coro()))
        paths[key] = paths.get(key, 0) + 1
    return [
        {"count": count, "await_path": list(path)}
        for path, count in sorted(paths.items(), key=lambda item: -item[1])
    ]


async def close_retired_turns():
    """Release only pipelines owned by an already-closing OvenHeart instance."""
    filename = str(Path(__file__).parent.parent / "main.py").replace("\\", "/")
    current = asyncio.current_task()
    events, tasks = {}, set()
    for item in gc.get_objects():
        if not isinstance(item, AsyncGeneratorType) or item.ag_frame is None:
            continue
        frame = item.ag_frame
        if (
            frame.f_code.co_filename.replace("\\", "/") != filename
            or frame.f_code.co_name not in {"participate", "participation_request"}
            or not getattr(frame.f_locals.get("self"), "closing", False)
        ):
            continue
        event = frame.f_locals.get("event")
        if event is None:
            continue
        reference = event.get_extra("ovenheart_native_task")
        task = reference() if callable(reference) else None
        if task is current:
            continue
        events[id(event)] = event
        if task is not None and not task.done():
            tasks.add(task)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    closed, skipped = 0, 0
    # Cancel owners first: never aclose a generator that is still being advanced.
    generators = []
    for item in gc.get_objects():
        if not isinstance(item, AsyncGeneratorType) or item.ag_frame is None:
            continue
        event = item.ag_frame.f_locals.get("event")
        if event is not None and events.get(id(event)) is event:
            if item.ag_running or item.ag_await is not None:
                skipped += 1
            else:
                generators.append(item)
    for generator in generators:
        try:
            await generator.aclose()
            closed += 1
        except Exception as error:
            logger.warning("旧会话生成器清理未完成 (%s)", type(error).__name__)
            skipped += 1
    for event in events.values():
        event.get_extra().pop("ovenheart_usage_runner", None)
    return {
        "retired_events": len(events),
        "cancelled_tasks": len(tasks),
        "closed_generators": closed,
        "skipped_generators": skipped,
    }


def allocator_memory():
    """Read glibc heap counters, without walking live payload objects."""
    if sys.platform != "linux":
        return None
    import ctypes

    library = ctypes.CDLL(None)
    if not hasattr(library, "mallinfo2"):
        return None

    class MallInfo(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_size_t)
            for name in (
                "arena",
                "ordblks",
                "smblks",
                "hblks",
                "hblkhd",
                "usmblks",
                "fsmblks",
                "uordblks",
                "fordblks",
                "keepcost",
            )
        ]

    library.mallinfo2.argtypes = []
    library.mallinfo2.restype = MallInfo
    info = library.mallinfo2()
    return {
        "in_use_bytes": info.uordblks + info.hblkhd,
        "free_bytes": info.fordblks,
        "arena_bytes": info.arena,
    }


def memory_snapshot():
    process = {}
    status = Path("/proc/self/status")
    if sys.platform == "linux" and status.exists():
        for line in status.read_text().splitlines():
            key = line.split(":", 1)[0]
            if key in {"VmRSS", "VmSwap", "RssAnon", "RssFile", "Threads"}:
                process[key] = int(line.split()[1])
    return {
        "process_kib": process,
        "allocator": allocator_memory(),
        "gc_counts": gc.get_count(),
    }


def limit_metadata_cache(metadata):
    """Apply the same per-thread budget to room and person ledger connections."""
    manager = metadata._connection_manager
    if getattr(manager, "_ovenheart_cache_limited", False):
        return
    create = manager._create_connection

    def create_connection():
        connection = create()
        connection.execute("PRAGMA cache_size=-8192")
        return connection

    with manager._lock:
        manager._create_connection = create_connection
        for connection in manager._connections.values():
            connection.execute("PRAGMA cache_size=-8192")
            connection.execute("PRAGMA shrink_memory")
        manager._ovenheart_cache_limited = True


def reclaim_memory(*, collect=False):
    """Return freed heap pages after disposal; never drop active data or caches."""
    before = memory_snapshot()
    collected = gc.collect() if collect else 0
    available = allocator_memory()
    trimmed = False
    if available is not None and available["free_bytes"] >= 64 * 1024 * 1024:
        import ctypes

        library = ctypes.CDLL(None)
        if hasattr(library, "malloc_trim"):
            library.malloc_trim.argtypes = [ctypes.c_size_t]
            library.malloc_trim.restype = ctypes.c_int
            trimmed = bool(library.malloc_trim(0))
    return {
        "before": before,
        "after": memory_snapshot(),
        "collected": collected,
        "trimmed": trimmed,
    }
