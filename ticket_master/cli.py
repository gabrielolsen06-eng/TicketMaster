"""`tm`: a small command line for Ticket Master.

Examples:
    tm add "Revise for stats exam" -p UNI --priority High --due 2026-12-10 -l exam
    tm list -p UNI
    tm move UNI-1 "In Progress"
    tm show UNI-1
    tm undo
"""

from __future__ import annotations

import argparse
import json
import sys

from ticket_master.core.errors import TicketMasterError
from ticket_master.core.models import PRIORITIES, STATUSES
from ticket_master.core.service import TicketMaster


def _ticket_line(t) -> str:
    bits = [f"{t.key:<8}", f"{t.status:<11}", f"{t.priority:<6}", t.title]
    if t.due_date:
        bits.append(f"(due {t.due_date})")
    if t.labels:
        bits.append("[" + ", ".join(t.labels) + "]")
    if t.flags:
        bits.append("!" + ",".join(t.flags))
    return "  ".join(bits)


def _print(obj, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tm", description="Ticket Master command line.")
    p.add_argument("--db", help="Path to the database (default: your Ticket Master folder).")
    p.add_argument("--json", action="store_true", help="Print JSON instead of text.")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="Create a ticket.")
    a.add_argument("title")
    a.add_argument("-p", "--project", default="IN")
    a.add_argument("-d", "--description", default="")
    a.add_argument("--priority", default="None", choices=PRIORITIES)
    a.add_argument("--status", default="Backlog")
    a.add_argument("--due")
    a.add_argument("-l", "--label", action="append", default=[])
    a.add_argument("--parent")

    ls = sub.add_parser("list", help="List tickets.")
    ls.add_argument("-p", "--project", action="append")
    ls.add_argument("-s", "--status", action="append")
    ls.add_argument("-l", "--label", action="append")
    ls.add_argument("--priority", action="append")
    ls.add_argument("--flag", action="append", help="overdue, due_soon or stale")
    ls.add_argument("--open", action="store_true", help="Hide Done and Cancelled.")
    ls.add_argument("--archived", action="store_true", help="Include archived tickets.")
    ls.add_argument("-q", "--text")

    s = sub.add_parser("show", help="Show one ticket.")
    s.add_argument("key")

    e = sub.add_parser("edit", help="Change a ticket.")
    e.add_argument("key")
    e.add_argument("--title")
    e.add_argument("-d", "--description")
    e.add_argument("-p", "--project")
    e.add_argument("--priority", choices=PRIORITIES)
    e.add_argument("--due", help="YYYY-MM-DD, or 'none' to clear.")
    e.add_argument("--parent", help="Parent key, or 'none' to clear.")
    e.add_argument("--add-label", action="append", default=[])
    e.add_argument("--remove-label", action="append", default=[])

    m = sub.add_parser("move", help="Change a ticket's status.")
    m.add_argument("key")
    m.add_argument("status", help=", ".join(STATUSES))
    m.add_argument("-r", "--reason", help="Required when moving to Blocked.")

    c = sub.add_parser("comment", help="Add a comment.")
    c.add_argument("key")
    c.add_argument("body")

    for name in ("archive", "restore"):
        x = sub.add_parser(name, help=f"{name.title()} a ticket.")
        x.add_argument("key")

    h = sub.add_parser("history", help="Show recent changes.")
    h.add_argument("key", nargs="?")
    h.add_argument("-n", type=int, default=20)

    u = sub.add_parser("undo", help="Undo your last change, or a change set by id.")
    u.add_argument("change_set", nargs="?")
    u.add_argument("--force", action="store_true")

    pr = sub.add_parser("projects", help="List projects.")
    pr.add_argument("--archived", action="store_true")
    pa = sub.add_parser("project-add", help="Create a project.")
    pa.add_argument("key")
    pa.add_argument("name")
    pa.add_argument("-d", "--description", default="")

    sub.add_parser("labels", help="List labels.")
    la = sub.add_parser("label-add", help="Create a label.")
    la.add_argument("name")
    la.add_argument("--colour")

    b = sub.add_parser("board", help="Show a board (All, Today, a project name or a custom board).")
    b.add_argument("name", nargs="?", default="Today")

    sub.add_parser("tidy", help="Run housekeeping now.")
    sub.add_parser("report", help="Show the housekeeping report.")
    sub.add_parser("backup", help="Back up the database now.")
    return p


