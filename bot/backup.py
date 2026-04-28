import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

_BACKUP_DIR = Path(config.DB_PATH).parent / "backups"
_MAX_BACKUPS = 2


def _do_backup(src_path: str, dst_path: str) -> None:
    src = sqlite3.connect(src_path)
    try:
        dst = sqlite3.connect(dst_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _rotate_and_backup(src_path: str, backup_dir: Path, max_backups: int) -> str:
    backup_dir.mkdir(parents=True, exist_ok=True)

    existing = sorted(backup_dir.glob("*.db"))
    while len(existing) >= max_backups:
        oldest = existing.pop(0)
        oldest.unlink()
        log.info("backup: removed old dump %s", oldest.name)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dst = backup_dir / f"football_{ts}.db"

    try:
        _do_backup(src_path, str(dst))
    except Exception:
        dst.unlink(missing_ok=True)
        raise

    return dst.name


async def run_backup() -> None:
    name = await asyncio.to_thread(
        _rotate_and_backup, config.DB_PATH, _BACKUP_DIR, _MAX_BACKUPS
    )
    log.info("backup: created %s", name)


async def backup_loop() -> None:
    while True:
        await asyncio.sleep(86400)
        try:
            await run_backup()
        except Exception:
            log.exception("backup failed")
