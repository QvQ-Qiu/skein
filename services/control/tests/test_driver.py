from datetime import datetime

import pytest

from skein import db, runs
from skein.driver import Driver, parse_schedules


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "driver.db")
    db.migrate(c)
    yield c
    c.close()


class FakeRunner:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def __call__(self, command, timeout):
        self.calls.append((command, timeout))
        return self.ok, "done" if self.ok else "boom"


def test_parse_schedules_skips_invalid():
    schedules = parse_schedules('[{"job":"daily","at":"09:15"},{"job":"","at":"10:00"},{"job":"x","at":"bad"}]')
    assert [(s.job, s.at) for s in schedules] == [("daily", "09:15")]


def test_due_jobs_matches_minute():
    driver = Driver(conn=None, schedules=parse_schedules('[{"job":"daily","at":"09:15"}]'), commands={})
    assert driver.due_jobs(datetime(2026, 11, 18, 9, 15)) == ["daily"]
    assert driver.due_jobs(datetime(2026, 11, 18, 9, 16)) == []


def test_tick_runs_command_once_per_day(conn):
    runner = FakeRunner()
    driver = Driver(
        conn,
        parse_schedules('[{"job":"daily","at":"09:15"}]'),
        {"daily": "python main.py daily"},
        runner=runner,
        inline=True,
    )
    now = datetime(2026, 11, 18, 9, 15)
    assert len(driver.tick(now)) == 1
    assert runner.calls == [("python main.py daily", 4 * 3600)]
    run = runs.by_day(conn, "daily", "2026-11-18")
    assert run.status == "succeeded"

    assert driver.tick(now) == []
    assert len(runner.calls) == 1


def test_missing_command_is_skipped(conn):
    driver = Driver(
        conn,
        parse_schedules('[{"job":"curator","at":"10:20"}]'),
        {},
        runner=FakeRunner(),
        inline=True,
    )
    assert driver.tick(datetime(2026, 11, 18, 10, 20)) == []
    run = runs.by_day(conn, "curator", "2026-11-18")
    assert run.status == "skipped"


def test_failed_command_marks_failed(conn):
    driver = Driver(
        conn,
        parse_schedules('[{"job":"daily","at":"09:15"}]'),
        {"daily": "false"},
        runner=FakeRunner(ok=False),
        inline=True,
    )
    driver.tick(datetime(2026, 11, 18, 9, 15))
    assert runs.by_day(conn, "daily", "2026-11-18").status == "failed"
