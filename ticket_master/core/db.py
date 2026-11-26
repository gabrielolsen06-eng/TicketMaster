"""Database connection and migrations."""

from __future__ import annotations

import os
import sqlite3
import sys
from importlib import resources
from pathlib import Path


def default_home() -> Path:
    """Folder holding the database and backups. Override with TICKET_MASTER_HOME."""
    env = os.environ.get("TICKET_MASTER_HOME")
    if env:
        return Path(env).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "TicketMaster"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "TicketMaster"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "ticket-master"


def connect(path: Path | str) -> sqlite3.Connection:
    """Open the database in WAL mode so the app and the MCP server can share it."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _migration_files() -> list[tuple[int, str]]:
    files = []
    for entry in resources.files("ticket_master.migrations").iterdir():
        name = entry.name
        if name.endswith(".sql") and name[:3].isdigit():
            files.append((int(name[:3]), entry.read_text(encoding="utf-8")))
    return sorted(files)


def migrate(conn: sqlite3.Connection) -> int:
    """Apply any migrations not yet applied. Returns the schema version."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for number, sql in _migration_files():
        if number <= version:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in sql.split(";"):
                if statement.strip():
                    conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {number}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        version = number
    return version
