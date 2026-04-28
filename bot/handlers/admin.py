import logging
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters,
)

from .. import database as db, config
from ..keyboards import match_keyboard, options_keyboard, delete_confirm_keyboard
from ..message_builder import build_announce
from ..promotion import promote_reserve_to_capacity, notify_promoted
from ..handlers.notifications import notify_cancellation

log = logging.getLogger(__name__)

NM_EDIT_FILL = "nm_edit_fill"
NM_FILL = "nm_fill"

WEEKDAYS = {
    0: "Понедельник", 1: "Вторник", 2: "Среда",
    3: "Четверг", 4: "Пятница", 5: "Суббота", 6: "Воскресенье",
}

MONTHS_RU = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4,
    "май": 5, "мая": 5, "июн": 6, "июл": 7, "август": 8,
    "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}

TEMPLATE = (
    "https://maps.app.goo.gl/...\n\n"
    "Дата: 28 апреля\n"
    "Место: Поле 2\n"
    "Стоимость: 6 евро (Наличка или Bizum)\n"
    '"Минимальный номинал для оплаты монета в 1 евро🪙"\n'
    "Время: 22:30-23:30"
)


def _is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


def _parse_id(data: str) -> int | None:
    try:
        return int(data.split("_", 1)[1])
    except (ValueError, IndexError):
        return None


def _parse_date(text: str) -> tuple[str, str] | None:
    text = text.strip()
    now_year = datetime.now(config.TIMEZONE).year

    m = re.match(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?$", text)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else now_year
        try:
            dt = datetime(year, month, day)
            return dt.strftime("%Y-%m-%d"), WEEKDAYS[dt.weekday()]
        except ValueError:
            return None

    m = re.match(r"^(\d{1,2})\s+([а-яёА-ЯЁ]+)$", text, re.IGNORECASE)
    if m:
        day = int(m.group(1))
        month_word = m.group(2).lower()
        for key, val in MONTHS_RU.items():
            if month_word.startswith(key):
                try:
                    dt = datetime(now_year, val, day)
                    return dt.strftime("%Y-%m-%d"), WEEKDAYS[dt.weekday()]
                except ValueError:
                    return None
    return None


def _parse_time(text: str) -> tuple[str, str | None] | None:
    text = text.strip()
    m = re.match(r"^(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})$", text)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"^(\d{1,2}:\d{2})$", text)
    if m:
        return m.group(1), None
    return None


def _build_kickoff_iso(date_str: str, time_start: str, tz: ZoneInfo) -> str:
    naive = datetime.strptime(f"{date_str} {time_start}", "%Y-%m-%d %H:%M")
    local = naive.replace(tzinfo=tz)
    return local.isoformat()


def _label(text: str, label: str) -> str | None:
    m = re.search(rf"^{label}\s*:\s*(.+)$", text, re.MULTILINE | re.IGNORECASE)
    return m.group(1).strip() if m else None


def _parse_template(text: str) -> dict | None:
    date_str = _label(text, "Дата")
    if not date_str:
        return None
    date_result = _parse_date(date_str)
    if not date_result:
        return None
    date, weekday = date_result

    time_str = _label(text, "Время")
    if not time_str:
        return None
    time_result = _parse_time(time_str)
    if not time_result:
        return None
    time_start, time_end = time_result

    field = _label(text, "Место")
    if not field:
        return None

    price = _label(text, "Стоимость")
    if not price:
        return None

    location_url = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("http"):
            location_url = line
            break

    note_m = re.search(r'^[""](.+)[""]$', text, re.MULTILINE)
    payment_info = note_m.group(1).strip() if note_m else None

    max_players = 18
    max_m = re.search(r"^(?:Мест|Игроков)\s*:\s*(\d+)$", text, re.MULTILINE | re.IGNORECASE)
    if max_m:
        max_players = int(max_m.group(1))

    return {
        "date": date,
        "weekday": weekday,
        "time_start": time_start,
        "time_end": time_end,
        "field": field,
        "price": price,
        "payment_info": payment_info,
        "location_url": location_url,
        "max_players": max_players,
    }


def _match_to_template(match) -> str:
    try:
        dt = datetime.strptime(match.date, "%Y-%m-%d")
        date_str = dt.strftime("%d.%m.%Y")
    except ValueError:
        date_str = match.date
    time_str = f"{match.time_start}-{match.time_end}" if match.time_end else match.time_start
    loc = match.location_url or "https://maps.app.goo.gl/..."
    parts = [loc, ""]
    parts.append(f"Дата: {date_str}")
    parts.append(f"Место: {match.field}")
    parts.append(f"Стоимость: {match.price}")
    if match.payment_info:
        parts.append(f'"{match.payment_info}"')
    parts.append(f"Время: {time_str}")
    parts.append(f"Мест: {match.max_players}")
    return "\n".join(parts)


