import asyncio
import contextvars
from collections.abc import Awaitable, Callable
from typing import Optional, TypeVar

import aiosqlite
from . import config
from .models import Match, Registration

_MATCH_UPDATE_FIELDS = frozenset({
    "kickoff_at", "date", "weekday", "field", "time_start", "time_end",
    "price", "payment_info", "location_url", "max_players", "status",
    "message_id", "auto_closed_at",
})

_REMINDER_FIELDS = frozenset({"reminder_24h_sent_at", "reminder_2h_sent_at"})

_db: Optional[aiosqlite.Connection] = None
_db_lock: Optional[asyncio.Lock] = None
_db_lock_owned: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "db_lock_owned", default=False
)
T = TypeVar("T")


def _connection() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("Database is not initialized")
    return _db


def _lock() -> asyncio.Lock:
    if _db_lock is None:
        raise RuntimeError("Database is not initialized")
    return _db_lock


async def _run_locked(operation: Callable[[], Awaitable[T]]) -> T:
    if _db_lock_owned.get():
        return await operation()

    async with _lock():
        token = _db_lock_owned.set(True)
        try:
            return await operation()
        finally:
            _db_lock_owned.reset(token)


async def init():
    global _db, _db_lock
    _db_lock = asyncio.Lock()
    _db = await aiosqlite.connect(config.DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA foreign_keys=ON")
    await _db.executescript("""
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            message_id INTEGER,
            kickoff_at TEXT NOT NULL,
            date TEXT NOT NULL,
            weekday TEXT,
            field TEXT,
            time_start TEXT,
            time_end TEXT,
            price TEXT,
            payment_info TEXT,
            location_url TEXT,
            max_players INTEGER DEFAULT 18,
            status TEXT DEFAULT 'open',
            reminder_24h_sent_at TEXT,
            reminder_2h_sent_at TEXT,
            auto_closed_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS registrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER NOT NULL REFERENCES matches(id),
            user_id INTEGER NOT NULL,
            contact TEXT NOT NULL,
            contact_type TEXT NOT NULL,
            display_name TEXT NOT NULL,
            slot_type TEXT NOT NULL,
            slot_number INTEGER NOT NULL,
            status TEXT DEFAULT 'active',
            joined_at TEXT DEFAULT (datetime('now')),
            main_since TEXT,
            cancelled_at TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_active_registration
            ON registrations(match_id, user_id) WHERE status = 'active';
    """)
    await _db.commit()


async def close():
    if _db:
        await _db.close()


def _row_to_match(row) -> Match:
    return Match(
        id=row["id"], chat_id=row["chat_id"], message_id=row["message_id"],
        kickoff_at=row["kickoff_at"], date=row["date"], weekday=row["weekday"],
        field=row["field"], time_start=row["time_start"], time_end=row["time_end"],
        price=row["price"], payment_info=row["payment_info"], location_url=row["location_url"],
        max_players=row["max_players"], status=row["status"],
        reminder_24h_sent_at=row["reminder_24h_sent_at"],
        reminder_2h_sent_at=row["reminder_2h_sent_at"],
        auto_closed_at=row["auto_closed_at"], created_at=row["created_at"],
    )


def _row_to_reg(row) -> Registration:
    return Registration(
        id=row["id"], match_id=row["match_id"], user_id=row["user_id"],
        contact=row["contact"], contact_type=row["contact_type"],
        display_name=row["display_name"], slot_type=row["slot_type"],
        slot_number=row["slot_number"], status=row["status"],
        joined_at=row["joined_at"], main_since=row["main_since"],
        cancelled_at=row["cancelled_at"],
    )


async def create_match(chat_id: int, kickoff_at: str, date: str, weekday: str,
                       field: str, time_start: str, time_end: str, price: str,
                       payment_info: str, location_url: str, max_players: int) -> int:
    async def operation() -> int:
        conn = _connection()
        async with conn.execute(
            """INSERT INTO matches (chat_id, kickoff_at, date, weekday, field, time_start, time_end,
               price, payment_info, location_url, max_players)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (chat_id, kickoff_at, date, weekday, field, time_start, time_end,
             price, payment_info, location_url, max_players)
        ) as cur:
            await conn.commit()
            return cur.lastrowid

    return await _run_locked(operation)


async def set_match_message_id(match_id: int, message_id: int):
    async def operation() -> None:
        conn = _connection()
        await conn.execute("UPDATE matches SET message_id=? WHERE id=?", (message_id, match_id))
        await conn.commit()

    await _run_locked(operation)


async def get_match(match_id: int) -> Optional[Match]:
    async def operation() -> Optional[Match]:
        async with _connection().execute("SELECT * FROM matches WHERE id=?", (match_id,)) as cur:
            row = await cur.fetchone()
            return _row_to_match(row) if row else None

    return await _run_locked(operation)


async def get_open_matches() -> list[Match]:
    async def operation() -> list[Match]:
        async with _connection().execute("SELECT * FROM matches WHERE status='open' ORDER BY kickoff_at") as cur:
            return [_row_to_match(r) for r in await cur.fetchall()]

    return await _run_locked(operation)


async def get_all_matches(limit: int = 20) -> list[Match]:
    async def operation() -> list[Match]:
        async with _connection().execute(
            "SELECT * FROM matches ORDER BY kickoff_at DESC LIMIT ?", (limit,)
        ) as cur:
            return [_row_to_match(r) for r in await cur.fetchall()]

    return await _run_locked(operation)


async def update_match_fields(match_id: int, **fields):
    async def operation() -> None:
        invalid = set(fields) - _MATCH_UPDATE_FIELDS
        if invalid:
            raise ValueError(f"Disallowed match fields: {invalid}")
        sets = ", ".join(f"{k}=?" for k in fields)
        vals = list(fields.values()) + [match_id]
        conn = _connection()
        await conn.execute(f"UPDATE matches SET {sets} WHERE id=?", vals)
        await conn.commit()

    await _run_locked(operation)


async def get_registrations(match_id: int) -> list[Registration]:
    async def operation() -> list[Registration]:
        async with _connection().execute(
            "SELECT * FROM registrations WHERE match_id=? AND status='active' ORDER BY slot_type, slot_number",
            (match_id,)
        ) as cur:
            return [_row_to_reg(r) for r in await cur.fetchall()]

    return await _run_locked(operation)


async def get_all_active_user_ids(match_id: int) -> list[int]:
    async def operation() -> list[int]:
        async with _connection().execute(
            "SELECT user_id FROM registrations WHERE match_id=? AND status='active'",
            (match_id,)
        ) as cur:
            return [r[0] for r in await cur.fetchall()]

    return await _run_locked(operation)


async def get_active_registration(match_id: int, user_id: int) -> Optional[Registration]:
    async def operation() -> Optional[Registration]:
        async with _connection().execute(
            "SELECT * FROM registrations WHERE match_id=? AND user_id=? AND status='active'",
            (match_id, user_id)
        ) as cur:
            row = await cur.fetchone()
            return _row_to_reg(row) if row else None

    return await _run_locked(operation)


async def get_registration(reg_id: int) -> Optional[Registration]:
    async def operation() -> Optional[Registration]:
        async with _connection().execute(
            "SELECT * FROM registrations WHERE id=?", (reg_id,)
        ) as cur:
            row = await cur.fetchone()
            return _row_to_reg(row) if row else None

    return await _run_locked(operation)


async def count_main(match_id: int) -> int:
    async def operation() -> int:
        async with _connection().execute(
            "SELECT COUNT(*) FROM registrations WHERE match_id=? AND status='active' AND slot_type='main'",
            (match_id,)
        ) as cur:
            return (await cur.fetchone())[0]

    return await _run_locked(operation)


async def count_reserve(match_id: int) -> int:
    async def operation() -> int:
        async with _connection().execute(
            "SELECT COUNT(*) FROM registrations WHERE match_id=? AND status='active' AND slot_type='reserve'",
            (match_id,)
        ) as cur:
            return (await cur.fetchone())[0]

    return await _run_locked(operation)


async def next_slot_number(match_id: int, slot_type: str) -> int:
    async def operation() -> int:
        async with _connection().execute(
            "SELECT MAX(slot_number) FROM registrations WHERE match_id=? AND slot_type=?",
            (match_id, slot_type)
        ) as cur:
            row = await cur.fetchone()
            return (row[0] or 0) + 1

    return await _run_locked(operation)


async def add_registration(match_id: int, user_id: int, contact: str, contact_type: str,
                            display_name: str, slot_type: str, slot_number: int,
                            main_since: Optional[str] = None, commit: bool = True) -> int:
    async def operation() -> int:
        conn = _connection()
        async with conn.execute(
            """INSERT INTO registrations (match_id, user_id, contact, contact_type, display_name,
               slot_type, slot_number, main_since) VALUES (?,?,?,?,?,?,?,?)""",
            (match_id, user_id, contact, contact_type, display_name, slot_type, slot_number, main_since)
        ) as cur:
            if commit:
                await conn.commit()
            return cur.lastrowid

    return await _run_locked(operation)


async def cancel_registration(reg_id: int, now_iso: str, commit: bool = True):
    async def operation() -> None:
        conn = _connection()
        await conn.execute(
            "UPDATE registrations SET status='cancelled', cancelled_at=? WHERE id=?",
            (now_iso, reg_id)
        )
        if commit:
            await conn.commit()

    await _run_locked(operation)


async def get_penalty_registrations(match_id: int) -> list[Registration]:
    async def operation() -> list[Registration]:
        async with _connection().execute(
            "SELECT * FROM registrations WHERE match_id=? AND status='penalty' ORDER BY slot_number",
            (match_id,)
        ) as cur:
            return [_row_to_reg(r) for r in await cur.fetchall()]

    return await _run_locked(operation)


async def set_penalty(reg_id: int, now_iso: str, commit: bool = True):
    async def operation() -> None:
        conn = _connection()
        await conn.execute(
            "UPDATE registrations SET status='penalty', cancelled_at=? WHERE id=?",
            (now_iso, reg_id)
        )
        if commit:
            await conn.commit()

    await _run_locked(operation)


async def get_first_reserve(match_id: int) -> Optional[Registration]:
    async def operation() -> Optional[Registration]:
        async with _connection().execute(
            """SELECT * FROM registrations WHERE match_id=? AND status='active' AND slot_type='reserve'
               ORDER BY slot_number LIMIT 1""",
            (match_id,)
        ) as cur:
            row = await cur.fetchone()
            return _row_to_reg(row) if row else None

    return await _run_locked(operation)


async def promote_to_main(reg_id: int, new_slot: int, main_since: str, commit: bool = True):
    async def operation() -> None:
        conn = _connection()
        await conn.execute(
            "UPDATE registrations SET slot_type='main', slot_number=?, main_since=? WHERE id=?",
            (new_slot, main_since, reg_id)
        )
        if commit:
            await conn.commit()

    await _run_locked(operation)


async def mark_reminder_sent(match_id: int, field: str, now_iso: str):
    async def operation() -> None:
        if field not in _REMINDER_FIELDS:
            raise ValueError(f"Disallowed reminder field: {field}")
        conn = _connection()
        await conn.execute(f"UPDATE matches SET {field}=? WHERE id=?", (now_iso, match_id))
        await conn.commit()

    await _run_locked(operation)


async def fetch_matches_needing_reminder(field: str, before_iso: str) -> list[Match]:
    async def operation() -> list[Match]:
        if field not in _REMINDER_FIELDS:
            raise ValueError(f"Disallowed reminder field: {field}")
        async with _connection().execute(
            f"SELECT * FROM matches WHERE status='open' AND {field} IS NULL AND kickoff_at <= ?",
            (before_iso,)
        ) as cur:
            return [_row_to_match(r) for r in await cur.fetchall()]

    return await _run_locked(operation)


async def fetch_open_matches_before(kickoff_before_iso: str) -> list[Match]:
    async def operation() -> list[Match]:
        async with _connection().execute(
            "SELECT * FROM matches WHERE status='open' AND auto_closed_at IS NULL AND kickoff_at <= ?",
            (kickoff_before_iso,)
        ) as cur:
            return [_row_to_match(r) for r in await cur.fetchall()]

    return await _run_locked(operation)


async def execute_immediate(operation: Callable[[], Awaitable[T]]) -> T:
    """Run an operation inside BEGIN IMMEDIATE while holding the DB lock."""
    async def locked_operation() -> T:
        conn = _connection()
        await conn.execute("BEGIN IMMEDIATE")
        try:
            result = await operation()
            await conn.commit()
            return result
        except Exception:
            await conn.rollback()
            raise

    return await _run_locked(locked_operation)
