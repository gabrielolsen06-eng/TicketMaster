import pytest

from ticket_master.core.errors import RuleError, UndoConflictError


def test_every_change_logged_with_actor(tm):
    ai = tm.as_actor("ai:claude")
    t = ai.create_ticket("x")
    ai.update_ticket(t.key, title="y", priority="High")
    rows = tm.get_history(t.key)
    assert {r["actor"] for r in rows} == {"ai:claude"}
    assert {(r["field"], r["old"], r["new"]) for r in rows if r["action"] == "update"} == {
        ("title", "x", "y"), ("priority", "None", "High"),
    }


def test_undo_multi_field_change(tm):
    tm.create_label("exam")
    t = tm.create_ticket("x")
    tm.update_ticket(t.key, title="y", priority="High", project="UNI", add_labels=["exam"], due_date="2026-12-01")
    tm.undo()
    t = tm.get_ticket(t.key)
    assert (t.title, t.priority, t.project, t.labels, t.due_date) == ("x", "None", "IN", [], None)


def test_undo_create_archives(tm):
    t = tm.create_ticket("x")
    tm.undo()
    assert tm.get_ticket(t.key).archived_at is not None


def test_undo_is_undoable(tm):
    t = tm.create_ticket("x")
    tm.update_ticket(t.key, title="y")
    r = tm.undo()
    tm.undo(r["change_set"])
    assert tm.get_ticket(t.key).title == "y"


def test_undo_comment(tm):
    t = tm.create_ticket("x")
    tm.add_comment(t.key, "hello")
    tm.undo()
    assert tm.get_ticket(t.key).comments == []


def test_undo_only_own_latest_by_default(tm):
    t = tm.create_ticket("x")
    tm.update_ticket(t.key, title="mine")
    tm.as_actor("ai:claude").update_ticket(t.key, priority="High")
    tm.undo()
    t = tm.get_ticket(t.key)
    assert (t.title, t.priority) == ("x", "High")


def test_undo_conflict(tm):
    t = tm.create_ticket("x")
    tm.update_ticket(t.key, title="y")
    cs = tm.get_history(t.key)[0]["change_set"]
    tm.update_ticket(t.key, title="z")
    with pytest.raises(UndoConflictError):
        tm.undo(cs)
    tm.undo(cs, force=True)
    assert tm.get_ticket(t.key).title == "x"


def test_failed_call_changes_nothing(tm):
    t = tm.create_ticket("x")
    before = len(tm.get_history())
    with pytest.raises(RuleError):
        tm.update_ticket(t.key, title="y", status="Done")
    assert tm.get_ticket(t.key).title == "x"
    assert len(tm.get_history()) == before


def test_bulk_needs_dry_run_over_limit(tm):
    keys = [tm.create_ticket(f"t{i}").key for i in range(12)]
    with pytest.raises(RuleError, match="dry_run"):
        tm.bulk_update(keys, priority="High")
    plan = tm.bulk_update(keys, priority="High", dry_run=True)
    assert plan["count"] == 12
    assert tm.get_ticket(keys[0]).priority == "None"
    done = tm.bulk_update(keys, priority="High", confirm=plan["confirm"])
    assert all(tm.get_ticket(k).priority == "High" for k in keys)
    tm.undo(done["change_set"])
    assert all(tm.get_ticket(k).priority == "None" for k in keys)


def test_bulk_small_runs_directly(tm):
    keys = [tm.create_ticket(f"t{i}").key for i in range(3)]
    tm.bulk_update(keys, status="To Do")
    assert all(tm.get_ticket(k).status == "To Do" for k in keys)
