"""Controllers pulled out of app.run(): tested with fakes, no network, no
real devices."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QApplication

from pulse_hwm.controllers.update_controller import UpdateController
from pulse_hwm.db import Database
from pulse_hwm.lifecycle import ThreadGroup
from pulse_hwm.rgb.drivers.registry import DriverRegistry, FakeDriver
from pulse_hwm.rgb.sensors import sensors_from_snapshot
from pulse_hwm.rgb.worker import RgbWorker


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "pulse.db")
    yield database
    database.close()


# ── fakes ────────────────────────────────────────────────────────────────
class FakeChecker(QObject):
    checked = Signal(object)

    def __init__(self):
        super().__init__()
        self.manual_checks = 0

    def check_now(self, manual: bool = False) -> bool:
        self.manual_checks += int(manual)
        return True


class FakeInstaller(QObject):
    progress = Signal(int, int)
    finished = Signal(object)

    def __init__(self):
        super().__init__()
        self.installed: list[dict] = []
        self.cleared = 0

    def install(self, release: dict) -> bool:
        self.installed.append(release)
        return True

    def clear(self) -> None:
        self.cleared += 1


class FakeAccountTab(QObject):
    install_update_requested = Signal()
    update_dismissed = Signal(str)

    def __init__(self):
        super().__init__()
        self.calls: list[tuple] = []

    def show_update_available(self, release, current_version, forced):
        self.calls.append(("available", release["version"], forced))

    def show_update_progress(self, done, total):
        self.calls.append(("progress", done, total))

    def show_update_done(self, version):
        self.calls.append(("done", version))

    def show_update_error(self, message):
        self.calls.append(("error", message))

    def clear_update_banner(self):
        self.calls.append(("clear",))


class FakeAlerts:
    def __init__(self):
        self.notified: list[tuple] = []

    def notify(self, level, title, message, play_sound=True):
        self.notified.append((level, title))


@dataclass
class Outcome:
    state: str
    release: dict = field(default_factory=dict)
    reason: str = ""
    manual: bool = False


@dataclass
class InstallResult:
    ok: bool
    version: str = ""
    error: str = ""


# ── UpdateController ─────────────────────────────────────────────────────
def test_update_available_toasts_once_and_offers_install(app, db):
    alerts, tab, installer = FakeAlerts(), FakeAccountTab(), FakeInstaller()
    ctl = UpdateController(
        db, alerts, FakeChecker(), installer, "1.0.0", account_tab=tab
    )
    outcome = Outcome("available", {"version": "1.1.0"})
    # NOTE: seeded because is_newer(x, "") is False, so an EMPTY
    # highest-seen is never advanced (pre-existing behavior, flagged
    # separately — it leaves the anti-rollback guard unarmed)
    db.set_setting("update_highest_seen", "1.0.0")

    ctl.on_check_done(outcome)
    ctl.on_check_done(outcome)  # the next periodic check: no second toast

    assert alerts.notified == [("info", "UPDATE AVAILABLE")]
    assert db.get_setting("update_highest_seen") == "1.1.0"
    assert ("available", "1.1.0", False) in tab.calls
    tab.install_update_requested.emit()
    assert installer.installed == [{"version": "1.1.0"}]


def test_install_flow_without_account_tab_does_not_crash(app, db):
    """Regression: the progress/finish handlers used to assume the ACCOUNT
    tab existed (AttributeError on None when accounts are unavailable)."""
    installer = FakeInstaller()
    quits: list[bool] = []
    ctl = UpdateController(
        db,
        FakeAlerts(),
        FakeChecker(),
        installer,
        "1.0.0",
        account_tab=None,
        quit_for_update=lambda: quits.append(True),
    )
    ctl.on_check_done(Outcome("available", {"version": "1.1.0"}))
    ctl.on_install_progress(10, 100)
    ctl.on_install_finished(InstallResult(ok=False, error="bad signature"))
    assert installer.cleared == 1
    events = [row["message"] for row in db.events_since(0)]
    assert "update failed: bad signature" in events


def test_manual_check_error_is_shown(app, db):
    shown: list[str] = []
    ctl = UpdateController(
        db,
        FakeAlerts(),
        FakeChecker(),
        FakeInstaller(),
        "1.0.0",
        show_feedback=shown.append,
    )
    ctl.on_check_done(Outcome("error", reason="offline", manual=True))
    ctl.on_check_done(Outcome("error", reason="offline", manual=False))
    assert shown == ["update check failed: offline"]


# ── RGB pieces ───────────────────────────────────────────────────────────
def test_sensors_from_snapshot_picks_hottest_per_family():
    snapshot = {
        "temps": [
            {"label": "CPU Package", "temp": 61.0},
            {"label": "CPU Core #2", "temp": 70.0},
            {"label": "GPU Core", "temp": 55.0},
            {"label": "NVMe", "temp": 80.0},
            {"label": "CPU Core #3", "temp": None},
        ],
        "mem": {"pct": 42.5},
    }
    assert sensors_from_snapshot(snapshot) == {
        "cpu_temp": 70.0,
        "gpu_temp": 55.0,
        "mem_pct": 42.5,
        "max_temp": 70.0,
    }


def test_sensors_from_empty_snapshot_are_none():
    assert sensors_from_snapshot({}) == {
        "cpu_temp": None,
        "gpu_temp": None,
        "mem_pct": None,
        "max_temp": None,
    }


def test_worker_attaches_first_available_driver(app):
    worker = RgbWorker()
    reported: list[list] = []
    worker.devices_changed.connect(lambda devices: reported.append(devices))
    registry = DriverRegistry((FakeDriver,))
    registry.load()
    worker.attach_first_available(registry)
    assert [d.device_id for d in reported[-1]] == ["fake:0", "fake:1"]


def test_worker_with_no_driver_reports_empty_device_list(app):
    worker = RgbWorker()
    reported: list[list] = []
    worker.devices_changed.connect(lambda devices: reported.append(devices))
    worker.attach_first_available(DriverRegistry(()))
    assert reported == [[]]


# ── ThreadGroup ──────────────────────────────────────────────────────────
def test_thread_group_runs_hooks_then_stops_threads(app):
    group = ThreadGroup()
    hooks: list[str] = []
    first = group.add(QThread(), before_quit=lambda: hooks.append("first"))
    second = group.add(QThread(), before_quit=lambda: 1 / 0)  # must not block
    group.start_all()
    assert first.isRunning() and second.isRunning()

    group.shutdown()

    assert hooks == ["first"]
    assert not first.isRunning() and not second.isRunning()
