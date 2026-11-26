def test_flags(tm, clock):
    overdue = tm.create_ticket("late", due_date="2026-11-25")
    soon = tm.create_ticket("soon", due_date="2026-11-28")
    later = tm.create_ticket("later", due_date="2026-12-20")
    assert tm.get_ticket(overdue.key).flags == ["overdue"]
    assert tm.get_ticket(soon.key).flags == ["due_soon"]
    assert tm.get_ticket(later.key).flags == []


def test_stale_flags_clear_on_change(tm, clock):
    t = tm.create_ticket("x", status="In Progress")
    clock.advance(days=15)
    assert "stale" in tm.get_ticket(t.key).flags
    tm.add_comment(t.key, "still on it")
    assert "stale" not in tm.get_ticket(t.key).flags


def test_closed_tickets_have_no_flags(tm):
    t = tm.create_ticket("x", due_date="2026-11-01", status="To Do")
    tm.move_ticket(t.key, "Cancelled")
    assert tm.get_ticket(t.key).flags == []


def test_auto_archive_after_30_days_and_undo(tm, clock):
    t = tm.create_ticket("x", status="In Progress")
    tm.move_ticket(t.key, "Done")
    clock.advance(days=29)
    assert tm.run_housekeeping()["actions"]["archived"] == []
    clock.advance(days=2)
    r = tm.run_housekeeping()
    assert r["actions"]["archived"] == [t.key]
    assert tm.get_history(t.key)[0]["actor"] == "system:housekeeping"
    tm.undo(r["actions"]["change_set"])
    assert tm.get_ticket(t.key).archived_at is None


def test_report_suggestions(tm, clock):
    tm.create_label("unused")
    tm.create_ticket("Revise for stats exam", project="UNI")
    tm.create_ticket("Revise for stats exams", project="UNI")
    old_inbox = tm.create_ticket("Sort me")
    clock.advance(days=8)
    r = tm.housekeeping_report()
    assert r["suggestions"]["unused_labels"] == ["unused"]
    assert r["suggestions"]["possible_duplicates"][0]["tickets"] == ["UNI-1", "UNI-2"]
    assert r["needs_sorting"] == [old_inbox.key]
    # suggestions never change data
    assert len(tm.list_tickets()) == 3


def test_daily_runs_once_and_backs_up(tm, clock, tmp_path):
    first = tm.run_daily_maintenance()
    assert first is not None and first["backup"].endswith("tickets-2026-11-26.db")
    assert tm.run_daily_maintenance() is None
    clock.advance(days=1)
    assert tm.run_daily_maintenance() is not None


def test_old_backups_pruned(tm, clock, tmp_path):
    for _ in range(16):
        tm.backup_now()
        clock.advance(days=1)
    assert len(list((tmp_path / "backups").glob("tickets-*.db"))) == 14


def test_settings(tm, clock):
    tm.update_settings(stale_in_progress_days=3)
    t = tm.create_ticket("x", status="In Progress")
    clock.advance(days=3)
    assert "stale" in tm.get_ticket(t.key).flags
