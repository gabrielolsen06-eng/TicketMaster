from ticket_master.cli import run


def test_cli_roundtrip(tmp_path, capsys):
    db = str(tmp_path / "t.db")
    run(["--db", db, "add", "Hello", "-p", "DAY", "--priority", "High"])
    run(["--db", db, "move", "DAY-1", "To Do"])
    run(["--db", db, "list"])
    out = capsys.readouterr().out
    assert "Created DAY-1" in out and "To Do" in out