async def _publish_match(app, data: dict) -> int:
    kickoff = _build_kickoff_iso(data["date"], data["time_start"], config.TIMEZONE)
    match_id = await db.create_match(
        chat_id=config.GROUP_CHAT_ID,
        kickoff_at=kickoff,
        date=data["date"],
        weekday=data["weekday"],
        field=data["field"],
        time_start=data["time_start"],
        time_end=data["time_end"],
        price=data["price"],
        payment_info=data["payment_info"],
        location_url=data["location_url"],
        max_players=data["max_players"],
    )
    match = await db.get_match(match_id)
    text = build_announce(match, [])
    msg = await app.bot.send_message(
        chat_id=config.GROUP_CHAT_ID,
        text=text,
        reply_markup=match_keyboard(match_id),
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )
    await db.set_match_message_id(match_id, msg.message_id)
    return match_id


async def _promote_after_capacity_increase(app, before, after):
    if after.status != "open" or after.max_players <= before.max_players:
        return

    promoted_regs = await promote_reserve_to_capacity(after)
    for reg in promoted_regs:
        await notify_promoted(app, reg, after)


# ──────────────────────────── /newmatch ────────────────────────────

async def newmatch_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return

    text = update.message.text or ""
    content = text.partition("\n")[2].strip()

    if not content:
        await update.message.reply_text(
            f"Отправь команду с шаблоном одним сообщением:\n\n/newmatch\n{TEMPLATE}"
        )
        return

    data = _parse_template(content)
    if not data:
        await update.message.reply_text(
            "Не смог разобрать. Проверь что есть:\n"
            "Дата: ...\nМесто: ...\nСтоимость: ...\nВремя: ..."
        )
        return

    match_id = await _publish_match(context.application, data)
    await update.message.reply_text(f"✅ Матч #{match_id} опубликован!")


def newmatch_handler() -> CommandHandler:
    return CommandHandler("newmatch", newmatch_start, filters=filters.ChatType.GROUPS)


# ──────────────────────────── /newmatch (DM) ────────────────────────────

async def newmatch_dm_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return ConversationHandler.END

    text = update.message.text or ""
    content = text.partition("\n")[2].strip()

    if content:
        data = _parse_template(content)
        if not data:
            await update.message.reply_text(
                "Не смог разобрать. Проверь что есть:\n"
                "Дата: ...\nМесто: ...\nСтоимость: ...\nВремя: ..."
            )
            return ConversationHandler.END
        match_id = await _publish_match(context.application, data)
        await update.message.reply_text(f"✅ Матч #{match_id} опубликован!")
        return ConversationHandler.END

    await update.message.reply_text(
        f"Пришли шаблон для нового матча:\n\n{TEMPLATE}"
    )
    return NM_FILL


async def newmatch_dm_fill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = _parse_template(update.message.text)
    if not data:
        await update.message.reply_text(
            "Не смог разобрать. Проверь что есть:\n"
            "Дата: ...\nМесто: ...\nСтоимость: ...\nВремя: ...\n\nИли /cancel"
        )
        return NM_FILL

    match_id = await _publish_match(context.application, data)
    await update.message.reply_text(f"✅ Матч #{match_id} опубликован!")
    return ConversationHandler.END


async def newmatch_dm_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Создание матча отменено.")
    return ConversationHandler.END


def newmatch_dm_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler(
            "newmatch", newmatch_dm_start, filters=filters.ChatType.PRIVATE,
        )],
        states={
            NM_FILL: [MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
                newmatch_dm_fill,
            )],
        },
        fallbacks=[CommandHandler("cancel", newmatch_dm_cancel)],
        per_chat=True,
        per_user=True,
    )


# ──────────────────────────── Options ────────────────────────────

async def handle_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _is_admin(query.from_user.id):
        await query.answer("Нет доступа.", show_alert=True)
        return
    await query.answer()
    match_id = _parse_id(query.data)
    if match_id is None:
        return
    await query.edit_message_reply_markup(options_keyboard(match_id))


async def handle_options_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _is_admin(query.from_user.id):
        await query.answer("Нет доступа.", show_alert=True)
        return
    await query.answer()
    match_id = _parse_id(query.data)
    if match_id is None:
        return
    match = await db.get_match(match_id)
    if not match:
        return
    await query.edit_message_reply_markup(
        match_keyboard(match_id, closed=(match.status != "open"))
    )


