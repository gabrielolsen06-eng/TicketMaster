import pytest

from ticket_master.core.errors import ValidationError


def test_builtin_boards(tm):
    names = [b["name"] for b in tm.list_boards()]
    assert names[:2] == ["All", "Today"] and "University" in names


def test_project_board_columns(tm):
    t = tm.create_ticket("x", project="UNI", status="To Do")
    board = tm.get_board("University")
    col = next(c for c in board["columns"] if c["status"] == "To Do")
    assert [x["key"] for x in col["tickets"]] == [t.key]


def test_today_board(tm):
    a = tm.create_ticket("due", due_date="2026-11-26", status="To Do")
    b = tm.create_ticket("doing", status="In Progress")
    tm.create_ticket("someday")
    keys = {t["key"] for c in tm.get_board("Today")["columns"] for t in c["tickets"]}
    assert keys == {a.key, b.key}


def test_custom_board(tm):
    tm.create_label("exam")
    t = tm.create_ticket("x", labels=["exam"], status="To Do")
    tm.create_ticket("y", status="To Do")
    tm.create_board("Exams", {"labels": ["exam"]}, ["To Do", "Done"])
    board = tm.get_board("exams")
    assert [c["status"] for c in board["columns"]] == ["To Do", "Done"]
    assert [x["key"] for x in board["columns"][0]["tickets"]] == [t.key]
    with pytest.raises(ValidationError):
        tm.create_board("Bad", {"colour": "red"})
