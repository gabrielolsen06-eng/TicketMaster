CREATE TABLE projects (
    id          INTEGER PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    colour      TEXT,
    next_number INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    archived_at TEXT
);

CREATE TABLE labels (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    colour      TEXT,
    created_at  TEXT NOT NULL,
    archived_at TEXT
);

CREATE TABLE tickets (
    id                INTEGER PRIMARY KEY,
    key               TEXT NOT NULL UNIQUE,
    project_id        INTEGER NOT NULL REFERENCES projects(id),
    title             TEXT NOT NULL,
    description       TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL,
    priority          TEXT NOT NULL,
    due_date          TEXT,
    parent_id         INTEGER REFERENCES tickets(id),
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    status_changed_at TEXT NOT NULL,
    created_by        TEXT NOT NULL,
    archived_at       TEXT
);
CREATE INDEX tickets_project ON tickets(project_id);
CREATE INDEX tickets_status ON tickets(status);

CREATE TABLE ticket_labels (
    ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    label_id  INTEGER NOT NULL REFERENCES labels(id),
    PRIMARY KEY (ticket_id, label_id)
);

CREATE TABLE comments (
    id         INTEGER PRIMARY KEY,
    ticket_id  INTEGER NOT NULL REFERENCES tickets(id),
    author     TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE boards (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    filter     TEXT NOT NULL DEFAULT '{}',
    columns    TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- One row per change. change_set groups rows made by one call so they undo together.
CREATE TABLE history (
    id          INTEGER PRIMARY KEY,
    change_set  TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id   INTEGER NOT NULL,
    entity_key  TEXT NOT NULL,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    field       TEXT,
    old_value   TEXT,
    new_value   TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX history_change_set ON history(change_set);
CREATE INDEX history_entity ON history(entity_type, entity_id);

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