async def handle_options_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _is_admin(query.from_user.id):
        await query.answer("Нет доступа.", show_alert=True)
        return
    await query.answer()
    match_id = _parse_id(query.data)
    if match_id is None:
        return
    await query.edit_message_reply_markup(delete_confirm_keyboard(match_id))


async def handle_options_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _is_admin(query.from_user.id):
        await query.answer("Нет доступа.", show_alert=True)
        return
    await query.answer()
    match_id = _parse_id(query.data)
    if match_id is None:
        return

    match = await db.get_match(match_id)
    if not match:
        await query.answer("Матч не найден.", show_alert=True)
        return

    user_ids = await db.get_all_active_user_ids(match_id)
    await db.update_match_fields(match_id, status="cancelled")
    await notify_cancellation(context.application.bot, user_ids, match.date, match.field)

    if match.message_id:
        try:
            await context.application.bot.edit_message_text(
                chat_id=match.chat_id,
                message_id=match.message_id,
                text=f"❌ Матч #{match_id} · {match.date}, {match.weekday} — {match.field} ОТМЕНЁН.",
            )
        except Exception:
            log.exception("delete via options: could not edit message")


async def handle_options_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _is_admin(query.from_user.id):
        await query.answer("Нет доступа.", show_alert=True)
        return ConversationHandler.END
    await query.answer()

    match_id = _parse_id(query.data)
    if match_id is None:
        return ConversationHandler.END
    match = await db.get_match(match_id)
    if not match:
        return ConversationHandler.END

    context.user_data["edit_match_id"] = match_id
    current = _match_to_template(match)

    try:
        await context.application.bot.send_message(
            chat_id=query.from_user.id,
            text=f"Пришли обновлённый шаблон для матча #{match_id}:\n\n{current}",
        )
        await query.answer("Написал в личку.", show_alert=True)
    except Exception:
        await query.answer("Открой личку с ботом.", show_alert=True)
        context.user_data.pop("edit_match_id", None)
        return ConversationHandler.END

    return NM_EDIT_FILL


async def nm_edit_fill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    match_id = context.user_data.get("edit_match_id")
    if not match_id:
        return ConversationHandler.END

    data = _parse_template(update.message.text)
    if not data:
        await update.message.reply_text("Не смог разобрать. Проверь формат или /cancel")
        return NM_EDIT_FILL

    match = await db.get_match(match_id)
    if not match:
        await update.message.reply_text("Матч не найден.")
        context.user_data.pop("edit_match_id", None)
        return ConversationHandler.END

    kickoff = _build_kickoff_iso(data["date"], data["time_start"], config.TIMEZONE)
    await db.update_match_fields(
        match_id,
        kickoff_at=kickoff,
        date=data["date"],
        weekday=data["weekday"],
        field=data["field"],
        time_start=data["time_start"],
        time_end=data["time_end"],
        price=data["price"],
        payment_info=data["payment_info"],
        location_url=data["location_url"],
        max_players=data["max_players"],
    )

    updated_match = await db.get_match(match_id)
    await _promote_after_capacity_increase(context.application, match, updated_match)
    regs = await db.get_registrations(match_id)
    text = build_announce(updated_match, regs)
    try:
        await context.application.bot.edit_message_text(
            chat_id=updated_match.chat_id,
            message_id=updated_match.message_id,
            text=text,
            reply_markup=match_keyboard(match_id, closed=(updated_match.status != "open")),
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
    except Exception:
        log.exception("edit via options: could not edit message")

    context.user_data.pop("edit_match_id", None)
    await update.message.reply_text(f"✅ Матч #{match_id} обновлён.")
    return ConversationHandler.END


async def edit_match_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("edit_match_id", None)
    await update.message.reply_text("Редактирование отменено.")
    return ConversationHandler.END


def edit_match_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_options_edit, pattern=r"^optedit_\d+$")],
        states={
            NM_EDIT_FILL: [MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
                nm_edit_fill,
            )],
        },
        fallbacks=[CommandHandler("cancel", edit_match_cancel)],
        per_chat=False,
        per_user=True,
    )


# ──────────────────────────── /editmatch ────────────────────────────

