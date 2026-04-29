import logging
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import TypeVar

from telegram import Update
from telegram.ext import ContextTypes

log = logging.getLogger(__name__)
T = TypeVar("T")


def timed_handler(
    name: str,
    handler: Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[T]],
) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[T]]:
    @wraps(handler)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE) -> T:
        started = time.perf_counter()
        status = "ok"
        try:
            return await handler(update, context)
        except Exception:
            status = "failed"
            raise
        finally:
            elapsed = time.perf_counter() - started
            level = logging.WARNING if elapsed >= 1.0 or status == "failed" else logging.INFO
            chat = update.effective_chat
            user = update.effective_user
            log.log(
                level,
                "handler=%s status=%s duration=%.3fs update_id=%s chat_id=%s user_id=%s",
                name,
                status,
                elapsed,
                update.update_id,
                chat.id if chat else None,
                user.id if user else None,
            )

    return wrapped
