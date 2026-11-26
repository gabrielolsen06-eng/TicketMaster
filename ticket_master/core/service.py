"""The Ticket Master core: the only code that writes to the database.

The desktop app, the MCP server and the `tm` CLI all call this class, so every
rule in the spec ("02 Workflow and rules") applies to all of them equally.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from ticket_master.core import db
from ticket_master.core.errors import (
    NotFoundError,
    RuleError,
    UndoConflictError,
    ValidationError,
)
from ticket_master.core.models import (
    CLOSED_STATUSES,
    DEFAULT_SETTINGS,
    INBOX_KEY,
    PRIORITIES,
    STARTER_PROJECTS,
    STATUSES,
    TRANSITIONS,
    Comment,
    Label,
    Project,
    Ticket,
)

_UNSET: Any = object()
_PROJECT_KEY = re.compile(r"^[A-Z]{2,6}$")
_LABEL_NAME = re.compile(r"^[a-z0-9][a-z0-9\-_ ]{0,39}$")
TICKET_FIELDS = ("title", "description", "project", "status", "priority", "due_date", "parent")


def _now_local() -> datetime:
    return datetime.now().astimezone()


def _enc(value: Any) -> str | None:
    return None if value is None else json.dumps(value)


def _dec(value: str | None) -> Any:
    return None if value is None else json.loads(value)


class TicketMaster:
    """Open (or create) a Ticket Master database.

    `actor` is recorded on every change: "user" for you, "ai:<client>" for AI tools,
    "system:housekeeping" for automatic tidying.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        actor: str = "user",
        clock: Callable[[], datetime] | None = None,
        _conn: sqlite3.Connection | None = None,
    ):
        if path is None:
            path = db.default_home() / "tickets.db"
        self.path = Path(path) if str(path) != ":memory:" else None
        self.actor = actor
        self.clock = clock or _now_local
        if _conn is not None:
            self.conn = _conn
        else:
            self.conn = db.connect(path)
            db.migrate(self.conn)
            self._seed()

    # ------------------------------------------------------------------ basics

    def as_actor(self, actor: str) -> "TicketMaster":
        """Same database, different name in the history."""
        return TicketMaster(self.path or ":memory:", actor=actor, clock=self.clock, _conn=self.conn)

    def close(self) -> None:
        self.conn.close()

    def now(self) -> str:
        return self.clock().isoformat(timespec="seconds")

    def today(self) -> date:
        return self.clock().date()

    @contextmanager
    def _tx(self) -> Iterator[str]:
        """One transaction = one change set (undone together)."""
        if self.conn.in_transaction:
            raise RuntimeError("nested transaction")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield uuid.uuid4().hex[:12]
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    def _log(
        self,
        cs: str,
        entity_type: str,
        entity_id: int,
        entity_key: str,
        action: str,
        field: str | None = None,
        old: Any = None,
        new: Any = None,
        actor: str | None = None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO history (change_set, entity_type, entity_id, entity_key, actor, action,"
            " field, old_value, new_value, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (cs, entity_type, entity_id, entity_key, actor or self.actor, action, field,
             _enc(old), _enc(new), self.now()),
        )

    def _seed(self) -> None:
        if self.conn.execute("SELECT 1 FROM projects LIMIT 1").fetchone():
            return
        with self._tx() as cs:
            for key, name, desc in STARTER_PROJECTS:
                self._insert_project(cs, key, name, desc, None, actor="system:setup")

    # ---------------------------------------------------------------- settings

    def get_settings(self) -> dict:
        row = self.conn.execute("SELECT value FROM meta WHERE key='settings'").fetchone()
        stored = json.loads(row["value"]) if row else {}
        return {**DEFAULT_SETTINGS, **stored}

    def update_settings(self, **changes: Any) -> dict:
        unknown = set(changes) - set(DEFAULT_SETTINGS)
        if unknown:
            raise ValidationError(
                f"Unknown setting(s): {', '.join(sorted(unknown))}. "
                f"Known settings: {', '.join(DEFAULT_SETTINGS)}."
            )
        for k, v in changes.items():
            if not isinstance(v, (int, float)) or v < 0:
                raise ValidationError(f"Setting {k} must be a number of 0 or more.")
        settings = {**self.get_settings(), **changes}
        self._set_meta("settings", json.dumps(settings))
        return settings

    def _get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def _set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    # ---------------------------------------------------------------- projects

    def _project_row(self, key: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM projects WHERE key=?", (str(key).upper(),)).fetchone()
        if row is None:
            known = ", ".join(p.key for p in self.list_projects())
            raise NotFoundError(f"No project with key '{key}'. Known projects: {known}.")
        return row

    @staticmethod
    def _to_project(row: sqlite3.Row) -> Project:
        return Project(row["key"], row["name"], row["description"], row["colour"], row["archived_at"])

    def list_projects(self, include_archived: bool = False) -> list[Project]:
        sql = "SELECT * FROM projects"
        if not include_archived:
            sql += " WHERE archived_at IS NULL"
        return [self._to_project(r) for r in self.conn.execute(sql + " ORDER BY id")]

    def get_project(self, key: str) -> Project:
        return self._to_project(self._project_row(key))

    def _insert_project(self, cs, key, name, description, colour, actor=None) -> int:
        cur = self.conn.execute(
            "INSERT INTO projects (key, name, description, colour, created_at) VALUES (?,?,?,?,?)",
            (key, name, description, colour, self.now()),
        )
        self._log(cs, "project", cur.lastrowid, key, "create", new={"name": name}, actor=actor)
        return cur.lastrowid

    def create_project(self, key: str, name: str, description: str = "", colour: str | None = None) -> Project:
        key = (key or "").strip().upper()
        if not _PROJECT_KEY.match(key):
            raise ValidationError("Project key must be 2 to 6 letters, e.g. 'UNI' or 'HOME'.")
        name = (name or "").strip()
        if not name:
            raise ValidationError("Project name is required.")
        if self.conn.execute("SELECT 1 FROM projects WHERE key=?", (key,)).fetchone():
            raise ValidationError(f"A project with key '{key}' already exists (it may be archived).")
        with self._tx() as cs:
            self._insert_project(cs, key, name, description or "", colour)
        return self.get_project(key)

    def update_project(self, key: str, name: Any = _UNSET, description: Any = _UNSET, colour: Any = _UNSET) -> Project:
        row = self._project_row(key)
        changes = {}
        if name is not _UNSET:
            if not (name or "").strip():
                raise ValidationError("Project name can't be empty.")
            changes["name"] = name.strip()
        if description is not _UNSET:
            changes["description"] = description or ""
        if colour is not _UNSET:
            changes["colour"] = colour
        with self._tx() as cs:
            for f, v in changes.items():
                self._set_field(cs, "project", row, f, v)
        return self.get_project(key)

    def archive_project(self, key: str) -> Project:
        """Archive a project. Its open tickets move to Inbox."""
        row = self._project_row(key)
        if row["key"] == INBOX_KEY:
            raise RuleError("The Inbox project can't be archived.")
        if row["archived_at"]:
            return self._to_project(row)
        inbox = self._project_row(INBOX_KEY)
        with self._tx() as cs:
            for t in self.conn.execute(
                "SELECT * FROM tickets WHERE project_id=? AND archived_at IS NULL AND status NOT IN (?,?)",
                (row["id"], *CLOSED_STATUSES),
            ).fetchall():
                self._set_field(cs, "ticket", t, "project", inbox["key"])
            self._set_field(cs, "project", row, "archived_at", self.now(), action="archive")
        return self.get_project(key)

    def restore_project(self, key: str) -> Project:
        row = self._project_row(key)
        if row["archived_at"]:
            with self._tx() as cs:
                self._set_field(cs, "project", row, "archived_at", None, action="restore")
        return self.get_project(key)

    # ------------------------------------------------------------------ labels

    def _label_row(self, name: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM labels WHERE name=?", (str(name).strip().lower(),)).fetchone()
        if row is None:
            known = ", ".join(l.name for l in self.list_labels()) or "none yet"
            raise NotFoundError(
                f"No label called '{name}'. Create it first with create_label. Known labels: {known}."
            )
        return row

    @staticmethod
    def _to_label(row: sqlite3.Row) -> Label:
        return Label(row["name"], row["colour"], row["archived_at"])

    def list_labels(self, include_archived: bool = False) -> list[Label]:
        sql = "SELECT * FROM labels"
        if not include_archived:
            sql += " WHERE archived_at IS NULL"
        return [self._to_label(r) for r in self.conn.execute(sql + " ORDER BY name")]

    def create_label(self, name: str, colour: str | None = None) -> Label:
        name = (name or "").strip().lower()
        if not _LABEL_NAME.match(name):
            raise ValidationError(
                "Label names are 1 to 40 characters: lowercase letters, numbers, spaces, '-' or '_'."
            )
        if self.conn.execute("SELECT 1 FROM labels WHERE name=?", (name,)).fetchone():
            raise ValidationError(f"Label '{name}' already exists (it may be archived).")
        with self._tx() as cs:
            cur = self.conn.execute(
                "INSERT INTO labels (name, colour, created_at) VALUES (?,?,?)", (name, colour, self.now())
            )
            self._log(cs, "label", cur.lastrowid, name, "create")
        return self._to_label(self._label_row(name))

    def archive_label(self, name: str) -> Label:
        row = self._label_row(name)
        if not row["archived_at"]:
            with self._tx() as cs:
                self._set_field(cs, "label", row, "archived_at", self.now(), action="archive")
        return self._to_label(self._label_row(name))

    def restore_label(self, name: str) -> Label:
        row = self._label_row(name)
        if row["archived_at"]:
            with self._tx() as cs:
                self._set_field(cs, "label", row, "archived_at", None, action="restore")
        return self._to_label(self._label_row(name))

    # ----------------------------------------------------------------- tickets

    def _ticket_row(self, key: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM tickets WHERE key=?", (str(key).strip().upper(),)).fetchone()
        if row is None:
            raise NotFoundError(f"No ticket with key '{key}'.")
        return row

    def _ticket_labels(self, ticket_id: int) -> list[str]:
        return [
            r["name"]
            for r in self.conn.execute(
                "SELECT l.name FROM labels l JOIN ticket_labels tl ON tl.label_id=l.id"
                " WHERE tl.ticket_id=? ORDER BY l.name",
                (ticket_id,),
            )
        ]

    def _flags(self, row: sqlite3.Row, settings: dict) -> list[str]:
        flags = []
        if row["archived_at"] or row["status"] in CLOSED_STATUSES:
            return flags
        today = self.today()
        if row["due_date"]:
            due = date.fromisoformat(row["due_date"])
            if due < today:
                flags.append("overdue")
            elif due <= today + timedelta(days=settings["due_soon_days"]):
                flags.append("due_soon")
        updated = datetime.fromisoformat(row["updated_at"])
        age = (self.clock() - updated).days
        if row["status"] == "In Progress" and age >= settings["stale_in_progress_days"]:
            flags.append("stale")
        if row["status"] == "Blocked" and age >= settings["stale_blocked_days"]:
            flags.append("stale")
        return flags

    def _to_ticket(self, row: sqlite3.Row, detail: bool = False, settings: dict | None = None) -> Ticket:
        settings = settings or self.get_settings()
        project = self.conn.execute("SELECT key FROM projects WHERE id=?", (row["project_id"],)).fetchone()["key"]
        parent = None
        if row["parent_id"]:
            parent = self.conn.execute("SELECT key FROM tickets WHERE id=?", (row["parent_id"],)).fetchone()["key"]
        t = Ticket(
            key=row["key"], title=row["title"], description=row["description"], project=project,
            status=row["status"], priority=row["priority"], labels=self._ticket_labels(row["id"]),
            due_date=row["due_date"], parent=parent, created_at=row["created_at"],
            updated_at=row["updated_at"], created_by=row["created_by"], archived_at=row["archived_at"],
            flags=self._flags(row, settings),
        )
        if detail:
            t.comments = [
                Comment(c["id"], c["author"], c["body"], c["created_at"])
                for c in self.conn.execute(
                    "SELECT * FROM comments WHERE ticket_id=? ORDER BY id", (row["id"],)
                )
            ]
            t.subtasks = [
                r["key"] for r in self.conn.execute(
                    "SELECT key FROM tickets WHERE parent_id=? AND archived_at IS NULL ORDER BY id", (row["id"],)
                )
            ]
        return t

    def get_ticket(self, key: str) -> Ticket:
        """Full ticket, with comments and sub-tasks."""
        return self._to_ticket(self._ticket_row(key), detail=True)

    # validation helpers

    @staticmethod
    def _check_title(title: Any) -> str:
        title = (title or "").strip() if isinstance(title, str) or title is None else None
        if not title:
            raise ValidationError("Title is required.")
        if len(title) > 200:
            raise ValidationError("Title must be 200 characters or fewer.")
        return title

    @staticmethod
    def _check_priority(priority: Any) -> str:
        for p in PRIORITIES:
            if str(priority).strip().lower() == p.lower():
                return p
        raise ValidationError(f"Unknown priority '{priority}'. Use one of: {', '.join(PRIORITIES)}.")

    @staticmethod
    def _check_status(status: Any) -> str:
        for s in STATUSES:
            if str(status).strip().lower() == s.lower():
                return s
        raise ValidationError(f"Unknown status '{status}'. Use one of: {', '.join(STATUSES)}.")

    @staticmethod
    def _check_date(value: Any) -> str | None:
        if value in (None, ""):
            return None
        if isinstance(value, date):
            return value.isoformat()
        try:
            return date.fromisoformat(str(value)).isoformat()
        except ValueError:
            raise ValidationError(f"Due date '{value}' isn't a date. Use YYYY-MM-DD.") from None

    def _check_parent(self, parent: Any, own_id: int | None = None) -> sqlite3.Row | None:
        if parent in (None, ""):
            return None
        row = self._ticket_row(parent)
        if own_id is not None and row["id"] == own_id:
            raise RuleError("A ticket can't be its own parent.")
        if row["parent_id"]:
            raise RuleError(f"{row['key']} is already a sub-task. Sub-tasks are one level deep only.")
        if own_id is not None and self.conn.execute(
            "SELECT 1 FROM tickets WHERE parent_id=? AND archived_at IS NULL", (own_id,)
        ).fetchone():
            raise RuleError("This ticket has its own sub-tasks, so it can't become a sub-task.")
        return row

    def _check_open_project(self, key: str) -> sqlite3.Row:
        row = self._project_row(key)
        if row["archived_at"]:
            raise RuleError(f"Project {row['key']} is archived. Restore it first or pick another project.")
        return row

    def _check_labels(self, names: Iterable[str]) -> list[sqlite3.Row]:
        rows = []
        for n in names:
            row = self._label_row(n)
            if row["archived_at"]:
                raise RuleError(f"Label '{row['name']}' is archived. Restore it first.")
            rows.append(row)
        return rows

    def _check_transition(self, row: sqlite3.Row, new_status: str, reason: str | None) -> None:
        old = row["status"]
        if new_status == old:
            return
        if new_status not in TRANSITIONS[old]:
            allowed = ", ".join(sorted(TRANSITIONS[old]))
            raise RuleError(f"Can't move {row['key']} from {old} to {new_status}. From {old} you can move to: {allowed}.")
        if new_status == "Blocked" and not (reason or "").strip():
            raise RuleError("Moving to Blocked needs a reason (what is it waiting on?).")
        if new_status == "Done":
            open_subs = [
                r["key"] for r in self.conn.execute(
                    "SELECT key FROM tickets WHERE parent_id=? AND archived_at IS NULL AND status NOT IN (?,?)",
                    (row["id"], *CLOSED_STATUSES),
                )
            ]
            if open_subs:
                raise RuleError(f"{row['key']} still has open sub-tasks: {', '.join(open_subs)}. Finish or cancel them first.")

    # raw writes (always logged)

    _COLUMN = {"project": "project_id", "parent": "parent_id"}
    _TABLE = {"ticket": "tickets", "project": "projects", "label": "labels"}

    def _set_field(self, cs: str, entity_type: str, row: sqlite3.Row, field: str, value: Any,
                   action: str = "update", actor: str | None = None) -> bool:
        """Set one field and log old/new. Project and parent are given as keys."""
        table = self._TABLE[entity_type]
        column = self._COLUMN.get(field, field) if entity_type == "ticket" else field
        current = self.conn.execute(f"SELECT * FROM {table} WHERE id=?", (row["id"],)).fetchone()
        old_raw = current[column]
        if entity_type == "ticket" and field == "project":
            old = self.conn.execute("SELECT key FROM projects WHERE id=?", (old_raw,)).fetchone()["key"]
            new_raw = self._project_row(value)["id"]
        elif entity_type == "ticket" and field == "parent":
            old = self.conn.execute("SELECT key FROM tickets WHERE id=?", (old_raw,)).fetchone()["key"] if old_raw else None
            new_raw = self._ticket_row(value)["id"] if value else None
        else:
            old, new_raw = old_raw, value
        if old_raw == new_raw:
            return False
        now = self.now()
        if entity_type == "ticket":
            extra = ", status_changed_at=?" if field == "status" else ""
            params = [new_raw, now] + ([now] if field == "status" else []) + [row["id"]]
            self.conn.execute(f"UPDATE tickets SET {column}=?, updated_at=?{extra} WHERE id=?", params)
        else:
            self.conn.execute(f"UPDATE {table} SET {column}=? WHERE id=?", (new_raw, row["id"]))
        key = current["key"] if entity_type != "label" else current["name"]
        self._log(cs, entity_type, row["id"], key, action, field, old, value, actor=actor)
        return True

    def _add_label(self, cs: str, ticket: sqlite3.Row, label: sqlite3.Row, actor: str | None = None) -> bool:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO ticket_labels (ticket_id, label_id) VALUES (?,?)", (ticket["id"], label["id"])
        )
        if cur.rowcount:
            self.conn.execute("UPDATE tickets SET updated_at=? WHERE id=?", (self.now(), ticket["id"]))
            self._log(cs, "ticket", ticket["id"], ticket["key"], "label_add", "label:" + label["name"],
                      None, label["name"], actor=actor)
        return bool(cur.rowcount)

    def _remove_label(self, cs: str, ticket: sqlite3.Row, label: sqlite3.Row, actor: str | None = None) -> bool:
        cur = self.conn.execute(
            "DELETE FROM ticket_labels WHERE ticket_id=? AND label_id=?", (ticket["id"], label["id"])
        )
        if cur.rowcount:
            self.conn.execute("UPDATE tickets SET updated_at=? WHERE id=?", (self.now(), ticket["id"]))
            self._log(cs, "ticket", ticket["id"], ticket["key"], "label_remove", "label:" + label["name"],
                      label["name"], None, actor=actor)
        return bool(cur.rowcount)

    def _insert_comment(self, cs: str, ticket: sqlite3.Row, body: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO comments (ticket_id, author, body, created_at) VALUES (?,?,?,?)",
            (ticket["id"], self.actor, body, self.now()),
        )
        self.conn.execute("UPDATE tickets SET updated_at=? WHERE id=?", (self.now(), ticket["id"]))
        self._log(cs, "comment", cur.lastrowid, ticket["key"], "comment", "comment", None, body)
        return cur.lastrowid

    # public ticket operations

    def create_ticket(
        self,
        title: str,
        project: str = INBOX_KEY,
        description: str = "",
        status: str = "Backlog",
        priority: str = "None",
        labels: Iterable[str] = (),
        due_date: str | date | None = None,
        parent: str | None = None,
    ) -> Ticket:
        title = self._check_title(title)
        status = self._check_status(status)
        if status in CLOSED_STATUSES or status == "Blocked":
            raise RuleError(f"New tickets can't start as {status}. Create it, then move it.")
        priority = self._check_priority(priority)
        due = self._check_date(due_date)
        proj = self._check_open_project(project or INBOX_KEY)
        label_rows = self._check_labels(labels)
        parent_row = self._check_parent(parent)
        with self._tx() as cs:
            number = proj["next_number"]
            self.conn.execute("UPDATE projects SET next_number=? WHERE id=?", (number + 1, proj["id"]))
            key = f"{proj['key']}-{number}"
            now = self.now()
            cur = self.conn.execute(
                "INSERT INTO tickets (key, project_id, title, description, status, priority, due_date,"
                " parent_id, created_at, updated_at, status_changed_at, created_by)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, proj["id"], title, description or "", status, priority, due,
                 parent_row["id"] if parent_row else None, now, now, now, self.actor),
            )
            self._log(cs, "ticket", cur.lastrowid, key, "create", new={"title": title})
            ticket = self._ticket_row(key)
            for lr in label_rows:
                self._add_label(cs, ticket, lr)
        return self.get_ticket(key)

    def update_ticket(
        self,
        key: str,
        title: Any = _UNSET,
        description: Any = _UNSET,
        project: Any = _UNSET,
        status: Any = _UNSET,
        priority: Any = _UNSET,
        due_date: Any = _UNSET,
        parent: Any = _UNSET,
        labels: Any = _UNSET,
        add_labels: Iterable[str] = (),
        remove_labels: Iterable[str] = (),
        reason: str | None = None,
    ) -> Ticket:
        """Change any fields of a ticket in one go. `labels` replaces all labels.

        `reason` is required when moving to Blocked and is saved as a comment.
        """
        row = self._ticket_row(key)
        with self._tx() as cs:
            self._update_in_tx(cs, row, dict(
                title=title, description=description, project=project, status=status,
                priority=priority, due_date=due_date, parent=parent, labels=labels,
                add_labels=add_labels, remove_labels=remove_labels, reason=reason,
            ))
        return self.get_ticket(key)

    def _plan_update(self, row: sqlite3.Row, changes: dict) -> tuple[dict, list, list]:
        """Validate changes; return (field values, labels to add, labels to remove)."""
        if row["archived_at"]:
            raise RuleError(f"{row['key']} is archived. Restore it before changing it.")
        fields: dict[str, Any] = {}
        if changes.get("title", _UNSET) is not _UNSET:
            fields["title"] = self._check_title(changes["title"])
        if changes.get("description", _UNSET) is not _UNSET:
            fields["description"] = changes["description"] or ""
        if changes.get("project", _UNSET) is not _UNSET:
            fields["project"] = self._check_open_project(changes["project"])["key"]
        if changes.get("priority", _UNSET) is not _UNSET:
            fields["priority"] = self._check_priority(changes["priority"])
        if changes.get("due_date", _UNSET) is not _UNSET:
            fields["due_date"] = self._check_date(changes["due_date"])
        if changes.get("parent", _UNSET) is not _UNSET:
            p = self._check_parent(changes["parent"], own_id=row["id"])
            fields["parent"] = p["key"] if p else None
        if changes.get("status", _UNSET) is not _UNSET:
            status = self._check_status(changes["status"])
            self._check_transition(row, status, changes.get("reason"))
            fields["status"] = status
        current = set(self._ticket_labels(row["id"]))
        if changes.get("labels", _UNSET) is not _UNSET:
            target = {r["name"] for r in self._check_labels(changes["labels"] or [])}
        else:
            target = set(current)
        target |= {r["name"] for r in self._check_labels(changes.get("add_labels") or [])}
        target -= {str(n).strip().lower() for n in (changes.get("remove_labels") or [])}
        return fields, sorted(target - current), sorted(current - target)

    def _update_in_tx(self, cs: str, row: sqlite3.Row, changes: dict) -> bool:
        fields, add, remove = self._plan_update(row, changes)
        changed = False
        for f in TICKET_FIELDS:
            if f in fields:
                changed |= self._set_field(cs, "ticket", row, f, fields[f])
        for n in add:
            changed |= self._add_label(cs, row, self._label_row(n))
        for n in remove:
            changed |= self._remove_label(cs, row, self._label_row(n))
        if fields.get("status") == "Blocked" and (changes.get("reason") or "").strip() and changed:
            self._insert_comment(cs, row, "Blocked: " + changes["reason"].strip())
        return changed

    def move_ticket(self, key: str, status: str, reason: str | None = None) -> Ticket:
        """Change status, following the allowed moves."""
        return self.update_ticket(key, status=status, reason=reason)

    def add_comment(self, key: str, body: str) -> Comment:
        body = (body or "").strip()
        if not body:
            raise ValidationError("Comment can't be empty.")
        row = self._ticket_row(key)
        with self._tx() as cs:
            cid = self._insert_comment(cs, row, body)
        c = self.conn.execute("SELECT * FROM comments WHERE id=?", (cid,)).fetchone()
        return Comment(c["id"], c["author"], c["body"], c["created_at"])

    def archive_ticket(self, key: str) -> Ticket:
        """Hide a ticket. Nothing is ever deleted; restore_ticket brings it back."""
        row = self._ticket_row(key)
        if not row["archived_at"]:
            with self._tx() as cs:
                self._set_field(cs, "ticket", row, "archived_at", self.now(), action="archive")
        return self.get_ticket(key)

    def restore_ticket(self, key: str) -> Ticket:
        row = self._ticket_row(key)
        if row["archived_at"]:
            proj = self.conn.execute("SELECT * FROM projects WHERE id=?", (row["project_id"],)).fetchone()
            with self._tx() as cs:
                if proj["archived_at"]:
                    self._set_field(cs, "ticket", row, "project", INBOX_KEY)
                self._set_field(cs, "ticket", row, "archived_at", None, action="restore")
        return self.get_ticket(key)

    # ------------------------------------------------------------------- lists

    def list_tickets(
        self,
        project: str | Iterable[str] | None = None,
        status: str | Iterable[str] | None = None,
        priority: str | Iterable[str] | None = None,
        labels: str | Iterable[str] | None = None,
        text: str | None = None,
        due_before: str | date | None = None,
        due_after: str | date | None = None,
        flags: str | Iterable[str] | None = None,
        parent: str | None = None,
        include_archived: bool = False,
        include_closed: bool = True,
        limit: int | None = None,
    ) -> list[Ticket]:
        """Find tickets. List arguments match any of the values given.

        Sorted by priority (Urgent first), then due date, then newest.
        """
        def as_list(v):
            if v is None:
                return []
            return [v] if isinstance(v, str) else list(v)

        where, params = [], []
        if not include_archived:
            where.append("t.archived_at IS NULL")
        if not include_closed:
            where.append("t.status NOT IN ('Done', 'Cancelled')")
        if project is not None:
            ids = [self._project_row(p)["id"] for p in as_list(project)]
            where.append(f"t.project_id IN ({','.join('?' * len(ids))})")
            params += ids
        if status is not None:
            st = [self._check_status(s) for s in as_list(status)]
            where.append(f"t.status IN ({','.join('?' * len(st))})")
            params += st
        if priority is not None:
            pr = [self._check_priority(p) for p in as_list(priority)]
            where.append(f"t.priority IN ({','.join('?' * len(pr))})")
            params += pr
        if labels is not None:
            names = [str(n).strip().lower() for n in as_list(labels)]
            where.append(
                "t.id IN (SELECT tl.ticket_id FROM ticket_labels tl JOIN labels l ON l.id=tl.label_id"
                f" WHERE l.name IN ({','.join('?' * len(names))}))"
            )
            params += names
        if text:
            like = f"%{text}%"
            where.append(
                "(t.title LIKE ? OR t.description LIKE ? OR t.key LIKE ? OR t.id IN"
                " (SELECT ticket_id FROM comments WHERE body LIKE ?))"
            )
            params += [like, like, like, like]
        if due_before is not None:
            where.append("t.due_date IS NOT NULL AND t.due_date <= ?")
            params.append(self._check_date(due_before))
        if due_after is not None:
            where.append("t.due_date IS NOT NULL AND t.due_date >= ?")
            params.append(self._check_date(due_after))
        if parent is not None:
            where.append("t.parent_id = ?")
            params.append(self._ticket_row(parent)["id"])
        sql = "SELECT t.* FROM tickets t"
        if where:
            sql += " WHERE " + " AND ".join(where)
        order = "CASE t.priority " + " ".join(f"WHEN '{p}' THEN {i}" for i, p in enumerate(PRIORITIES)) + " END"
        sql += f" ORDER BY {order}, t.due_date IS NULL, t.due_date, t.id DESC"
        settings = self.get_settings()
        wanted_flags = set(as_list(flags))
        result = []
        for row in self.conn.execute(sql, params).fetchall():
            t = self._to_ticket(row, settings=settings)
            if wanted_flags and not wanted_flags & set(t.flags):
                continue
            result.append(t)
            if limit and len(result) >= limit:
                break
        return result

    def search(self, text: str, include_archived: bool = False, limit: int = 50) -> list[Ticket]:
        """Free text search over keys, titles, descriptions and comments."""
        return self.list_tickets(text=text, include_archived=include_archived, limit=limit)

    # ------------------------------------------------------------------ boards

    _BOARD_FILTER_KEYS = {"project", "priority", "labels", "text", "due_before", "due_after", "flags"}

    def list_boards(self) -> list[dict]:
        boards = [{"name": "All", "builtin": True}, {"name": "Today", "builtin": True}]
        boards += [{"name": p.name, "builtin": True, "project": p.key} for p in self.list_projects()]
        boards += [
            {"name": r["name"], "builtin": False, "filter": json.loads(r["filter"]), "columns": json.loads(r["columns"])}
            for r in self.conn.execute("SELECT * FROM boards ORDER BY name")
        ]
        return boards

    def create_board(self, name: str, filter: dict | None = None, columns: Iterable[str] | None = None) -> dict:
        name = (name or "").strip()
        if not name:
            raise ValidationError("Board name is required.")
        if any(b["name"].lower() == name.lower() for b in self.list_boards()):
            raise ValidationError(f"A board called '{name}' already exists.")
        filter = dict(filter or {})
        bad = set(filter) - self._BOARD_FILTER_KEYS
        if bad:
            raise ValidationError(
                f"Unknown board filter(s): {', '.join(sorted(bad))}. Allowed: {', '.join(sorted(self._BOARD_FILTER_KEYS))}."
            )
        self.list_tickets(**filter)  # validates values
        cols = [self._check_status(c) for c in (columns or STATUSES)]
        with self._tx() as cs:
            cur = self.conn.execute(
                "INSERT INTO boards (name, filter, columns, created_at) VALUES (?,?,?,?)",
                (name, json.dumps(filter), json.dumps(cols), self.now()),
            )
            self._log(cs, "board", cur.lastrowid, name, "create")
        return {"name": name, "builtin": False, "filter": filter, "columns": cols}

    def get_board(self, name: str) -> dict:
        """A board's columns, each with its tickets."""
        board = next((b for b in self.list_boards() if b["name"].lower() == str(name).lower()), None)
        if board is None:
            board = next((b for b in self.list_boards() if b.get("project") == str(name).upper()), None)
        if board is None:
            raise NotFoundError(f"No board called '{name}'. Boards: {', '.join(b['name'] for b in self.list_boards())}.")
        columns = list(STATUSES)
        if board["name"] == "Today" and board["builtin"]:
            tickets = [t for t in self.list_tickets(include_closed=False)
                       if t.status == "In Progress" or {"overdue", "due_soon"} & set(t.flags)
                       or t.due_date == self.today().isoformat()]
            columns = ["To Do", "In Progress", "Blocked", "Backlog"]
        elif board.get("project"):
            tickets = self.list_tickets(project=board["project"])
        elif board["builtin"]:
            tickets = self.list_tickets()
        else:
            tickets = self.list_tickets(**board["filter"])
            columns = board["columns"]
        return {
            "name": board["name"],
            "columns": [
                {"status": s, "tickets": [t.to_dict() for t in tickets if t.status == s]} for s in columns
            ],
        }

    # ------------------------------------------------------------------- bulk

    def bulk_update(self, keys: Iterable[str], dry_run: bool = False, confirm: str | None = None,
                    **changes: Any) -> dict:
        """Apply the same changes to many tickets, all in one change set.

        Over the bulk limit (default 10 tickets) you must first call with dry_run=True,
        then call again passing the returned `confirm` token.
        """
        keys = [str(k).strip().upper() for k in keys]
        allowed = set(TICKET_FIELDS) | {"labels", "add_labels", "remove_labels", "reason"}
        bad = set(changes) - allowed
        if bad:
            raise ValidationError(f"Can't bulk change: {', '.join(sorted(bad))}.")
        plan = []
        for k in keys:
            row = self._ticket_row(k)
            fields, add, remove = self._plan_update(row, changes)
            plan.append({"key": row["key"], "fields": fields, "add_labels": add, "remove_labels": remove})
        token = hashlib.sha256(json.dumps([keys, changes], sort_keys=True, default=str).encode()).hexdigest()[:16]
        if dry_run:
            return {"dry_run": True, "count": len(plan), "plan": plan, "confirm": token}
        if len(keys) > self.get_settings()["bulk_limit"] and confirm != token:
            raise RuleError(
                f"This changes {len(keys)} tickets. Run it with dry_run=True first, check the plan, "
                "then run again with the confirm token it returns."
            )
        with self._tx() as cs:
            for k in keys:
                self._update_in_tx(cs, self._ticket_row(k), changes)
        return {"dry_run": False, "count": len(plan), "change_set": cs, "plan": plan}

    # ----------------------------------------------------------- history/undo

    def get_history(self, key: str | None = None, limit: int = 50, actor: str | None = None) -> list[dict]:
        """Recent changes, newest first. Filter by ticket/project key or actor."""
        where, params = [], []
        if key:
            where.append("entity_key=?")
            params.append(str(key).strip())
        if actor:
            where.append("actor LIKE ?")
            params.append(actor + "%")
        sql = "SELECT * FROM history"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return [
            {
                "change_set": r["change_set"], "entity_type": r["entity_type"], "key": r["entity_key"],
                "actor": r["actor"], "action": r["action"], "field": r["field"],
                "old": _dec(r["old_value"]), "new": _dec(r["new_value"]), "at": r["created_at"],
            }
            for r in self.conn.execute(sql, params)
        ]

    def undo(self, change_set: str | None = None, force: bool = False) -> dict:
        """Reverse a change set. With no id, undoes this actor's latest change.

        If a later change touched the same field, raises UndoConflictError unless force=True.
        The undo is itself a change set, so it can be undone too.
        """
        if change_set is None:
            r = self.conn.execute(
                "SELECT change_set FROM history WHERE actor=? AND action!='undo' ORDER BY id DESC LIMIT 1",
                (self.actor,),
            ).fetchone()
            if r is None:
                raise NotFoundError("Nothing to undo.")
            change_set = r["change_set"]
        rows = self.conn.execute(
            "SELECT * FROM history WHERE change_set=? AND action!='undo' ORDER BY id DESC", (change_set,)
        ).fetchall()
        if not rows:
            raise NotFoundError(f"No change set '{change_set}'.")
        last_id = max(r["id"] for r in rows)
        conflicts = []
        for r in rows:
            later = self.conn.execute(
                "SELECT 1 FROM history WHERE id>? AND entity_type=? AND entity_id=? AND"
                " COALESCE(field,'archived_at')=? AND action!='undo' LIMIT 1",
                (last_id, r["entity_type"], r["entity_id"], r["field"] or "archived_at"),
            ).fetchone()
            if later:
                conflicts.append(f"{r['entity_key']} {r['field'] or 'archived'}")
        if conflicts and not force:
            raise UndoConflictError(
                "Later changes touched the same fields: " + ", ".join(sorted(set(conflicts)))
                + ". Pass force=True to undo anyway."
            )
        with self._tx() as cs:
            for r in rows:
                self._reverse(cs, r)
            self._log(cs, "change_set", 0, change_set, "undo")
        return {"undone": change_set, "change_set": cs, "changes": len(rows)}

    def _reverse(self, cs: str, h: sqlite3.Row) -> None:
        et, action = h["entity_type"], h["action"]
        if et == "comment":
            self.conn.execute("DELETE FROM comments WHERE id=?", (h["entity_id"],))
            self._log(cs, "comment", h["entity_id"], h["entity_key"], "comment_removed", "comment", _dec(h["new_value"]), None)
            return
        if et == "board":
            self.conn.execute("DELETE FROM boards WHERE id=?", (h["entity_id"],))
            return
        table = self._TABLE[et]
        row = self.conn.execute(f"SELECT * FROM {table} WHERE id=?", (h["entity_id"],)).fetchone()
        if action == "create":
            self._set_field(cs, et, row, "archived_at", self.now(), action="archive")
        elif action == "label_add":
            self._remove_label(cs, row, self._label_row(_dec(h["new_value"])))
        elif action == "label_remove":
            self._add_label(cs, row, self._label_row(_dec(h["old_value"])))
        else:  # update / archive / restore
            back = {"archive": "restore", "restore": "archive"}.get(action, "update")
            self._set_field(cs, et, row, h["field"], _dec(h["old_value"]), action=back)

    # ------------------------------------------------------- housekeeping etc.

    def run_housekeeping(self) -> dict:
        """Tidy up now and return the report. See "03 Housekeeping" in the spec."""
        from ticket_master.core import housekeeping
        return housekeeping.run(self)

    def housekeeping_report(self) -> dict:
        """What needs attention, without changing anything."""
        from ticket_master.core import housekeeping
        return housekeeping.report(self)

    def run_daily_maintenance(self) -> dict | None:
        """Housekeeping + backup, at most once a day. Returns the report if it ran."""
        from ticket_master.core import housekeeping
        return housekeeping.run_if_due(self)

    def backup_now(self) -> Path | None:
        from ticket_master.core import backup
        return backup.backup(self)
