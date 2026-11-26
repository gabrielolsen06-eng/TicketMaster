"""Fixed values (statuses, priorities) and the data shapes the core returns."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

STATUSES = ["Backlog", "To Do", "In Progress", "Blocked", "Done", "Cancelled"]
CLOSED_STATUSES = {"Done", "Cancelled"}

# Allowed moves, see "02 Workflow and rules" in the Obsidian spec.
TRANSITIONS: dict[str, set[str]] = {
    "Backlog": {"To Do", "Cancelled"},
    "To Do": {"In Progress", "Backlog", "Cancelled"},
    "In Progress": {"Done", "Blocked", "To Do", "Cancelled"},
    "Blocked": {"In Progress", "Cancelled"},
    "Done": {"To Do"},
    "Cancelled": {"To Do"},
}

PRIORITIES = ["Urgent", "High", "Medium", "Low", "None"]

STARTER_PROJECTS = [
    ("IN", "Inbox", "Anything not sorted yet."),
    ("DAY", "Daily", "Day to day tasks."),
    ("UNI", "University", "University work."),
]
INBOX_KEY = "IN"

DEFAULT_SETTINGS = {
    "stale_in_progress_days": 14,
    "stale_blocked_days": 7,
    "archive_closed_after_days": 30,
    "due_soon_days": 2,
    "inbox_sort_after_days": 7,
    "duplicate_similarity": 0.85,
    "backup_keep_days": 14,
    "bulk_limit": 10,
}


@dataclass
class Comment:
    id: int
    author: str
    body: str
    created_at: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Ticket:
    key: str
    title: str
    description: str
    project: str
    status: str
    priority: str
    labels: list[str]
    due_date: str | None
    parent: str | None
    created_at: str
    updated_at: str
    created_by: str
    archived_at: str | None
    flags: list[str] = field(default_factory=list)
    comments: list[Comment] | None = None
    subtasks: list[str] | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.comments is None:
            d.pop("comments")
        if self.subtasks is None:
            d.pop("subtasks")
        return d


@dataclass
class Project:
    key: str
    name: str
    description: str
    colour: str | None
    archived_at: str | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Label:
    name: str
    colour: str | None
    archived_at: str | None

    def to_dict(self) -> dict:
        return asdict(self)
