import asyncio
import logging
from urllib.parse import urlparse
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters,
)

from . import config, database as db
from .reminders import reminders_loop
from .backup import backup_loop
from .handlers.admin import (
    newmatch_handler, newmatch_dm_handler, edit_match_handler,
    editmatch, cancelmatch, closematch, restorematch, list_matches,
    handle_options, handle_options_back,
    handle_options_delete, handle_options_delete_confirm,
)
from .handlers.player import (
    handle_join, handle_leave, handle_contact_received,
    handle_join_reserve, handle_leave_reserve,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

_BACKGROUND_TASKS_KEY = "background_tasks"


async def _run_background_tasks(app: Application):
    while not app.running:
        await asyncio.sleep(0.1)

    await asyncio.gather(
        reminders_loop(app),
        backup_loop(),
    )


async def _cancel_background_tasks(app: Application):
    tasks = app.bot_data.pop(_BACKGROUND_TASKS_KEY, [])
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def post_init(app: Application):
    await db.init()
    app.bot_data[_BACKGROUND_TASKS_KEY] = [
        asyncio.create_task(_run_background_tasks(app), name="background_tasks"),
    ]


async def post_stop(app: Application):
    await _cancel_background_tasks(app)


async def post_shutdown(app: Application):
    await _cancel_background_tasks(app)
    await db.close()


def _webhook_path() -> str:
    if config.WEBHOOK_PATH:
        return config.WEBHOOK_PATH.rsplit("/", 1)[-1]

    parsed_path = urlparse(config.WEBHOOK_URL).path.rstrip("/")
    if parsed_path:
        return parsed_path.rsplit("/", 1)[-1]

    raise RuntimeError("WEBHOOK_PATH must be set when WEBHOOK_URL has no path")


def main():
    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .post_init(post_init)
        .post_stop(post_stop)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(newmatch_dm_handler())
    app.add_handler(newmatch_handler())
    app.add_handler(edit_match_handler())
    app.add_handler(CommandHandler("editmatch", editmatch))
    app.add_handler(CommandHandler("cancelmatch", cancelmatch))
    app.add_handler(CommandHandler("closematch", closematch))
    app.add_handler(CommandHandler("restorematch", restorematch))
    app.add_handler(CommandHandler("matches", list_matches))

    app.add_handler(CallbackQueryHandler(handle_join, pattern=r"^join_\d+$"))
    app.add_handler(CallbackQueryHandler(handle_leave, pattern=r"^leave_\d+$"))
    app.add_handler(CallbackQueryHandler(handle_join_reserve, pattern=r"^joinreserve_\d+$"))
    app.add_handler(CallbackQueryHandler(handle_leave_reserve, pattern=r"^leavereserve_\d+$"))
    app.add_handler(CallbackQueryHandler(handle_options, pattern=r"^options_\d+$"))
    app.add_handler(CallbackQueryHandler(handle_options_back, pattern=r"^optback_\d+$"))
    app.add_handler(CallbackQueryHandler(handle_options_delete, pattern=r"^optdelete_\d+$"))
    app.add_handler(CallbackQueryHandler(handle_options_delete_confirm, pattern=r"^optdelconfirm_\d+$"))

    app.add_handler(MessageHandler(filters.CONTACT & filters.ChatType.PRIVATE, handle_contact_received))

    if config.WEBHOOK_URL:
        log.info("Starting bot in webhook mode...")
        app.run_webhook(
            listen="0.0.0.0",
            port=config.WEBHOOK_PORT,
            url_path=_webhook_path(),
            webhook_url=config.WEBHOOK_URL,
            drop_pending_updates=True,
        )
    else:
        log.info("Starting bot in polling mode...")
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
