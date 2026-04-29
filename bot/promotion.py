import logging
from datetime import datetime, timezone
from . import database as db
from .models import Match, Registration

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def promote_first_reserve(match: Match) -> tuple[bool, Registration | None]:
    """
    Move the first active reserve to main slot.
    Must be called inside an open BEGIN IMMEDIATE transaction (no commit inside).
    """
    reserve = await db.get_first_reserve(match.id)
    if not reserve:
        return False, None

    main_count = await db.count_main(match.id)
    new_slot = main_count + 1
    now = _now_iso()

    await db.promote_to_main(reserve.id, new_slot, now, commit=False)

    updated = await db.get_registration(reserve.id)
    return True, updated


async def promote_reserve_to_capacity(match: Match) -> list[Registration]:
    """
    Promote active reserve players until main reaches match.max_players.
    Opens its own transaction and returns promoted registrations for notifications.
    """
    async def operation() -> list[Registration]:
        promoted: list[Registration] = []
        while await db.count_main(match.id) < match.max_players:
            did_promote, promoted_reg = await promote_first_reserve(match)
            if not did_promote or promoted_reg is None:
                break
            promoted.append(promoted_reg)
        return promoted

    return await db.execute_immediate(operation)


async def notify_promoted(app, reg: Registration, match: Match):
    try:
        await app.bot.send_message(
            chat_id=reg.user_id,
            text=f"✅ Ты в основном составе на матч {match.date}, {match.weekday} — {match.field}!",
        )
    except Exception:
        log.exception("Failed to notify promoted player %s", reg.user_id)
