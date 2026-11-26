# Ticket Master

My day to day ticketing system: a personal, Jira-style tracker for daily tasks, projects and university work.

AI isn't built in. Instead, AI tools can use it freely through an MCP server (coming in step 3), and they follow exactly the same rules as I do. The design spec lives in my Obsidian vault under `Ticket Master/`.

## Status

- [x] Core library (this code): SQLite storage, workflow rules, history and undo, housekeeping, backups, `tm` CLI
- [ ] MCP server
- [ ] Desktop app

## Quick start

Needs [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run tm add "Revise for stats exam" -p UNI --priority High --due 2026-12-10
uv run tm list
uv run tm move UNI-1 "To Do"
uv run tm board Today
uv run tm undo
uv run tm --help
```

The database lives in `~/Library/Application Support/TicketMaster/tickets.db` on macOS. Set `TICKET_MASTER_HOME` to use another folder.

## Using the core from Python

```python
from ticket_master import TicketMaster

tm = TicketMaster()                    # or TicketMaster(actor="ai:claude")
t = tm.create_ticket("Buy milk", project="DAY", priority="Low")
tm.move_ticket(t.key, "To Do")
tm.list_tickets(project="UNI", flags="overdue")
tm.undo()
```

## How it stays tidy and safe

- **One way in:** the app, MCP server and CLI all go through `TicketMaster`, so rules can't be skipped.
- **Statuses:** Backlog → To Do → In Progress → Done, plus Blocked (needs a reason) and Cancelled. Invalid moves are refused with a message saying what is allowed.
- **No deletes:** tickets, projects and labels are archived and can be restored.
- **History and undo:** every change records who made it (`user`, `ai:…` or `system:housekeeping`). Any change set can be undone.
- **Bulk safety:** changing more than 10 tickets at once needs a dry run first.
- **Housekeeping:** flags overdue, due soon and stale tickets, archives tickets closed for 30+ days, and suggests possible duplicates and unused labels. Runs once a day along with a backup (last 14 kept).

## Layout

```
ticket_master/
  core/service.py       the TicketMaster class: all reads and writes
  core/housekeeping.py  self-tidying and the report
  core/backup.py        daily backups
  core/db.py            connection (WAL) and migrations
  migrations/           numbered SQL files, applied automatically
  cli.py                the `tm` command
tests/
```

## Tests

```bash
uv run pytest
```
