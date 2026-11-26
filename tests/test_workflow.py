import pytest

from ticket_master.core.errors import RuleError


def test_allowed_path(tm):
    t = tm.create_ticket("x")
    for s in ["To Do", "In Progress", "Done", "To Do"]:
        t = tm.move_ticket(t.key, s)
    assert t.status == "To Do"


def test_invalid_move_explains(tm):
    t = tm.create_ticket("x")
    with pytest.raises(RuleError, match="From Backlog you can move to: Cancelled, To Do"):
        tm.move_ticket(t.key, "Done")


def test_blocked_needs_reason_and_records_it(tm):
    t = tm.create_ticket("x", status="In Progress")
    with pytest.raises(RuleError, match="reason"):
        tm.move_ticket(t.key, "Blocked")
    t = tm.move_ticket(t.key, "Blocked", reason="Waiting on lecturer")
    full = tm.get_ticket(t.key)
    assert full.status == "Blocked"
    assert full.comments[-1].body == "Blocked: Waiting on lecturer"


def test_subtasks(tm):
    parent = tm.create_ticket("Project", status="In Progress")
    sub = tm.create_ticket("Part 1", parent=parent.key, status="In Progress")
    assert tm.get_ticket(parent.key).subtasks == [sub.key]
    with pytest.raises(RuleError, match="open sub-tasks"):
        tm.move_ticket(parent.key, "Done")
    with pytest.raises(RuleError, match="one level"):
        tm.create_ticket("Deeper", parent=sub.key)
    with pytest.raises(RuleError):
        tm.update_ticket(parent.key, parent=parent.key)
    tm.move_ticket(sub.key, "Done")
    assert tm.move_ticket(parent.key, "Done").status == "Done"


def test_rules_apply_to_ai_too(tm):
    ai = tm.as_actor("ai:claude")
    t = ai.create_ticket("x")
    assert t.created_by == "ai:claude"
    with pytest.raises(RuleError):
        ai.move_ticket(t.key, "Done")
