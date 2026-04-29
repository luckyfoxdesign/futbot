import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from telegram.ext import Application

log = logging.getLogger(__name__)

_TRACKED_TASKS_KEY = "tracked_background_tasks"


def create_background_task(
    app: Application,
    coro: Coroutine[Any, Any, Any],
    name: str,
) -> asyncio.Task:
    tasks = app.bot_data.setdefault(_TRACKED_TASKS_KEY, set())
    task = asyncio.create_task(coro, name=name)
    tasks.add(task)

    def _on_done(done: asyncio.Task) -> None:
        tasks.discard(done)
        try:
            done.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("background task failed: %s", name)

    task.add_done_callback(_on_done)
    return task


async def cancel_background_tasks(app: Application) -> None:
    tasks = set(app.bot_data.pop(_TRACKED_TASKS_KEY, set()))
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
