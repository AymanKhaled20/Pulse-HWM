from __future__ import annotations

import sys
import traceback


def run() -> int:
    """Start the desktop application and wire its background services."""
    if "--selftest" in sys.argv:
        return _selftest()
    from pulse_hwm import config

    config.ensure_dirs()

    from PySide6.QtCore import Qt, QThread, QTimer
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from pulse_hwm import APP_NAME, __version__
    from pulse_hwm.collectors.hardware import HardwareThreadBridge
    from pulse_hwm.db import open_default
    from pulse_hwm.ui.main_window import MainWindow
    from pulse_hwm.ui.theme import load_fonts

    QGuiApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps)

    # single-instance: a login-link click re-launches the exe while we
    # may already be running. If an instance exists, hand the URL over
    # and exit BEFORE creating QApplication (QLocalSocket works pre-loop;
    # waitFor* calls are synchronous).
    from pulse_hwm.single_instance import try_handoff

    if not try_handoff(sys.argv[1:]):
        return 0

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)  # close = minimize to tray

    # named mutex matches installer/pulse-hwm.iss AppMutex=PulseHWMAppMutex
    # so Inno detects (and silently closes) a live instance during updates
    _app_mutex = None
    try:  # optional probe: the app runs fine without it
        import win32event

        _app_mutex = win32event.CreateMutexW(None, False, "PulseHWMAppMutex")
    except Exception:
        _app_mutex = None

    db = open_default()
    db.seed_default_sites()

    from pulse_hwm import app_settings
    from pulse_hwm.ui.theme_manager import ThemeManager

    settings = app_settings.load(db)

    # Fonts first (QFontDatabase has no styling), then the SAVED theme —
    # before any window is built, so the app never flashes default colors.
    load_fonts()
    theme_manager = ThemeManager(app, db)
    theme_manager.bootstrap(
        settings.theme_color, settings.theme_font, settings.font_size
    )

    hardware_thread = QThread()
    hardware_thread.setObjectName("hardware-collector")
    collector = HardwareThreadBridge().attach(
        hardware_thread, settings.hardware_interval_ms
    )

    from pulse_hwm.collectors.processes import ProcessesThreadBridge
    from pulse_hwm.collectors.websites import WebsiteThreadBridge
    from pulse_hwm.processes import set_low_priority_mode, trim_working_set

    websites_thread = QThread()
    websites_thread.setObjectName("websites-monitor")
    monitor = WebsiteThreadBridge().attach(
        websites_thread,
        db,
        interval_s=settings.website_interval_s,
        timeout_s=settings.website_timeout_s,
        ssl_warn_days=settings.ssl_warn_days,
    )

    # own worker thread for the (heavier) full process scan; the collector
    # pauses itself whenever the Processes tab is hidden
    processes_thread = QThread()
    processes_thread.setObjectName("processes-scan")
    processes_collector = ProcessesThreadBridge.attach(
        processes_thread,
        interval_s=settings.process_interval_s,
        max_rows=settings.process_max_rows,
    )

    def excepthook(etype, value, tb) -> None:
        traceback.print_exception(etype, value, tb)
        try:
            log = config.data_dir() / "error.log"
            with log.open("a", encoding="utf-8") as fh:
                import time

                fh.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
                traceback.print_exception(etype, value, tb, file=fh)
        except Exception:
            pass

    sys.excepthook = excepthook

    from pulse_hwm.alerts.notifier import AlertChannels, AlertManager

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

    # —— accounts / cloud sync wiring ——————————————————————————————————
    from pulse_hwm.cloud.config import auth_config
    from pulse_hwm.cloud.oauth import OauthCoordinator, parse_callback_url
    from pulse_hwm.cloud.rest import CloudClient
    from pulse_hwm.cloud.session import SessionManager

    auth_cfg = auth_config()
    supa = CloudClient(auth_cfg.base_url, auth_cfg.publishable_key)
    session = SessionManager(supa)
    coordinator = OauthCoordinator(supa)

    # ── updates (v1.2.0): registered+active users get in-app updates ────
    # metadata: /updates/latest (Bearer); binary: /dl streamed from the
    # private R2 bucket. Everything passes Ed25519 + sha256 before spawn.
    from pulse_hwm import __version__ as CURRENT_VERSION
    from pulse_hwm.cloud.updates.checker import UpdateChecker
    from pulse_hwm.cloud.updates.installer import UpdateInstaller
    from pulse_hwm.cloud.updates.policy import is_newer

    checker = UpdateChecker(session, supa, db, CURRENT_VERSION, auth_cfg.base_url)
    updates_dir = config.data_dir() / "updates"
    updates_dir.mkdir(parents=True, exist_ok=True)
    installer = UpdateInstaller(session, auth_cfg.base_url, updates_dir)

    # ── RGB engine wiring (phases 1-8): catalog + worker thread + manager.
    # One driver, one render loop. Mode resolution runs on the UI thread
    # (cheap pure planner); all hardware I/O stays on the rgb thread.
    from PySide6.QtCore import QObject, QThread
    from PySide6.QtCore import Signal as _Signal

    from pulse_hwm.rgb.drivers.asus_aura import ASUSAuraDriver
    from pulse_hwm.rgb.drivers.aula_f75 import AulaDriver
    from pulse_hwm.rgb.drivers.corsair_icue import CorsairDriver
    from pulse_hwm.rgb.drivers.logitech_g import LogitechDriver
    from pulse_hwm.rgb.drivers.msi_mystic import MysticLightDriver
    from pulse_hwm.rgb.drivers.raw.corsair_raw import CorsairRawDriver
    from pulse_hwm.rgb.drivers.raw.logitech_raw import LogitechRawDriver
    from pulse_hwm.rgb.drivers.raw.msi_raw import MysticLightRawDriver
    from pulse_hwm.rgb.drivers.raw.razer_raw import RazerRawDriver
    from pulse_hwm.rgb.drivers.razer_chroma import RazerDriver
    from pulse_hwm.rgb.drivers.registry import DriverRegistry
    from pulse_hwm.rgb.effects.catalog import EffectCatalog
    from pulse_hwm.rgb.manager import RgbManager
    from pulse_hwm.rgb.worker import RgbThreadBridge, RgbWorker

    class _RgbAssignments(QObject):
        """Carrier signal: dict pushed onto the worker thread queued."""

        pushed = _Signal(dict)

    def _rgb_set_driver_connected() -> bool:
        """Hand the first available driver to the worker thread via queued
        signal; device enumeration + open happen there, and the manager
        learns device ids from the queued devices_changed callback."""
        available = rgb_registry.available()
        if not available:
            rgb_manager.clear_driver()
            return False
        rgb_worker.attach_requested.emit(available[0])  # I/O on rgb thread
        return True

    rgb_catalog = EffectCatalog()
    from pulse_hwm.rgb.effects.loader import UserEffectStore

    _rgb_user_store = UserEffectStore(db)
    _rgb_registered = _rgb_user_store.register_with_catalog(rgb_catalog)
    rgb_registry = DriverRegistry(
        # Order: Aula (hardware-verified) → raw vendor-free drivers (no
        # vendor software needed) → vendor-SDK drivers (ship-bloat probes).
        # Probe availability sorts runtime picks automatically.
        (
            AulaDriver,
            RazerRawDriver,
            LogitechRawDriver,
            CorsairRawDriver,
            MysticLightRawDriver,
            LogitechDriver,
            RazerDriver,
            CorsairDriver,
            MysticLightDriver,
            ASUSAuraDriver,
        )
    )
    rgb_registry.load()

    rgb_thread = QThread()
    rgb_thread.setObjectName("rgb-engine")
    rgb_worker = RgbWorker(rgb_catalog)
    RgbThreadBridge.attach(rgb_worker, rgb_thread)

    rgb_values = app_settings.load(db)
    # vendor-free phase: hand the experimental gate to raw drivers. Device
    # enumeration never needs it; byte-level first-pass writes fail closed
    # without it (settings-level switch, checked live by the drivers).
    _RAW_DRIVER_IDS = (
        "razer_raw",
        "logitech_raw",
        "corsair_raw",
        "msi_raw",
    )
    for _raw_id in _RAW_DRIVER_IDS:
        _raw_driver = rgb_registry.get(_raw_id)
        if _raw_driver is not None:
            _raw_driver.experimental_allowed = bool(rgb_values.rgb_allow_raw_protocols)

    rgb_worker.set_brightness(rgb_values.rgb_brightness)
    rgb_worker.set_fps(rgb_values.rgb_engine_fps)

    rgb_manager = RgbManager(db, rgb_catalog)
    rgb_carrier = _RgbAssignments()
    rgb_manager.apply_assignments = rgb_carrier.pushed.emit
    rgb_carrier.pushed.connect(rgb_worker.apply_assignments)

    def _rgb_on_devices(devices: list) -> None:
        rgb_manager.set_driver(
            devices[0].driver_id if devices else "",
            [d.device_id for d in devices],
        )
        rgb_manager.reconsider()

    rgb_worker.devices_changed.connect(_rgb_on_devices)

    def _rgb_push_sensors(snapshot: dict) -> None:
        # hardware thread → UI thread → queued worker push. Sensor keys are
        # the reactive effect contract: cpu_temp / gpu_temp / mem_pct /
        # max_temp (None when the source is unavailable).
        cpu_temps = [
            row["temp"]
            for row in (snapshot.get("temps") or [])
            if "CPU" in str(row.get("label", "")).upper()
            and row.get("temp") is not None
        ]
        gpu_temps = [
            row["temp"]
            for row in (snapshot.get("temps") or [])
            if "GPU" in str(row.get("label", "")).upper()
            and row.get("temp") is not None
        ]
        cpu_temp = max(cpu_temps) if cpu_temps else None
        gpu_temp = max(gpu_temps) if gpu_temps else None
        rgb_worker.sensors_requested.emit(
            {
                "cpu_temp": cpu_temp,
                "gpu_temp": gpu_temp,
                "mem_pct": (snapshot.get("mem") or {}).get("pct"),
                "max_temp": max(
                    [t for t in (cpu_temp, gpu_temp) if t is not None],
                    default=None,
                ),
            }
        )

    collector.updated.connect(_rgb_push_sensors)

    def _rgb_on_brightness(pct: int) -> None:
        # UI-thread hook from the RGB tab brightness spinner → queued worker
        rgb_worker.brightness_requested.emit(int(pct))

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
        rgb_manager=rgb_manager,
        rgb_worker=rgb_worker,
    )
    window.show()

    # —— incoming auth callbacks (pulsehwm:// handoff from a relaunched process)
    from pulse_hwm.single_instance import SingleInstance

    single = SingleInstance()

    def handle_incoming_url(url: str) -> None:
        """Adopt an authentication callback forwarded by another instance."""
        # the user is still sitting in the browser — surface the window
        window.bring_to_front()
        callback = parse_callback_url(url)
        if not callback.ok:
            # dead flow: drop the parked verifier so no later callback can
            # restore it, and release the buttons for a clean retry
            coordinator.reject_pending()
            window.show_account_feedback(f"login link problem: {callback.error}")
            return
        # cold launch: this process never issued the sign-in, but the
        # verifier is parked in Credential Manager — rebuild the flow
        coordinator.restore_pending()
        pending = coordinator.pending
        if pending is None:
            window.show_account_feedback("no sign-in in progress — link expired")
            return
        result = session.adopt_pkce_result(
            callback.code, pending.flow_id, pending.provider
        )
        coordinator.clear_pending()  # verifier consumed (single-use)
        window.on_oauth_result(result.ok, result.error)
        if result.ok:
            session_started()

    single.url_received.connect(handle_incoming_url)

    # cold launch can itself carry the callback (the link was clicked
    # while the app was closed, so the exe relaunched with the URL in
    # argv) — the pipe only covers warm handoffs between live instances
    from pulse_hwm.single_instance import auth_urls_from_args

    for url in auth_urls_from_args(sys.argv[1:]):
        QTimer.singleShot(100, lambda u=url: handle_incoming_url(u))

    # silent session restore from Credential Manager on boot
    def resume_session() -> None:
        resumed = session.try_resume()
        if resumed:
            window.on_oauth_result(True, "")

    QTimer.singleShot(20, resume_session)

    def session_started() -> None:
        # sign-in (and OAuth adoption) both kick a sync immediately, plus
        # an immediate update check — the daily-use contract for members
        sync_engine.sync_now()
        scheduler.trigger("update-check")

    # —— sync engine (automatic ~60 s compare-and-merge; payloads tiny) —
    from PySide6.QtCore import QThreadPool

    from pulse_hwm.cloud.sync_engine import SyncEngine

    sync_engine = SyncEngine(session, supa, db, parent=None)
    sync_engine.attach_pool(QThreadPool.globalInstance())
    _was_signed_in = {"v": session.is_signed_in()}

    def _sync_feedback(summary: str, ok: bool, error: str) -> None:
        window.show_account_feedback(summary or error)
        # only an actual SIGN-OUT (signed in → signed out because the
        # parked token was rejected) should repaint; showing "session
        # expired" on a never-signed-in install every 60 s would be noise
        signed_in = session.is_signed_in()
        if _was_signed_in["v"] and not signed_in:
            window.on_oauth_result(False, "session expired — sign in again")
        _was_signed_in["v"] = signed_in

    sync_engine.finished.connect(_sync_feedback)
    if window._account_tab is not None:
        window._account_tab.sync_requested.connect(sync_engine.sync_now)

    def on_sync_done(summary: str, ok: bool, _error: str) -> None:
        # "in sync" = nothing moved; reloading the form on that would
        # clobber a half-edited Settings form every 60 s
        if not ok or not summary or summary in ("not signed in", "in sync"):
            return
        # cloud may have updated syncable settings (theme etc.) — reapply
        merged = app_settings.load(db)
        theme_manager.apply(merged.theme_color, merged.theme_font, persist=False)
        theme_manager.set_body_px(merged.font_size)
        # push merged values into the Settings form so the next SAVE can't
        # clobber cloud-newer rows with stale spinbox values, and update
        # the website monitor cadence/thresholds live
        if window._settings_tab is not None:
            window._settings_tab.load_from(merged)
        monitor.reconfigure(
            interval_s=merged.website_interval_s,
            timeout_s=merged.website_timeout_s,
            ssl_warn_days=merged.ssl_warn_days,
        )
        # pulled sites (adds AND tombstones) must repaint the WEBSITES list
        if window._sites_tab is not None:
            window._sites_tab.reload_sites()

    sync_engine.finished.connect(on_sync_done)

    _rgb_attached = _rgb_set_driver_connected()
    rgb_thread.start()

    def shutdown() -> None:
        rgb_thread.quit()
        websites_thread.quit()
        hardware_thread.quit()
        processes_thread.quit()
        rgb_thread.wait(2500)
        websites_thread.wait(3000)
        hardware_thread.wait(2500)
        processes_thread.wait(2500)
        single.close()
        supa.close()
        db.close()

    app.aboutToQuit.connect(shutdown)

    # being a good citizen: apply boot-time resource mode from settings
    set_low_priority_mode(settings.limit_resources)

    def on_check_done(outcome) -> None:
        """UI-thread handler for update checks (UpdateChecker.checked)."""
        release = dict(outcome.release or {})
        version = str(release.get("version", ""))
        manual = bool(getattr(outcome, "manual", False))
        if outcome.state in ("available", "forced") and version:
            seen = db.get_setting("update_highest_seen", "") or ""
            if is_newer(version, seen):
                db.set_setting("update_highest_seen", version)
            if version != db.get_setting("update_notified_version", ""):
                db.set_setting("update_notified_version", version)
                alerts.notify(
                    "info",
                    "UPDATE AVAILABLE",
                    f"Pulse v{version} is ready — you are on v{CURRENT_VERSION}.",
                    play_sound=False,  # info-level: toast only, never a beep
                )
                import time as _time

                db.insert_event(
                    _time.time(), "INFO", "update", f"update available: v{version}"
                )
            if window._account_tab is None:
                return  # the ACCOUNT tab is absent — banner updates lost, but the toast above still leaks the news
            window._account_tab.show_update_available(
                release,
                current_version=CURRENT_VERSION,
                forced=outcome.state == "forced",
            )
            offer.update({"release": release, "version": version})
        elif outcome.state == "skipped":
            if release and is_newer(
                version, db.get_setting("update_highest_seen", "") or ""
            ):
                db.set_setting("update_highest_seen", version)  # seen-and-understood
            # silent when periodic; manual checks explain themselves
            if manual and outcome.reason and outcome.reason != "not signed in":
                window.show_account_feedback(f"updates: {outcome.reason}")
            if window._account_tab is not None:
                window._account_tab.clear_update_banner()
        elif outcome.state == "error":
            # never toast on background check errors; log for diagnostics
            if manual:
                window.show_account_feedback(f"update check failed: {outcome.reason}")
            import time as _time

            db.insert_event(
                _time.time(),
                "ERROR",
                "update",
                f"update check failed: {outcome.reason}",
            )

    checker.checked.connect(on_check_done)

    offer = {"release": None, "version": ""}

    def on_install_requested() -> None:
        release = offer.get("release")
        if not release:
            return
        installer.install(release)

    def on_install_progress(done: int, total: int) -> None:
        window._account_tab.show_update_progress(int(done), int(total))

    def on_install_finished(outcome) -> None:
        installer.clear()
        if outcome.ok:
            import time as _time

            db.insert_event(
                _time.time(), "INFO", "update", f"update installing: v{outcome.version}"
            )
            window._account_tab.show_update_done(outcome.version)
            # the silent installer needs OUR process gone to replace files;
            # it relaunches Pulse itself (installer [Run] /LAUNCHAFTER check)
            from PySide6.QtCore import QTimer as _QTimer

            _QTimer.singleShot(1500, window.quit_for_update)
        else:
            import time as _time

            db.insert_event(
                _time.time(), "ERROR", "update", f"update failed: {outcome.error}"
            )
            window._account_tab.show_update_error(str(outcome.error))

    if window._account_tab is not None:
        window._account_tab.install_update_requested.connect(on_install_requested)
        window._account_tab.update_dismissed.connect(
            lambda version: (
                db.set_setting("update_dismissed_version", str(version)),
                window._account_tab.clear_update_banner(),
            )
        )
        installer.progress.connect(on_install_progress)
        installer.finished.connect(on_install_finished)
        window.update_check_requested.connect(lambda: checker.check_now(manual=True))

    # ── one scheduler for every background job ──────────────────────────
    def trim_memory() -> None:
        # re-read on every fire so flipping the toggle in Settings applies
        # without a restart
        if app_settings.load(db).limit_resources:
            trim_working_set()

    def prune_now() -> dict:
        kept = app_settings.load(db)
        return db.prune(kept.retention_days)

    from pulse_hwm.scheduler import Scheduler

    scheduler = Scheduler()
    scheduler.add_job("sync", 60_000, sync_engine.sync_now, immediate=True)

    def check_updates_job() -> None:
        # a live read every fire so the Settings toggle applies mid-session
        if app_settings.load(db).update_check_enabled:
            checker.check_now()

    scheduler.add_job(
        "update-check", 6 * 3600 * 1000, check_updates_job, immediate=True
    )
    scheduler.add_job("trim", 15 * 60 * 1000, trim_memory, immediate=False)
    scheduler.add_job("prune", 24 * 3600 * 1000, prune_now, immediate=False)

    # ── reactive alerts (phase 16): error-level alerts flash the RGB
    # devices for rgb_alert_hold_ms, then expiry sweeping reverts to the
    # temperature map. Listener runs on the UI thread (fast planner pass).
    alerts.add_dispatch_listener(lambda level, title: rgb_manager.handle_alert())

    def rgb_maintain() -> None:
        rgb_manager.maintain()

    scheduler.add_job("rgb-alert", 500, rgb_maintain, immediate=False)
    scheduler.start()

    alerts.attach_tray(window.tray)
    monitor.site_state_changed.connect(alerts.handle_site_transition)
    monitor.checked.connect(window.on_site_checked)

    prune_now()

    hardware_thread.start()
    websites_thread.start()
    processes_thread.start()

    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("[pulse] system tray unavailable")
    return app.exec()


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

    # —— RGB probe (phase 7): enumerate + one red flash, best-effort ————
    try:
        from pulse_hwm.rgb.drivers.aula_f75 import AulaDriver
        from pulse_hwm.rgb.model import RgbColor

        rgb_driver = AulaDriver()
        rgb_probe = rgb_driver.probe()
        rgb_report = {
            "aula_available": rgb_probe.available,
            "aula_reason": rgb_probe.reason,
        }
        if rgb_probe.available:
            rgb_driver.open()
            rgb_report["aula_devices"] = len(rgb_driver.devices())
            rgb_report["aula_frame_set"] = rgb_driver.set_frame(
                "aula:0", [RgbColor(255, 0, 0)] * 10
            )
            rgb_driver.close()
            if not rgb_report["aula_frame_set"] and rgb_driver.last_error:
                rgb_report["aula_error"] = rgb_driver.last_error
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