def _none(v):
    return None if v is not None and v.lower() == "none" else v


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    tm = TicketMaster(args.db)
    tm.run_daily_maintenance()
    j = args.json
    cmd = args.cmd

    if cmd == "add":
        t = tm.create_ticket(args.title, project=args.project, description=args.description,
                             priority=args.priority, status=args.status, due_date=args.due,
                             labels=args.label, parent=args.parent)
        _print(t.to_dict(), j) if j else print(f"Created {t.key}: {t.title}")
    elif cmd == "list":
        ts = tm.list_tickets(project=args.project, status=args.status, labels=args.label,
                             priority=args.priority, flags=args.flag, text=args.text,
                             include_closed=not args.open, include_archived=args.archived)
        if j:
            _print([t.to_dict() for t in ts], j)
        else:
            print("\n".join(_ticket_line(t) for t in ts) or "No tickets.")
    elif cmd == "show":
        t = tm.get_ticket(args.key)
        if j:
            _print(t.to_dict(), j)
        else:
            print(_ticket_line(t))
            print(f"Project: {t.project}   Created: {t.created_at} by {t.created_by}")
            if t.parent:
                print(f"Parent: {t.parent}")
            if t.subtasks:
                print(f"Sub-tasks: {', '.join(t.subtasks)}")
            if t.description:
                print("\n" + t.description)
            for cmt in t.comments or []:
                print(f"\n[{cmt.created_at} {cmt.author}] {cmt.body}")
    elif cmd == "edit":
        kw = {}
        for field, val in (("title", args.title), ("description", args.description),
                           ("project", args.project), ("priority", args.priority)):
            if val is not None:
                kw[field] = val
        if args.due is not None:
            kw["due_date"] = _none(args.due)
        if args.parent is not None:
            kw["parent"] = _none(args.parent)
        t = tm.update_ticket(args.key, add_labels=args.add_label, remove_labels=args.remove_label, **kw)
        _print(t.to_dict(), j) if j else print(_ticket_line(t))
    elif cmd == "move":
        t = tm.move_ticket(args.key, args.status, reason=args.reason)
        _print(t.to_dict(), j) if j else print(_ticket_line(t))
    elif cmd == "comment":
        c = tm.add_comment(args.key, args.body)
        _print(c.to_dict(), j) if j else print("Comment added.")
    elif cmd in ("archive", "restore"):
        t = getattr(tm, f"{cmd}_ticket")(args.key)
        _print(t.to_dict(), j) if j else print(f"{cmd.title()}d {t.key}.")
    elif cmd == "history":
        rows = tm.get_history(args.key, limit=args.n)
        if j:
            _print(rows, j)
        for r in [] if j else rows:
            change = f"{r['field']}: {r['old']!r} -> {r['new']!r}" if r["field"] else ""
            print(f"{r['at']}  {r['change_set']}  {r['actor']:<22} {r['action']:<12} {r['key']}  {change}")
    elif cmd == "undo":
        r = tm.undo(args.change_set, force=args.force)
        _print(r, j) if j else print(f"Undid {r['changes']} change(s) from {r['undone']}.")
    elif cmd == "projects":
        ps = tm.list_projects(include_archived=args.archived)
        if j:
            _print([p.to_dict() for p in ps], j)
        else:
            print("\n".join(f"{p.key:<7} {p.name}" + (" (archived)" if p.archived_at else "") for p in ps))
    elif cmd == "project-add":
        p = tm.create_project(args.key, args.name, args.description)
        _print(p.to_dict(), j) if j else print(f"Created project {p.key}.")
    elif cmd == "labels":
        ls = tm.list_labels()
        _print([l.to_dict() for l in ls], j) if j else print("\n".join(l.name for l in ls) or "No labels.")
    elif cmd == "label-add":
        l = tm.create_label(args.name, args.colour)
        _print(l.to_dict(), j) if j else print(f"Created label {l.name}.")
    elif cmd == "board":
        b = tm.get_board(args.name)
        if j:
            _print(b, j)
        else:
            print(f"== {b['name']} ==")
            for col in b["columns"]:
                print(f"\n{col['status']} ({len(col['tickets'])})")
                for t in col["tickets"]:
                    print(f"  {t['key']:<8} {t['title']}")
    elif cmd in ("tidy", "report"):
        r = tm.run_housekeeping() if cmd == "tidy" else tm.housekeeping_report()
        print(json.dumps(r, indent=2))
    elif cmd == "backup":
        path = tm.backup_now()
        print(path if path else "Nothing to back up.")
    return 0


def main() -> None:
    try:
        sys.exit(run())
    except TicketMasterError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
