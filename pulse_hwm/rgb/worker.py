"""Qt thread bridge for the render engine — the ONLY Qt-aware file in the
engine layer.

Pattern matches the existing collectors (HardwareThreadBridge etc.): a
QObject with slots/signals moved onto a dedicated QThread; a QTimer on that
thread drives engine.tick() at rgb_engine_fps. All driver/effect run time
(sockets, ctypes, HID) happens on this thread — never on the UI thread.

The engine instance itself is plain Python: worker.py translates engine
results into signals and swallows engine.exceptions into error frame
reporting, so a dead device degrades instead of crashing the thread.
"""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from pulse_hwm.rgb.drivers.base import RgbDriver
from pulse_hwm.rgb.effects.catalog import EffectCatalog
from pulse_hwm.rgb.engine import DeviceAssignment, RgbEngine

log = logging.getLogger("pulse.rgb")

# the same failing device reports an error EVERY tick (30+/s); only log a
# repeat of the same message this often so pulse.log stays readable
_ERROR_LOG_REPEAT_S = 10.0
# how often the render loop asks the driver about unplugged/replugged devices
_POLL_CHANGES_S = 1.0
# how often the RGB tab gets a status + preview report. 5/s looks live but
# keeps cross-thread traffic and repainting tiny compared with 30 FPS
_STATUS_REPORT_S = 0.2


