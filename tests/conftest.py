from datetime import datetime, timedelta, timezone

import pytest

from ticket_master import TicketMaster


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def tm(tmp_path, clock):
    t = TicketMaster(tmp_path / "tickets.db", clock=clock)
    yield t
    t.close()
