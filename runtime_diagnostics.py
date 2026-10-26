"""Read coroutine locations without collecting messages or frame locals."""

import asyncio
import gc


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
                (item for item in gc.get_referents(current) if hasattr(item, "ag_frame")),
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
