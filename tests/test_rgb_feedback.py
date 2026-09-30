"""Per-device params in the assignment blob, and the engine/worker feedback
that powers the RGB tab's live preview and status line."""

from __future__ import annotations

from pulse_hwm.rgb.assignment_store import assign, entry_of, set_params
from pulse_hwm.rgb.drivers.registry import FakeDriver
from pulse_hwm.rgb.effects.catalog import EffectCatalog
from pulse_hwm.rgb.engine import DeviceAssignment, RgbEngine
from pulse_hwm.rgb.manager import parse_assignment_blob
from pulse_hwm.rgb.model import RgbColor
from pulse_hwm.ui.widgets.led_strip import downsample


# ── assignment store: params ────────────────────────────────────────────
def test_assign_stores_params():
    """A device's effect settings are saved with its assignment."""
    blob = assign(
        "{}", "openrgb", "openrgb:0", "static", True, params={"color": "#00FF00"}
    )
    assert entry_of(blob, "openrgb", "openrgb:0")["params"] == {"color": "#00FF00"}


def test_assign_without_params_keeps_the_stored_color():
    """Switching effect without new settings keeps the saved color."""
    blob = assign(
        "{}", "openrgb", "openrgb:0", "static", True, params={"color": "#00FF00"}
    )
    blob = assign(blob, "openrgb", "openrgb:0", "wave", True)
    entry = entry_of(blob, "openrgb", "openrgb:0")
    assert entry["effect"] == "wave"
    assert entry["params"] == {"color": "#00FF00"}


def test_set_params_merges_into_existing_entry():
    """New settings are merged in, not replacing the saved ones."""
    blob = assign(
        "{}", "openrgb", "openrgb:0", "wave", True, params={"color": "#00FF00"}
    )
    blob = set_params(blob, "openrgb", "openrgb:0", {"speed": 0.9})
    assert entry_of(blob, "openrgb", "openrgb:0")["params"] == {
        "color": "#00FF00",
        "speed": 0.9,
    }


def test_set_params_without_entry_changes_nothing():
    """Settings for an unassigned device are ignored, not invented."""
    assert set_params("{}", "openrgb", "openrgb:0", {"color": "#00FF00"}) == "{}"


def test_planner_uses_the_stored_device_color():
    """The planner renders the device's saved color, not the default."""
    # the bug this fixes: STATIC in EFFECTS mode was always the default yellow
    blob = assign(
        "{}", "openrgb", "openrgb:0", "static", True, params={"color": "#00FF00"}
    )
    assignment = parse_assignment_blob(blob, EffectCatalog(), "openrgb", "openrgb:0")
    assert assignment.params["color"] == "#00FF00"


def test_planner_revalidates_hand_edited_params():
    """Garbage settings from the database are clamped, never crash."""
    # stored params are untrusted: out-of-range/garbage values are clamped
    blob = assign(
        "{}", "openrgb", "openrgb:0", "wave", True, params={"speed": 50, "color": "x"}
    )
    assignment = parse_assignment_blob(blob, EffectCatalog(), "openrgb", "openrgb:0")
    assert assignment.params["speed"] == 1.0
    # garbage color degrades to black (RgbColor.from_hex contract), no crash
    assert assignment.params["color"] == "#000000"


# ── engine feedback ─────────────────────────────────────────────────────
def _engine() -> tuple[RgbEngine, FakeDriver]:
    """An engine with the fake driver (two 8-LED devices) attached."""
    engine = RgbEngine()
    driver = FakeDriver()
    engine.attach_driver(driver)
    return engine, driver


def test_engine_remembers_the_frame_it_sent():
    """The preview shows exactly the frame sent, only for driven devices."""
    engine, _driver = _engine()
    engine.set_assignment("fake:0", DeviceAssignment("static", {"color": "#FF0000"}))
    engine.tick()
    assert engine.last_frames["fake:0"] == [RgbColor(255, 0, 0)] * 8
    assert "fake:1" not in engine.last_frames  # not driven → no preview


def test_preview_frame_includes_brightness():
    """The preview frame is the brightness-scaled one the LEDs get."""
    engine, _driver = _engine()
    engine.set_brightness(50)
    engine.set_assignment("fake:0", DeviceAssignment("static", {"color": "#FF0000"}))
    engine.tick()
    assert engine.last_frames["fake:0"][0] == RgbColor(127, 0, 0)


def test_device_error_recorded_then_cleared_when_it_works_again():
    """A failing device is reported, then forgiven on its next good frame."""
    engine, driver = _engine()
    engine.set_assignment("fake:0", DeviceAssignment("static"))
    driver.fail_device = "fake:0"
    engine.tick()
    assert "fake:0" in engine.device_errors
    driver.fail_device = None
    engine.tick()
    assert "fake:0" not in engine.device_errors


def test_clearing_an_assignment_clears_its_preview():
    """Removing a device's effect removes its preview frame too."""
    engine, _driver = _engine()
    engine.set_assignment("fake:0", DeviceAssignment("static"))
    engine.tick()
    engine.set_assignment("fake:0", None)
    assert "fake:0" not in engine.last_frames


def test_disabling_an_assignment_clears_its_old_error():
    """A disabled device drops its old error (no stale PROBLEM status)."""
    engine, driver = _engine()
    engine.set_assignment("fake:0", DeviceAssignment("static"))
    driver.fail_device = "fake:0"
    engine.tick()
    assert "fake:0" in engine.device_errors
    engine.set_assignment("fake:0", DeviceAssignment("static", enabled=False))
    engine.tick()
    assert "fake:0" not in engine.device_errors


# ── worker status report ────────────────────────────────────────────────
def test_worker_status_report_carries_hex_frames_and_errors():
    """The worker's report has hex frames, errors, counts, and is throttled."""
    from PySide6.QtWidgets import QApplication

    from pulse_hwm.rgb.worker import RgbWorker

    _app = QApplication.instance() or QApplication([])
    worker = RgbWorker()
    driver = FakeDriver()
    worker.engine.attach_driver(driver)
    worker.engine.set_assignment(
        "fake:0", DeviceAssignment("static", {"color": "#00FF00"})
    )
    worker.engine.set_assignment("fake:1", DeviceAssignment("static"))
    driver.fail_device = "fake:1"
    reports: list[dict] = []
    worker.status_reported.connect(reports.append)

    worker._last_status_report = 0.0  # pretend the last report was long ago
    worker._on_tick()

    report = reports[-1]
    assert report["frames"]["fake:0"] == ["#00FF00"] * 8
    assert "fake:1" in report["errors"]
    assert report["driven"] == 2
    assert report["ok"] == 1

    worker._on_tick()  # right after: throttled, no second report
    assert len(reports) == 1


# ── preview downsampling ────────────────────────────────────────────────
def test_downsample_keeps_short_frames():
    """Frames under the limit are shown unchanged."""
    assert downsample(["#000000"] * 5, limit=8) == ["#000000"] * 5


def test_downsample_keeps_first_and_last():
    """Long frames shrink to the limit but keep both ends."""
    colors = [f"#{i:06X}" for i in range(100)]
    shown = downsample(colors, limit=10)
    assert len(shown) == 10
    assert shown[0] == colors[0]
    assert shown[-1] == colors[-1]
