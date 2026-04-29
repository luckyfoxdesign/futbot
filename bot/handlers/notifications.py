import asyncio
import logging
from telegram import Bot
from telegram.error import RetryAfter

log = logging.getLogger(__name__)

_SEND_CONCURRENCY = 5


def _retry_delay(exc: RetryAfter) -> float:
    retry_after = exc.retry_after
    if hasattr(retry_after, "total_seconds"):
        return retry_after.total_seconds()
    return float(retry_after)


async def send_safe(bot: Bot, chat_id: int, text: str, **kwargs):
    try:
        await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except RetryAfter as exc:
        delay = _retry_delay(exc)
        log.warning("Telegram rate limit for %s; retrying after %.1fs", chat_id, delay)
        await asyncio.sleep(delay)
        try:
            await bot.send_message(chat_id=chat_id, text=text, **kwargs)
        except Exception:
            log.exception("Could not send message to %s after retry", chat_id)
    except Exception:
        log.exception("Could not send message to %s", chat_id)


async def send_many_safe(bot: Bot, chat_ids, text: str, **kwargs):
    semaphore = asyncio.Semaphore(_SEND_CONCURRENCY)

    async def send_one(chat_id: int):
        async with semaphore:
            await send_safe(bot, chat_id, text, **kwargs)

    await asyncio.gather(*(send_one(uid) for uid in chat_ids))


async def notify_cancellation(bot: Bot, user_ids: list[int], match_date: str, match_field: str):
    text = f"❌ Матч {match_date} на поле «{match_field}» отменён."
    await send_many_safe(bot, user_ids, text)


def _plural_hours(n: int) -> str:
    last2 = n % 100
    last1 = n % 10
    if 11 <= last2 <= 14:
        return "часов"
    if last1 == 1:
        return "час"
    if 2 <= last1 <= 4:
        return "часа"
    return "часов"


async def notify_reminder(bot: Bot, user_ids: list[int], match_date: str, match_field: str, hours: int):
    text = f"⏰ Напоминание: матч {match_date} на поле «{match_field}» — через {hours} {_plural_hours(hours)}!"
    await send_many_safe(bot, user_ids, text)


async def notify_penalty(bot: Bot, user_id: int, match_date: str, match_field: str):
    await send_safe(
        bot, user_id,
        f"⚠️ Ты вышел из основного состава после 12:00 в день игры ({match_date}, «{match_field}»).\n"
        f"По правилам это считается участием — пожалуйста, переведи оплату организатору."
    )


async def notify_penalty_summary(bot: Bot, admin_ids: set[int], match_date: str, match_field: str,
                                  penalty_regs: list) -> None:
    if not penalty_regs:
        return
    lines = "\n".join(
        f"• {r.display_name} {r.contact}" for r in penalty_regs
    )
    text = (
        f"💰 Штрафники после матча {match_date}, «{match_field}»:\n\n"
        f"{lines}\n\n"
        f"Эти игроки вышли после 12:00 — должны оплатить участие."
    )
    await send_many_safe(bot, admin_ids, text)