class RgbWorker(QObject):
    """Owns the RgbEngine on its thread. Slots are invoked via signals from
    the UI thread; signals report outcomes back. All calls are queued by
    Qt's signal delivery, so no explicit locking is needed."""

    devices_changed = Signal(list)  # list[RgbDevice] — manager leg
    devices_reported = Signal(str, str, list)  # (driver_id, name, devices) — tab leg
    rendered = Signal(int)  # frames accepted this tick
    driver_error = Signal(str)  # attach/probe failure for the UI strip
    # throttled live report for the RGB tab (plain data only, so nothing
    # shared crosses threads): {"fps": float, "driven": int, "ok": int,
    # "frames": {device_id: ["#RRGGBB", ...]}, "errors": {device_id: str}}
    status_reported = Signal(dict)
    start_requested = Signal(int)  # queued render-loop startup
    attach_requested = Signal(object)  # RgbDriver handed off to this thread
    # DriverRegistry handed off to this thread: probe() does socket/file I/O,
    # so even choosing WHICH driver to attach happens here, not on the UI
    attach_first_available_requested = Signal(object)
    brightness_requested = Signal(int)  # queued like every other control call
    sensors_requested = Signal(dict)  # hardware snapshot push, queued
    assignments_requested = Signal(dict)  # {device_id: DeviceAssignment | None}
    detach_requested = Signal()  # queued close: releases driver (OpenRGB proc)

    def __init__(
        self, catalog: EffectCatalog | None = None, parent: QObject | None = None
    ):
        """Own the engine and the frame timer; both live on the rgb thread."""
        super().__init__(parent)
        self.engine = RgbEngine(catalog)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._fps = 30
        self._last_success_log = 0.0
        self._last_error_logged = ("", 0.0)  # (message, monotonic time)
        self._last_change_poll = 0.0
        # measured frame rate for the status report: ticks counted since the
        # last report, divided by the time since then
        self._last_status_report = time.monotonic()
        self._ticks_since_report = 0
        # queued onto OUR thread: driver probe/open I/O never runs on the
        # caller's thread during a mode/device switch
        self.attach_requested.connect(self.attach)
        self.attach_first_available_requested.connect(self.attach_first_available)
        self.start_requested.connect(self.start)
        self.brightness_requested.connect(self.set_brightness)
        # through OUR slot, not engine.set_sensors directly: the engine is a
        # plain object, and Qt runs plain callables on the EMITTING thread
        self.sensors_requested.connect(self.set_sensors)
        self.assignments_requested.connect(self.apply_assignments)
        self.detach_requested.connect(self.detach)

    # ── slots (invoked cross-thread via signals) ──────────────────────
    @Slot(int)
    def start(self, fps: int) -> None:
        self._fps = max(5, min(60, int(fps)))
        self._timer.start(max(16, int(1000 / self._fps)))

    @Slot()
    def stop(self) -> None:
        self._timer.stop()

    @Slot(object)
    def attach_first_available(self, registry) -> None:
        """Probe every registered driver (on THIS thread) and attach the
        first one that is available; report "no driver" otherwise."""
        try:
            available = registry.available()
        except Exception as exc:  # a broken registry must not kill the thread
            log.exception("driver probe failed")
            available = []
            self.driver_error.emit(f"driver probe failed: {exc}")
        if not available:
            log.info("no RGB driver available")
            self.detach()  # clears the manager's device list via devices_changed
            return
        self.attach(available[0])

    @Slot(object)
    def attach(self, driver: RgbDriver) -> None:
        try:
            devices = self.engine.attach_driver(driver)
            log.info(
                "attached driver=%s devices=%s",
                driver.driver_id,
                [(d.device_id, d.leds) for d in devices],
            )
            self.devices_changed.emit(list(devices))
            # the tab's status slots want (driver_id, name, devices) — see
            # the phase 27 note below about the old one-arg emit failing
            self.devices_reported.emit(driver.driver_id, driver.name, list(devices))
        except Exception as exc:
            log.exception("driver open failed")
            self.driver_error.emit(f"driver open failed: {exc}")
            self.devices_reported.emit("", "", [])  # leave the "probing…" state

    @Slot()
    def detach(self) -> None:
        self.engine.detach_driver()
        self.devices_changed.emit([])
        self.devices_reported.emit("", "", [])

    def set_assignment(
        self, device_id: str, assignment: DeviceAssignment | None
    ) -> None:
        self.engine.set_assignment(device_id, assignment)

    @Slot(dict)
    def apply_assignments(self, assignments: dict) -> None:
        """Slot for cross-thread plan pushes: a Signal→slot connection makes
        this run HERE (worker thread), so the plan applies re-entrancy-free."""
        log.info(
            "apply plan: %s",
            ", ".join(
                f"{did}={getattr(a, 'effect_id', None)}/{getattr(a, 'params', {})}"
                for did, a in assignments.items()
            ),
        )
        for device_id, assignment in assignments.items():
            self.engine.set_assignment(device_id, assignment)

    @Slot(int)
    def set_brightness(self, pct: int) -> None:
        self.engine.set_brightness(pct)

    @Slot(dict)
    def set_sensors(self, sensors: dict) -> None:
        self.engine.set_sensors(sensors)

    def set_fps(self, fps: int) -> None:
        self._fps = max(5, min(60, int(fps)))
        if self._timer.isActive():
            self._timer.setInterval(max(16, int(1000 / self._fps)))

    # ── internals ──────────────────────────────────────────────────────
    def _on_tick(self) -> None:
        """Render and send one frame, then maybe report status to the tab."""
        self._poll_device_changes()
        self._ticks_since_report += 1
        try:
            applied = self.engine.tick()
        except Exception as exc:  # ticking must survive catastrophic state
            self._log_error_throttled(f"render failed: {exc}")
            self._report_status(extra_error=f"render failed: {exc}")
            return
        self.rendered.emit(applied)
        if applied:
            now = time.monotonic()
            if now - self._last_success_log >= 1.0:
                log.debug("tick applied=%d", applied)
                self._last_success_log = now
        if self.engine.last_error:
            self._log_error_throttled(f"tick error: {self.engine.last_error}")
        # per-frame errors now travel in the throttled status report (they
        # used to be emitted 30x a second and never cleared in the UI)
        self._report_status()

    def _report_status(self, extra_error: str = "") -> None:
        """At most every _STATUS_REPORT_S: tell the RGB tab what is being
        sent right now (preview frames as hex strings) and what is failing."""
        now = time.monotonic()
        elapsed = now - self._last_status_report
        if elapsed < _STATUS_REPORT_S:
            return
        fps = self._ticks_since_report / elapsed if elapsed > 0 else 0.0
        self._last_status_report = now
        self._ticks_since_report = 0
        frames = {
            device_id: [color.to_hex() for color in frame]
            for device_id, frame in self.engine.last_frames.items()
        }
        errors = dict(self.engine.device_errors)
        if extra_error:
            errors["*"] = extra_error  # "*" = the whole engine, not one device
        self.status_reported.emit(
            {
                "fps": round(fps, 1),
                "driven": len(frames),
                "ok": len([d for d in frames if d not in errors]),
                "frames": frames,
                "errors": errors,
            }
        )

    def _poll_device_changes(self) -> None:
        """About once a second: did the driver's device list change? If so,
        report it exactly like a fresh attach so the manager re-plans and
        the RGB tab redraws its device rows."""
        now = time.monotonic()
        if now - self._last_change_poll < _POLL_CHANGES_S:
            return
        self._last_change_poll = now
        try:
            devices = self.engine.poll_driver()
        except Exception as exc:  # a broken poll must not stop rendering
            self._log_error_throttled(f"device poll failed: {exc}")
            return
        driver = self.engine.driver
        if devices is None or driver is None:
            return
        log.info(
            "devices changed: driver=%s devices=%s",
            driver.driver_id,
            [(d.device_id, d.leds) for d in devices],
        )
        self.devices_changed.emit(list(devices))
        self.devices_reported.emit(driver.driver_id, driver.name, list(devices))

    def _log_error_throttled(self, message: str) -> None:
        """Log a NEW error at once; the same error again only every
        _ERROR_LOG_REPEAT_S seconds (it would otherwise repeat per frame)."""
        last_message, last_time = self._last_error_logged
        now = time.monotonic()
        if message != last_message or now - last_time >= _ERROR_LOG_REPEAT_S:
            log.warning(message)
            self._last_error_logged = (message, now)


class RgbThreadBridge:
    """attach(worker, thread) — same shape as the collector bridges so app.py
    wiring reads identically to the hardware/websites/processes threads."""

    @staticmethod
    def attach(worker: RgbWorker, thread: QThread) -> None:
        worker.moveToThread(thread)
        thread.finished.connect(worker.stop)
