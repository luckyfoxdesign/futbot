import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters

from .. import database as db, config
from ..models import Match
from ..keyboards import match_keyboard
from ..message_builder import build_announce
from ..promotion import promote_first_reserve, notify_promoted
from .notifications import notify_penalty

log = logging.getLogger(__name__)

WAITING_CONTACT = "waiting_contact"


def _parse_id(data: str) -> int | None:
    try:
        return int(data.split("_", 1)[1])
    except (ValueError, IndexError):
        return None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


def _is_penalty_applicable(main_since_iso: str, kickoff_iso: str, tz: ZoneInfo) -> bool:
    now = _now_utc()
    kickoff = datetime.fromisoformat(kickoff_iso)
    kickoff_local = kickoff.astimezone(tz)
    noon_day_of_match = kickoff_local.replace(hour=12, minute=0, second=0, microsecond=0)
    if now < noon_day_of_match:
        return False
    main_since = datetime.fromisoformat(main_since_iso)
    return main_since < noon_day_of_match


async def _rebuild_message(app, match: Match):
    if not match.message_id:
        return
    regs = await db.get_registrations(match.id)
    text = build_announce(match, regs)
    closed = match.status != "open"
    try:
        await app.bot.edit_message_text(
            chat_id=match.chat_id,
            message_id=match.message_id,
            text=text,
            reply_markup=match_keyboard(match.id, closed=closed),
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
    except Exception:
        log.exception("Failed to edit match message %s", match.message_id)


async def handle_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    match_id = _parse_id(query.data)
    if match_id is None:
        return
    match = await db.get_match(match_id)
    if not match or match.status != "open":
        await query.answer("Запись на этот матч закрыта.", show_alert=True)
        return

    user = query.from_user

    existing = await db.get_active_registration(match_id, user.id)
    if existing:
        await query.answer("Ты уже в списке!", show_alert=True)
        return

    # Determine contact
    if user.username:
        contact = f"@{user.username}"
        contact_type = "username"
        await _do_join(context.application, match, user.id, user.full_name, contact, contact_type)
    else:
        # Need phone — send request to DM
        context.user_data[f"pending_match_{match_id}"] = {
            "match_id": match_id, "display_name": user.full_name
        }
        try:
            await context.application.bot.send_message(
                chat_id=user.id,
                text=(
                    "Чтобы записаться, нужен твой контакт.\n"
                    "Нажми кнопку ниже, чтобы поделиться номером телефона."
                ),
                reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton("📱 Поделиться контактом", request_contact=True)]],
                    resize_keyboard=True, one_time_keyboard=True,
                ),
            )
            await query.answer(
                "Напишу тебе в личку — там нужно поделиться контактом.", show_alert=True
            )
        except Exception:
            await query.answer(
                "Открой личку с ботом или поставь @username, чтобы записаться.",
                show_alert=True,
            )


async def _do_join(app, match: Match, user_id: int, display_name: str,
                   contact: str, contact_type: str, force_reserve: bool = False):
    now = _now_iso()
    await db._db.execute("BEGIN IMMEDIATE")
    try:
        main_count = await db.count_main(match.id)
        if not force_reserve and main_count < match.max_players:
            slot_type = "main"
            slot_number = await db.next_slot_number(match.id, "main")
            main_since = now
        else:
            slot_type = "reserve"
            slot_number = await db.next_slot_number(match.id, "reserve")
            main_since = None

        await db.add_registration(
            match.id, user_id, contact, contact_type, display_name,
            slot_type, slot_number, main_since, commit=False
        )
        await db._db.commit()
    except Exception:
        await db._db.rollback()
        raise

    await _rebuild_message(app, match)


