import pytest

from ticket_master import TicketMaster
from ticket_master.core.errors import NotFoundError, RuleError, ValidationError


def test_starter_projects(tm):
    assert [p.key for p in tm.list_projects()] == ["IN", "DAY", "UNI"]


def test_create_ticket_defaults(tm):
    t = tm.create_ticket("Buy milk")
    assert t.key == "IN-1"
    assert (t.status, t.priority, t.project, t.created_by) == ("Backlog", "None", "IN", "user")
    assert tm.create_ticket("Second").key == "IN-2"


def test_keys_never_reused(tm):
    t = tm.create_ticket("One", project="UNI")
    tm.archive_ticket(t.key)
    assert tm.create_ticket("Two", project="UNI").key == "UNI-2"


def test_required_and_valid_values(tm):
    with pytest.raises(ValidationError):
        tm.create_ticket("  ")
    with pytest.raises(ValidationError):
        tm.create_ticket("x", priority="Huge")
    with pytest.raises(ValidationError):
        tm.create_ticket("x", due_date="next week")
    with pytest.raises(NotFoundError):
        tm.create_ticket("x", project="NOPE")
    with pytest.raises(NotFoundError):
        tm.create_ticket("x", labels=["unknown"])
    with pytest.raises(RuleError):
        tm.create_ticket("x", status="Done")


def test_case_insensitive_values(tm):
    t = tm.create_ticket("x", priority="high", status="to do", project="uni")
    assert (t.priority, t.status, t.project) == ("High", "To Do", "UNI")


def test_projects(tm):
    p = tm.create_project("home", "Home", "House stuff")
    assert p.key == "HOME"
    with pytest.raises(ValidationError):
        tm.create_project("HOME", "Again")
    with pytest.raises(ValidationError):
        tm.create_project("X", "Too short")
    with pytest.raises(RuleError):
        tm.archive_project("IN")


def test_archive_project_moves_open_tickets_to_inbox(tm):
    tm.create_project("HOME", "Home")
    open_t = tm.create_ticket("Fix tap", project="HOME")
    tm.archive_project("HOME")
    assert tm.get_ticket(open_t.key).project == "IN"
    with pytest.raises(RuleError):
        tm.create_ticket("x", project="HOME")


def test_labels(tm):
    tm.create_label("Exam")
    assert [l.name for l in tm.list_labels()] == ["exam"]
    t = tm.create_ticket("Revise", labels=["exam"])
    assert t.labels == ["exam"]
    tm.create_label("reading")
    t = tm.update_ticket(t.key, add_labels=["reading"], remove_labels=["exam"])
    assert t.labels == ["reading"]
    t = tm.update_ticket(t.key, labels=[])
    assert t.labels == []
    tm.archive_label("reading")
    with pytest.raises(RuleError):
        tm.update_ticket(t.key, add_labels=["reading"])


def test_comments_and_search(tm):
    t = tm.create_ticket("Essay", description="History essay on Rome")
    tm.add_comment(t.key, "Found three good sources")
    assert [x.key for x in tm.search("rome")] == [t.key]
    assert [x.key for x in tm.search("sources")] == [t.key]
    assert tm.get_ticket(t.key).comments[0].body == "Found three good sources"


def test_list_filters_and_order(tm):
    tm.create_label("exam")
    a = tm.create_ticket("Low one", priority="Low", project="UNI")
    b = tm.create_ticket("Urgent one", priority="Urgent", project="UNI", labels=["exam"])
    c = tm.create_ticket("Daily", project="DAY")
    assert [t.key for t in tm.list_tickets(project="UNI")] == [b.key, a.key]
    assert [t.key for t in tm.list_tickets(labels="exam")] == [b.key]
    assert {t.key for t in tm.list_tickets(project=["UNI", "DAY"])} == {a.key, b.key, c.key}


def test_archive_hides_and_restore(tm):
    t = tm.create_ticket("x")
    tm.archive_ticket(t.key)
    assert tm.list_tickets() == []
    assert len(tm.list_tickets(include_archived=True)) == 1
    with pytest.raises(RuleError):
        tm.update_ticket(t.key, title="y")
    tm.restore_ticket(t.key)
    assert len(tm.list_tickets()) == 1


def test_no_delete_methods():
    assert not [m for m in dir(TicketMaster) if "delete" in m.lower()]


def test_two_connections_share_data(tmp_path):
    a = TicketMaster(tmp_path / "db.sqlite")
    b = TicketMaster(tmp_path / "db.sqlite", actor="ai:claude")
    b.create_ticket("From AI")
    assert a.list_tickets()[0].created_by == "ai:claude"
