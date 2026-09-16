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

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from pulse_hwm.rgb.drivers.base import RgbDriver
from pulse_hwm.rgb.effects.catalog import EffectCatalog
from pulse_hwm.rgb.engine import DeviceAssignment, RgbEngine


class RgbWorker(QObject):
    """Owns the RgbEngine on its thread. Slots are invoked via signals from
    the UI thread; signals report outcomes back. All calls are queued by
    Qt's signal delivery, so no explicit locking is needed."""

    devices_changed = Signal(list)  # list[RgbDevice]
    rendered = Signal(int)  # frames accepted this tick
    driver_error = Signal(str)  # human-readable failure for the UI strip

    def __init__(
        self, catalog: EffectCatalog | None = None, parent: QObject | None = None
    ):
        super().__init__(parent)
        self.engine = RgbEngine(catalog)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._fps = 30

    # ── slots (invoked cross-thread via signals) ──────────────────────
    def start(self, fps: int) -> None:
        self._fps = max(5, min(60, int(fps)))
        self._timer.start(max(16, int(1000 / self._fps)))

    def stop(self) -> None:
        self._timer.stop()

    def attach(self, driver: RgbDriver) -> None:
        try:
            devices = self.engine.attach_driver(driver)
            self.devices_changed.emit(list(devices))
        except Exception as exc:
            self.driver_error.emit(f"driver open failed: {exc}")

    def detach(self) -> None:
        self.engine.detach_driver()
        self.devices_changed.emit([])

    def set_assignment(
        self, device_id: str, assignment: DeviceAssignment | None
    ) -> None:
        self.engine.set_assignment(device_id, assignment)

    def set_brightness(self, pct: int) -> None:
        self.engine.set_brightness(pct)

    def set_fps(self, fps: int) -> None:
        self._fps = max(5, min(60, int(fps)))
        if self._timer.isActive():
            self._timer.setInterval(max(16, int(1000 / self._fps)))

    # ── internals ──────────────────────────────────────────────────────
    def _on_tick(self) -> None:
        try:
            applied = self.engine.tick()
        except Exception as exc:  # ticking must survive catastrophic state
            self.driver_error.emit(f"render failed: {exc}")
            return
        self.rendered.emit(applied)
        if self.engine.last_error:
            self.driver_error.emit(self.engine.last_error)


class RgbThreadBridge:
    """attach(worker, thread) — same shape as the collector bridges so app.py
    wiring reads identically to the hardware/websites/processes threads."""

    @staticmethod
    def attach(worker: RgbWorker, thread: QThread) -> None:
        worker.moveToThread(thread)
        thread.finished.connect(worker.stop)
