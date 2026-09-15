from __future__ import annotations

import pytest

from pulse_hwm.scheduler import Scheduler

# The Scheduler is a UI-thread engine; tests drive _tick() directly (no
# event loop needed) so behavior is deterministic and instant.


@pytest.fixture()
def scheduler():

    s = Scheduler()
    s._running = True  # no QTimer loop in tests; we tick by hand
    return s


def test_immediate_job_fires_on_first_tick(scheduler):
    runs = []
    scheduler.add_job("a", 60_000, lambda: runs.append(1), immediate=True)
    scheduler._tick()
    assert runs == [1]


def test_period_job_waits_out_its_interval(scheduler):
    runs = []
    scheduler.add_job("slow", 600.0 * 1000, lambda: runs.append(1), immediate=False)
    scheduler._tick()
    assert runs == []  # not yet due
    scheduler._jobs["slow"].next_run_monotonic = 0.0
    scheduler._tick()
    assert runs == [1]


def test_trigger_forces_next_fire(scheduler):
    runs = []
    scheduler.add_job("sync", 60_000, lambda: runs.append(1), immediate=False)
    scheduler.trigger("sync")
    scheduler._tick()
    assert runs == [1]


def test_error_backoff_is_applied(scheduler):
    calls = {"boom": 0}
    scheduler.add_job(
        "boom",
        1000,
        lambda: _fail(calls),
        immediate=False,
    )
    scheduler.trigger("boom")
    scheduler._tick()
    after_error = scheduler.next_run_in_s("boom")
    assert scheduler._jobs["boom"].consecutive_errors == 1
    assert after_error >= 1.0  # at least the interval (backoff doubled under it)


def _fail(counter):
    counter["boom"] += 1
    raise RuntimeError("kaboom")


def test_names_unknown_trigger_is_false(scheduler):
    assert scheduler.trigger("nope") is False
    assert scheduler.next_run_in_s("nope") == -1.0
