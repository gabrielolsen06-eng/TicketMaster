"""Self-tidying. See "03 Housekeeping" in the Obsidian spec.

Automatic actions are logged as `system:housekeeping` and can be undone.
Anything that would lose information is only suggested in the report.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from ticket_master.core.models import CLOSED_STATUSES, INBOX_KEY

if TYPE_CHECKING:
    from ticket_master.core.service import TicketMaster

ACTOR = "system:housekeeping"


def run(tm: "TicketMaster") -> dict:
    s = tm.get_settings()
    cutoff = (tm.clock() - timedelta(days=s["archive_closed_after_days"])).isoformat(timespec="seconds")
    archived, moved = [], []
    with tm._tx() as cs:
        for row in tm.conn.execute(
            "SELECT * FROM tickets WHERE archived_at IS NULL AND status IN (?,?) AND status_changed_at <= ?",
            (*CLOSED_STATUSES, cutoff),
        ).fetchall():
            tm._set_field(cs, "ticket", row, "archived_at", tm.now(), action="archive", actor=ACTOR)
            archived.append(row["key"])
        for row in tm.conn.execute(
            "SELECT t.* FROM tickets t JOIN projects p ON p.id=t.project_id"
            " WHERE t.archived_at IS NULL AND p.archived_at IS NOT NULL"
        ).fetchall():
            tm._set_field(cs, "ticket", row, "project", INBOX_KEY, actor=ACTOR)
            moved.append(row["key"])
        tm._set_meta("last_housekeeping", tm.now())
        change_set = cs if (archived or moved) else None
    result = report(tm)
    result["actions"] = {"archived": archived, "moved_to_inbox": moved, "change_set": change_set}
    return result


def report(tm: "TicketMaster") -> dict:
    s = tm.get_settings()
    open_tickets = tm.list_tickets(include_closed=False)
    overdue = [t.key for t in open_tickets if "overdue" in t.flags]
    due_soon = [t.key for t in open_tickets if "due_soon" in t.flags]
    stale = [t.key for t in open_tickets if "stale" in t.flags]

    inbox_cutoff = tm.clock() - timedelta(days=s["inbox_sort_after_days"])
    needs_sorting = [
        t.key for t in open_tickets
        if t.project == INBOX_KEY and datetime.fromisoformat(t.created_at) <= inbox_cutoff
    ]

    duplicates = []
    by_project: dict[str, list] = {}
    for t in open_tickets:
        by_project.setdefault(t.project, []).append(t)
    for tickets in by_project.values():
        for i, a in enumerate(tickets):
            for b in tickets[i + 1:]:
                ratio = SequenceMatcher(None, a.title.lower(), b.title.lower()).ratio()
                if ratio >= s["duplicate_similarity"]:
                    duplicates.append({"tickets": sorted([a.key, b.key]), "similarity": round(ratio, 2)})

    unused_labels = [
        r["name"] for r in tm.conn.execute(
            "SELECT name FROM labels WHERE archived_at IS NULL AND id NOT IN"
            " (SELECT tl.label_id FROM ticket_labels tl JOIN tickets t ON t.id=tl.ticket_id"
            "  WHERE t.archived_at IS NULL) ORDER BY name"
        )
    ]
    return {
        "generated_at": tm.now(),
        "overdue": overdue,
        "due_soon": due_soon,
        "stale": stale,
        "needs_sorting": needs_sorting,
        "suggestions": {"possible_duplicates": duplicates, "unused_labels": unused_labels},
    }


def run_if_due(tm: "TicketMaster") -> dict | None:
    """Run housekeeping and a backup if they haven't run today."""
    last = tm._get_meta("last_housekeeping")
    if last and datetime.fromisoformat(last).date() >= tm.today():
        return None
    result = run(tm)
    path = tm.backup_now()
    result["backup"] = str(path) if path else None
    return result
