from __future__ import annotations

import logging
import sys
import traceback

log = logging.getLogger("pulse.app")


def run() -> int:
    """Start the desktop application and wire its background services.

    This is the composition root: it BUILDS the services and controllers
    and CONNECTS them — the behavior itself lives in the controllers
    (pulse_hwm/controllers/) and services, where it can be tested.
    """
    if "--selftest" in sys.argv:
        return _selftest()
    from pulse_hwm import config
    from pulse_hwm.logging_setup import configure_logging

    config.ensure_dirs()
    config.env()  # loads .env first, so PULSE_LOG_LEVEL is visible below
    configure_logging(config.data_dir())

    from PySide6.QtCore import Qt, QThread, QThreadPool, QTimer
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from pulse_hwm import APP_NAME, __version__, app_settings
    from pulse_hwm.single_instance import (
        SingleInstance,
        auth_urls_from_args,
        try_handoff,
    )

    QGuiApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps)

    # single-instance: a login-link click re-launches the exe while we
    # may already be running. If an instance exists, hand the URL over
    # and exit BEFORE creating QApplication (QLocalSocket works pre-loop;
    # waitFor* calls are synchronous).
    if not try_handoff(sys.argv[1:]):
        return 0

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)  # close = minimize to tray
    log.info("starting %s v%s", APP_NAME, __version__)

    # named mutex matches installer/pulse-hwm.iss AppMutex=PulseHWMAppMutex
    # so Inno detects (and silently closes) a live instance during updates.
    # Kept in a local for the whole run: releasing it would drop the mutex.
    _app_mutex = _create_app_mutex()  # noqa: F841

    # —— storage + settings + theme ————————————————————————————————————
    from pulse_hwm.db import open_default
    from pulse_hwm.ui.theme import load_fonts
    from pulse_hwm.ui.theme_manager import ThemeManager

    db = open_default()
    db.seed_default_sites()
    settings = app_settings.load(db)

    # Fonts first (QFontDatabase has no styling), then the SAVED theme —
    # before any window is built, so the app never flashes default colors.
    load_fonts()
    theme_manager = ThemeManager(app, db)
    theme_manager.bootstrap(
        settings.theme_color, settings.theme_font, settings.font_size
    )

    # —— collectors (each on its own thread) ———————————————————————————
    from pulse_hwm.collectors.hardware import HardwareThreadBridge
    from pulse_hwm.collectors.processes import ProcessesThreadBridge
    from pulse_hwm.collectors.websites import WebsiteThreadBridge
    from pulse_hwm.lifecycle import ThreadGroup

    threads = ThreadGroup()
    hardware_thread = threads.add(_named_thread(QThread, "hardware-collector"))
    collector = HardwareThreadBridge().attach(
        hardware_thread, settings.hardware_interval_ms
    )
    websites_thread = threads.add(
        _named_thread(QThread, "websites-monitor"), wait_ms=3000
    )
    monitor = WebsiteThreadBridge().attach(
        websites_thread,
        db,
        interval_s=settings.website_interval_s,
        timeout_s=settings.website_timeout_s,
        ssl_warn_days=settings.ssl_warn_days,
    )
    # the (heavier) full process scan pauses itself whenever the
    # Processes tab is hidden
    processes_thread = threads.add(_named_thread(QThread, "processes-scan"))
    processes_collector = ProcessesThreadBridge.attach(
        processes_thread,
        interval_s=settings.process_interval_s,
        max_rows=settings.process_max_rows,
    )

    # —— alerts ————————————————————————————————————————————————————————
    from pulse_hwm.alerts.notifier import AlertChannels, AlertManager
    from pulse_hwm.controllers.alert_relay import SiteAlertRelay

    # channels come from the SAVED settings — hardcoding defaults here made
    # the SETTINGS toggles revert at every restart until APPLY was pressed
    alerts = AlertManager(
        db,
        AlertChannels(
            sound=settings.sound_enabled,
            desktop=settings.desktop_enabled,
            webhooks=settings.webhooks_enabled,
        ),
    )
    site_alert_relay = SiteAlertRelay(alerts)

    # —— cloud: accounts, sync, updates ————————————————————————————————
    from pulse_hwm.cloud.config import auth_config
    from pulse_hwm.cloud.oauth import OauthCoordinator
    from pulse_hwm.cloud.rest import CloudClient
    from pulse_hwm.cloud.session import SessionManager
    from pulse_hwm.cloud.sync_engine import SyncEngine
    from pulse_hwm.cloud.updates.checker import UpdateChecker
    from pulse_hwm.cloud.updates.installer import UpdateInstaller

    auth_cfg = auth_config()
    cloud = CloudClient(auth_cfg.base_url, auth_cfg.publishable_key)
    session = SessionManager(cloud)
    coordinator = OauthCoordinator(cloud)
    sync_engine = SyncEngine(session, cloud, db, parent=None)
    sync_engine.attach_pool(QThreadPool.globalInstance())

    # updates: registered+active users get in-app updates. Metadata comes
    # from /updates/latest (Bearer); the binary from /dl streamed out of the
    # private R2 bucket. Everything passes Ed25519 + sha256 before spawn.
    checker = UpdateChecker(session, cloud, db, __version__, auth_cfg.base_url)
    updates_dir = config.data_dir() / "updates"
    updates_dir.mkdir(parents=True, exist_ok=True)
    installer = UpdateInstaller(session, auth_cfg.base_url, updates_dir)

    # —— RGB engine (own thread; see controllers/rgb_controller.py) ————————
    from pulse_hwm.controllers.rgb_controller import RgbController

    rgb = RgbController(db)
    threads.add(rgb.thread, before_quit=rgb.request_shutdown)
    collector.updated.connect(rgb.on_hardware_snapshot)

    # —— window ————————————————————————————————————————————————————————
    from pulse_hwm.ui.main_window import MainWindow

    window = MainWindow(
        hardware_collector=collector,
        websites_monitor=monitor,
        db=db,
        alerts=alerts,
        processes_collector=processes_collector,
        theme_manager=theme_manager,
        session_manager=session,
        oauth_coordinator=coordinator,
        auth_configured=auth_cfg.is_configured(),
        rgb_manager=rgb.manager,
        rgb_worker=rgb.worker,
    )
    window.show()

    # —— controllers ———————————————————————————————————————————————————
    from pulse_hwm.controllers.sync_controller import SyncController
    from pulse_hwm.controllers.update_controller import UpdateController
    from pulse_hwm.scheduler import Scheduler

    # created early so the sign-in hook below can trigger jobs; jobs are
    # added further down and the ticker only starts at scheduler.start()
    scheduler = Scheduler()

    sync_controller = SyncController(
        db,
        session,
        coordinator,
        sync_engine,
        window,
        theme_manager,
        monitor,
        on_signed_in=lambda: scheduler.trigger("update-check"),
    )
    update_controller = UpdateController(
        db,
        alerts,
        checker,
        installer,
        current_version=__version__,
        account_tab=window.account_tab,
        show_feedback=window.show_account_feedback,
        quit_for_update=window.quit_for_update,
    )
    window.update_check_requested.connect(update_controller.check_now_manual)

    # —— incoming auth callbacks ————————————————————————————————————————
    single = SingleInstance()
    single.url_received.connect(sync_controller.handle_incoming_url)
    # cold launch can itself carry the callback (the link was clicked
    # while the app was closed, so the exe relaunched with the URL in
    # argv) — the pipe only covers warm handoffs between live instances
    for url in auth_urls_from_args(sys.argv[1:]):
        QTimer.singleShot(100, lambda u=url: sync_controller.handle_incoming_url(u))
    QTimer.singleShot(20, sync_controller.resume_session)

    rgb.start()

    def shutdown() -> None:
        scheduler.stop()
        threads.shutdown()
        single.close()
        cloud.close()
        db.close()
        log.info("shut down cleanly")

    app.aboutToQuit.connect(shutdown)

    # being a good citizen: apply boot-time resource mode from settings
    from pulse_hwm.processes import set_low_priority_mode, trim_working_set

    set_low_priority_mode(settings.limit_resources)

    # —— one scheduler for every background job ————————————————————————
    # Jobs read settings at fire time (cheap: app_settings.load is cached)
    # so Settings toggles apply mid-session without a restart.
    def trim_memory() -> None:
        if app_settings.load(db).limit_resources:
            trim_working_set()

    def prune_now() -> dict:
        return db.prune(app_settings.load(db).retention_days)

    def check_updates_job() -> None:
        if app_settings.load(db).update_check_enabled:
            checker.check_now()

    scheduler.add_job("sync", 60_000, sync_engine.sync_now, immediate=True)
    scheduler.add_job(
        "update-check", 6 * 3600 * 1000, check_updates_job, immediate=True
    )
    scheduler.add_job("trim", 15 * 60 * 1000, trim_memory, immediate=False)
    scheduler.add_job("prune", 24 * 3600 * 1000, prune_now, immediate=False)
    scheduler.job_event.connect(_log_job_errors)
    scheduler.start()

    # reactive alerts: error-level alerts flash the RGB devices for
    # rgb_alert_hold_ms, then they revert to the temperature map
    alerts.add_dispatch_listener(rgb.on_alert)
    alerts.attach_tray(window.tray)
    monitor.site_state_changed.connect(site_alert_relay.on_site_state_changed)
    monitor.checked.connect(window.on_site_checked)

    prune_now()
    threads.start_all()

    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.warning("system tray unavailable")
    return app.exec()