async def editmatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Использование: /editmatch <id> <поле>=<значение> ...")
        return

    try:
        match_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Неверный ID матча.")
        return
    match = await db.get_match(match_id)
    if not match:
        await update.message.reply_text("Матч не найден.")
        return

    fields = {}
    for arg in args[1:]:
        if "=" in arg:
            k, _, v = arg.partition("=")
            k = k.strip().lower()
            v = v.strip()
            allowed = {"field", "time_start", "time_end", "price", "payment_info", "location_url", "max_players"}
            if k in allowed:
                fields[k] = int(v) if k == "max_players" else v

    if not fields:
        await update.message.reply_text("Нет допустимых полей для обновления.")
        return

    if "time_start" in fields:
        fields["kickoff_at"] = _build_kickoff_iso(
            match.date, fields["time_start"], config.TIMEZONE
        )

    await db.update_match_fields(match_id, **fields)
    updated_match = await db.get_match(match_id)
    await _promote_after_capacity_increase(context.application, match, updated_match)
    regs = await db.get_registrations(match_id)
    text = build_announce(updated_match, regs)
    try:
        await context.application.bot.edit_message_text(
            chat_id=updated_match.chat_id,
            message_id=updated_match.message_id,
            text=text,
            reply_markup=match_keyboard(match_id, closed=(updated_match.status != "open")),
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
    except Exception:
        log.exception("editmatch: could not edit message")
    await update.message.reply_text("✅ Матч обновлён.")


# ──────────────────────────── /cancelmatch ────────────────────────────

async def cancelmatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Использование: /cancelmatch <id>")
        return

    try:
        match_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Неверный ID матча.")
        return
    match = await db.get_match(match_id)
    if not match:
        await update.message.reply_text("Матч не найден.")
        return

    user_ids = await db.get_all_active_user_ids(match_id)
    await db.update_match_fields(match_id, status="cancelled")

    await notify_cancellation(context.application.bot, user_ids, match.date, match.field)

    if match.message_id:
        try:
            await context.application.bot.edit_message_text(
                chat_id=match.chat_id,
                message_id=match.message_id,
                text=f"❌ Матч #{match_id} · {match.date}, {match.weekday} — {match.field} ОТМЕНЁН.",
            )
        except Exception:
            log.exception("cancelmatch: could not edit message")

    await update.message.reply_text("Матч отменён, все уведомлены.")


# ──────────────────────────── /closematch ────────────────────────────

async def closematch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Использование: /closematch <id>")
        return

    try:
        match_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Неверный ID матча.")
        return
    match = await db.get_match(match_id)
    if not match:
        await update.message.reply_text("Матч не найден.")
        return

    await db.update_match_fields(match_id, status="closed")
    match = await db.get_match(match_id)
    regs = await db.get_registrations(match_id)
    text = build_announce(match, regs)

    if match.message_id:
        try:
            await context.application.bot.edit_message_text(
                chat_id=match.chat_id,
                message_id=match.message_id,
                text=text,
                reply_markup=match_keyboard(match_id, closed=True),
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        except Exception:
            log.exception("closematch: could not edit message")

    await update.message.reply_text("Запись закрыта.")


# ──────────────────────────── /restorematch ────────────────────────────

async def restorematch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Использование: /restorematch <id>")
        return

    try:
        match_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Неверный ID матча.")
        return

    match = await db.get_match(match_id)
    if not match:
        await update.message.reply_text("Матч не найден.")
        return

    if match.status != "cancelled":
        await update.message.reply_text(
            f"Матч #{match_id} не отменён (статус: {match.status}), восстановление невозможно."
        )
        return

    await db.update_match_fields(match_id, status="open")
    match = await db.get_match(match_id)
    regs = await db.get_registrations(match_id)
    text = build_announce(match, regs)

    if match.message_id:
        try:
            await context.application.bot.edit_message_text(
                chat_id=match.chat_id,
                message_id=match.message_id,
                text=text,
                reply_markup=match_keyboard(match_id),
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        except Exception:
            log.exception("restorematch: could not edit message")

    await update.message.reply_text(f"✅ Матч #{match_id} восстановлён!")


# ──────────────────────────── /matches ────────────────────────────

async def list_matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return

    matches = await db.get_all_matches()
    if not matches:
        await update.message.reply_text("Матчей нет.")
        return

    STATUS_LABEL = {"open": "открыт", "closed": "закрыт", "cancelled": "отменён"}
    lines = []
    for m in matches:
        regs = await db.get_registrations(m.id)
        main_count = sum(1 for r in regs if r.slot_type == "main")
        time_str = f"{m.time_start}–{m.time_end}" if m.time_end else m.time_start
        status = STATUS_LABEL.get(m.status, m.status)
        lines.append(
            f"#{m.id} · {m.date}, {m.weekday} · {m.field} · {time_str} "
            f"· {main_count}/{m.max_players} · {status}"
        )

    text = "Матчи (последние 20):\n" + "\n".join(lines)
    try:
        await context.application.bot.send_message(chat_id=update.effective_user.id, text=text)
    except Exception:
        await update.message.reply_text(text)
