"""Daily database backups, keeping the last N days (default 14)."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ticket_master.core.service import TicketMaster


def backup(tm: "TicketMaster") -> Path | None:
    """Copy the database to backups/tickets-YYYY-MM-DD.db and prune old copies."""
    if tm.path is None:  # in-memory database, nothing to back up
        return None
    folder = tm.path.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"tickets-{tm.today().isoformat()}.db"
    dest = sqlite3.connect(str(target))
    try:
        tm.conn.backup(dest)
    finally:
        dest.close()
    keep = int(tm.get_settings()["backup_keep_days"])
    for old in folder.glob("tickets-*.db"):
        try:
            day = date.fromisoformat(old.stem.removeprefix("tickets-"))
        except ValueError:
            continue
        if (tm.today() - day).days >= keep:
            old.unlink()
    return target