def _named_thread(thread_class, name: str):
    thread = thread_class()
    thread.setObjectName(name)
    return thread


def _create_app_mutex():
    """Optional probe: the app runs fine without the mutex (the installer
    just can't auto-close us), so any failure returns None."""
    try:
        import win32event

        return win32event.CreateMutexW(None, False, "PulseHWMAppMutex")
    except Exception:
        return None


def _log_job_errors(name: str, outcome: str) -> None:
    if outcome.startswith("error"):
        log.warning("scheduled job %s failed: %s", name, outcome)


def _selftest() -> int:
    """Headless check: temp sources + safe-DB probe + sound probe. No GUI."""
    import ctypes
    import json

    admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    probes = {}

    # —— sound probe ——————————————————————————————————————
    try:
        from pulse_hwm.alerts import notifier as _notifier

        probes["sound_played"] = _notifier.play_alert_sound()
        if not probes["sound_played"] and _notifier.last_sound_error:
            probes["sound_played"] = f"FAILED: {_notifier.last_sound_error}"
    except Exception:
        probes["sound_played"] = f"EXC: {traceback.format_exc(limit=2)}"

    # —— add-site probe against the real DB ———————————————
    name = "pulse-selftest-probe"
    added = None
    try:
        from pulse_hwm.db import open_default

        db = open_default()
        stale = [s for s in db.get_sites(include_disabled=True) if s["name"] == name]
        for s in stale:
            db.remove_site(int(s["id"]))
        db.add_site(name=name, url="https://example.com/")
        for s in db.get_sites(include_disabled=True):
            if s["name"] == name:
                added = dict(s)
                break
        for s in db.get_sites(include_disabled=True):
            if s["name"] == name:
                db.remove_site(int(s["id"]))
        db.close()
        probes["add_site"] = "OK (row created)" if added else "FAILED: row not found"
    except Exception:
        probes["add_site"] = f"EXC: {traceback.format_exc(limit=3)}"

    from pulse_hwm.collectors.lhm import LibreSensors, is_available

    # —— RGB probe: the SAME OpenRGB path the app uses — enumerate every
    # device and give each one red frame, best-effort ————————————————
    try:
        from pulse_hwm.rgb.drivers.openrgb.driver import OpenRgbDriver
        from pulse_hwm.rgb.model import RgbColor

        rgb_driver = OpenRgbDriver()
        rgb_probe = rgb_driver.probe()
        rgb_report = {
            "openrgb_available": rgb_probe.available,
            "openrgb_reason": rgb_probe.reason,
        }
        if rgb_probe.available:
            rgb_driver.open()
            try:
                devices = rgb_driver.devices()
                rgb_report["openrgb_devices"] = [
                    {
                        "id": device.device_id,
                        "name": device.name,
                        "leds": device.leds,
                        "frame_set": rgb_driver.set_frame(
                            device.device_id, [RgbColor(255, 0, 0)] * device.leds
                        ),
                    }
                    for device in devices
                ]
            finally:
                rgb_driver.close()  # also stops the server if WE spawned it
        probes["rgb"] = rgb_report
    except Exception:
        probes["rgb"] = f"EXC: {traceback.format_exc(limit=2)}"

    report = {"admin": admin, "lhm_runtime": is_available(), "sensors": [], **probes}
    if is_available():
        sensors = LibreSensors()
        rows = sensors.read()
        if rows:
            report["sensors"] = [
                {"label": r["label"], "temp": round(r["temp"], 1)} for r in rows
            ]
        else:
            report["lhm_error"] = (
                sensors.last_error or "no temperature sensors returned"
            )
        import time as _time

        _time.sleep(3)
        rows2 = sensors.read()
        report["sensors_second_read"] = [
            {"label": r["label"], "temp": round(r["temp"], 1)} for r in (rows2 or [])
        ]
        if getattr(sensors, "_obj", None) is not None:
            hw_list = []
            for hw in list(sensors._obj.Hardware):
                entry = {
                    "name": hw.Name,
                    "type": str(hw.HardwareType),
                    "n_sensors": len(list(hw.Sensors)),
                    "temps": [
                        s.Name
                        for s in hw.Sensors
                        if s.SensorType.ToString() == "Temperature"
                    ][:6],
                }
                try:
                    rep = hw.GetReport()
                    if rep:
                        entry["report"] = str(rep)[:20000]
                except Exception as exc:
                    entry["report_exc"] = str(exc)
                hw_list.append(entry)
            report["hardware"] = hw_list
    rows = report["sensors"]
    cpu_temps = [r for r in rows if "CPU" in r["label"]]
    gpu_temps = [r for r in rows if "GPU" in r["label"]]

    out = {
        **report,
        "cpu_temps": len(cpu_temps),
        "gpu_temps": len(gpu_temps),
    }
    rendered = json.dumps(out, indent=2)
    print(rendered)
    if "--selftest-out" in sys.argv:
        path = sys.argv[sys.argv.index("--selftest-out") + 1]
        with open(path, "w", encoding="utf-8") as f:
            f.write(rendered)
    return 0 if (rows or added) else 2