async def handle_contact_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Called when user sends contact via DM."""
    contact = update.message.contact
    user = update.effective_user

    # Find pending match registrations for this user
    pending_key = None
    pending = None
    for key, val in context.user_data.items():
        if key.startswith("pending_match_"):
            pending_key = key
            pending = val
            break

    if not pending:
        await update.message.reply_text(
            "Нет активной заявки на запись.", reply_markup=ReplyKeyboardRemove()
        )
        return

    match_id = pending["match_id"]
    display_name = pending["display_name"]
    match = await db.get_match(match_id)

    if not match or match.status != "open":
        await update.message.reply_text(
            "Запись уже закрыта.", reply_markup=ReplyKeyboardRemove()
        )
        context.user_data.pop(pending_key, None)
        return

    phone = contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone

    force_reserve = pending.get("force_reserve", False)
    await _do_join(context.application, match, user.id, display_name, phone, "phone", force_reserve=force_reserve)
    context.user_data.pop(pending_key, None)

    await update.message.reply_text(
        f"✅ Готово! Ты записан на матч {match.date}, {match.weekday} — {match.field}.",
        reply_markup=ReplyKeyboardRemove(),
    )


async def handle_join_reserve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    match_id = _parse_id(query.data)
    if match_id is None:
        return
    match = await db.get_match(match_id)
    if not match or match.status != "open":
        await query.answer("Запись на этот матч закрыта.", show_alert=True)
        return

    user = query.from_user

    existing = await db.get_active_registration(match_id, user.id)
    if existing:
        await query.answer("Ты уже в списке!", show_alert=True)
        return

    if user.username:
        contact = f"@{user.username}"
        contact_type = "username"
        await _do_join(context.application, match, user.id, user.full_name, contact, contact_type, force_reserve=True)
    else:
        context.user_data[f"pending_match_{match_id}"] = {
            "match_id": match_id, "display_name": user.full_name, "force_reserve": True
        }
        try:
            await context.application.bot.send_message(
                chat_id=user.id,
                text=(
                    "Чтобы записаться в резерв, нужен твой контакт.\n"
                    "Нажми кнопку ниже, чтобы поделиться номером телефона."
                ),
                reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton("📱 Поделиться контактом", request_contact=True)]],
                    resize_keyboard=True, one_time_keyboard=True,
                ),
            )
            await query.answer(
                "Напишу тебе в личку — там нужно поделиться контактом.", show_alert=True
            )
        except Exception:
            await query.answer(
                "Открой личку с ботом или поставь @username, чтобы записаться.",
                show_alert=True,
            )


async def handle_leave_reserve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    match_id = _parse_id(query.data)
    if match_id is None:
        return
    match = await db.get_match(match_id)
    if not match:
        return

    user = query.from_user
    reg = await db.get_active_registration(match_id, user.id)
    if not reg:
        await query.answer("Тебя нет в списке.", show_alert=True)
        return

    if reg.slot_type != "reserve":
        await query.answer("Ты в основном составе, используй кнопку «Выйти».", show_alert=True)
        return

    now = _now_iso()
    await db.cancel_registration(reg.id, now)
    await _rebuild_message(context.application, match)


async def handle_leave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    match_id = _parse_id(query.data)
    if match_id is None:
        return
    match = await db.get_match(match_id)
    if not match:
        return

    user = query.from_user
    reg = await db.get_active_registration(match_id, user.id)
    if not reg:
        await query.answer("Тебя нет в списке.", show_alert=True)
        return

    now = _now_iso()
    is_main = reg.slot_type == "main"
    apply_penalty = (
        is_main
        and reg.main_since is not None
        and _is_penalty_applicable(reg.main_since, match.kickoff_at, config.TIMEZONE)
    )

    await db._db.execute("BEGIN IMMEDIATE")
    try:
        if apply_penalty:
            await db.set_penalty(reg.id, now, commit=False)
        else:
            await db.cancel_registration(reg.id, now, commit=False)

        promoted_reg = None
        if is_main:
            promoted, promoted_reg = await promote_first_reserve(match)
        await db._db.commit()
    except Exception:
        await db._db.rollback()
        raise

    if apply_penalty:
        await notify_penalty(context.application.bot, user.id, match.date, match.field)

    if is_main and promoted_reg:
        await notify_promoted(context.application, promoted_reg, match)

    await _rebuild_message(context.application, match)


