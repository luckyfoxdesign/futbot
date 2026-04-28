import asyncio
import logging
from datetime import datetime, timezone, timedelta

from . import config, database as db
from .keyboards import match_keyboard
from .message_builder import build_announce
from .handlers.notifications import notify_reminder, notify_penalty_summary

log = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _offset_iso(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


async def close_match(app, match):
    await db.update_match_fields(match.id, status="closed", auto_closed_at=_utcnow_iso())
    match = await db.get_match(match.id)
    regs = await db.get_registrations(match.id)
    text = build_announce(match, regs)
    if match.message_id:
        try:
            await app.bot.edit_message_text(
                chat_id=match.chat_id,
                message_id=match.message_id,
                text=text,
                reply_markup=match_keyboard(match.id, closed=True),
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        except Exception:
            log.exception("close_match: could not edit message %s", match.message_id)

    penalty_regs = await db.get_penalty_registrations(match.id)
    await notify_penalty_summary(app.bot, config.ADMIN_IDS, match.date, match.field, penalty_regs)


async def tick(app):
    now_plus_24h = _offset_iso(24)
    now_plus_2h = _offset_iso(2)
    now_plus_30m = _offset_iso(0.5)

    for match in await db.fetch_matches_needing_reminder("reminder_24h_sent_at", now_plus_24h):
        user_ids = await db.get_all_active_user_ids(match.id)
        await notify_reminder(app.bot, user_ids, match.date, match.field, 24)
        await db.mark_reminder_sent(match.id, "reminder_24h_sent_at", _utcnow_iso())

    for match in await db.fetch_matches_needing_reminder("reminder_2h_sent_at", now_plus_2h):
        user_ids = await db.get_all_active_user_ids(match.id)
        await notify_reminder(app.bot, user_ids, match.date, match.field, 2)
        await db.mark_reminder_sent(match.id, "reminder_2h_sent_at", _utcnow_iso())

    for match in await db.fetch_open_matches_before(now_plus_30m):
        await close_match(app, match)


async def reminders_loop(app):
    while True:
        try:
            await tick(app)
        except Exception:
            log.exception("reminders tick failed")
        await asyncio.sleep(60)
