import logging
from telegram import Bot

log = logging.getLogger(__name__)


async def send_safe(bot: Bot, chat_id: int, text: str, **kwargs):
    try:
        await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except Exception:
        log.exception("Could not send message to %s", chat_id)


async def notify_cancellation(bot: Bot, user_ids: list[int], match_date: str, match_field: str):
    text = f"❌ Матч {match_date} на поле «{match_field}» отменён."
    for uid in user_ids:
        await send_safe(bot, uid, text)


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
    for uid in user_ids:
        await send_safe(bot, uid, text)


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
    for uid in admin_ids:
        await send_safe(bot, uid, text)
