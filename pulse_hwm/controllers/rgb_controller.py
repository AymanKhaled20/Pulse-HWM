"""RGB wiring: builds the engine stack and routes events between threads.

Thread map (why this is a QObject on the UI thread):
  * RgbWorker lives on the "rgb-engine" thread and does ALL device I/O —
    including probing which driver is available.
  * RgbManager (mode planner) is Qt-free; its public calls happen here, on
    the UI thread, because this object's slots receive the worker's
    devices_changed signal *queued* onto the UI thread.
  * Plans travel back to the worker through its assignments_requested
    signal, which Qt queues onto the rgb thread.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QThread, QTimer, Slot

from pulse_hwm import app_settings
from pulse_hwm.rgb.drivers.openrgb.driver import OpenRgbDriver
from pulse_hwm.rgb.drivers.registry import DriverRegistry
from pulse_hwm.rgb.effects.catalog import EffectCatalog
from pulse_hwm.rgb.effects.loader import UserEffectStore
from pulse_hwm.rgb.manager import RgbManager
from pulse_hwm.rgb.sensors import sensors_from_snapshot
from pulse_hwm.rgb.worker import RgbThreadBridge, RgbWorker

log = logging.getLogger("pulse.rgb")

# small margin past the hold so the expiry check lands AFTER the deadline
_ALERT_EXPIRY_MARGIN_MS = 50


class RgbController(QObject):
    def __init__(self, db, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._db = db
        _repair_legacy_assignment_keys(db)
        settings = app_settings.load(db)

        self.catalog = EffectCatalog()
        UserEffectStore(db).register_with_catalog(self.catalog)

        # OpenRGB owns every supported RGB device, including the AULA
        # keyboard: one transport in charge means no two drivers fight
        # over the same device. Disabled in Settings → no driver at all,
        # so Pulse never touches the hardware.
        drivers = (
            (OpenRgbDriver(port=settings.rgb_openrgb_port),)
            if settings.rgb_openrgb_enabled
            else ()
        )
        self.registry = DriverRegistry(drivers)
        self.registry.load()

        self.thread = QThread()
        self.thread.setObjectName("rgb-engine")
        self.worker = RgbWorker(self.catalog)
        RgbThreadBridge.attach(self.worker, self.thread)
        self.worker.set_brightness(settings.rgb_brightness)
        self.worker.set_fps(settings.rgb_engine_fps)
        self._start_fps = settings.rgb_engine_fps

        self.manager = RgbManager(
            db, self.catalog, alert_hold_ms=settings.rgb_alert_hold_ms
        )
        # a plan push is a signal emit → queued onto the rgb thread
        self.manager.apply_assignments = self.worker.assignments_requested.emit
        self.worker.devices_changed.connect(self.on_devices_changed)
        # one automatic rescan per run when a device the user set up is
        # missing (OpenRGB's startup detection sometimes misses the AULA)
        self._auto_rescan_done = False

    # ── lifecycle ────────────────────────────────────────────────────────
    def start(self) -> None:
        self.thread.start()
        # both requests are queued, so they run on the rgb thread once its
        # event loop is up. Starting the QTimer directly from the UI thread
        # would leave the render loop stopped (cross-thread timer).
        self.worker.attach_first_available_requested.emit(self.registry)
        self.worker.start_requested.emit(self._start_fps)

    def request_rescan(self) -> None:
        """Queued hardware re-detection on the rgb thread (RESCAN button)."""
        self.worker.rescan_requested.emit()

    def request_shutdown(self) -> None:
        """Queued detach: the engine closes the driver on the rgb thread
        (for OpenRGB this also stops the server process WE spawned). Must
        be emitted before the thread is told to quit."""
        self.worker.detach_requested.emit()

    # ── slots (UI thread) ────────────────────────────────────────────────
    @Slot(list)
    def on_devices_changed(self, devices: list) -> None:
        self.manager.set_driver(
            devices[0].driver_id if devices else "",
            [d.device_id for d in devices],
        )
        self.manager.reconsider()
        self._maybe_auto_rescan(devices)

    def _maybe_auto_rescan(self, devices: list) -> None:
        """Rescan once if a device with a saved effect did not show up."""
        if self._auto_rescan_done or not devices:
            return
        blob = app_settings.load(self._db).rgb_device_assignment
        missing = missing_assigned_devices(
            blob, devices[0].driver_id, [d.device_id for d in devices]
        )
        if not missing:
            return
        self._auto_rescan_done = True
        log.info("saved RGB device(s) %s not found; rescanning once", missing)
        self.request_rescan()

    @Slot(dict)
    def on_hardware_snapshot(self, snapshot: dict) -> None:
        self.worker.sensors_requested.emit(sensors_from_snapshot(snapshot))

    @Slot(int)
    def set_brightness(self, pct: int) -> None:
        self.worker.brightness_requested.emit(int(pct))

    def on_alert(self, level: str, title: str) -> None:
        """AlertManager dispatch listener (error-level alerts only). Flashes
        the devices, then a one-shot timer reverts them when the hold ends
        — no need to poll every 500 ms for expiry."""
        hold_ms = app_settings.load(self._db).rgb_alert_hold_ms
        self.manager.set_alert_hold_ms(hold_ms)
        self.manager.handle_alert()
        QTimer.singleShot(hold_ms + _ALERT_EXPIRY_MARGIN_MS, self.manager.maintain)


def missing_assigned_devices(
    blob: str, driver_id: str, found_ids: list[str]
) -> list[str]:
    """Device ids the user saved an effect for under `driver_id` that the
    driver did not report this time (sorted, for stable log lines)."""
    from pulse_hwm.rgb import assignment_store

    prefix = f"{driver_id}/"
    saved = [
        key[len(prefix) :]
        for key in assignment_store.parse_blob(blob)
        if key.startswith(prefix)
    ]
    found = set(found_ids)
    return sorted(device_id for device_id in saved if device_id not in found)


def _repair_legacy_assignment_keys(db) -> None:
    """Assignments saved while a "composite" wrapper driver existed were
    keyed "composite/<device>", which the planner never matched. Move them
    to "openrgb/<device>" once so the user's saved effects start working."""
    from pulse_hwm.rgb import assignment_store

    blob = app_settings.load(db).rgb_device_assignment
    repaired = assignment_store.rename_driver(blob, "composite", "openrgb")
    if repaired != blob:
        app_settings.save_field(db, "rgb_device_assignment", repaired)
        log.info("moved legacy composite/* RGB assignments to openrgb/*")
