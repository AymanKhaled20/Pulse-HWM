from __future__ import annotations

import sys
import traceback


def run() -> int:
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

    alerts = AlertManager(
        db,
        AlertChannels(
            sound=config.env().alert_sound_enabled,
            desktop=True,
            webhooks=True,
        ),
    )

    # ── accounts / cloud sync wiring ──────────────────────────────────
    from pulse_hwm.auth.config import auth_config
    from pulse_hwm.auth.oauth import OauthCoordinator, parse_callback_url
    from pulse_hwm.auth.rest import SupabaseClient
    from pulse_hwm.auth.session import SessionManager

    auth_cfg = auth_config()
    supa = SupabaseClient(auth_cfg.base_url, auth_cfg.publishable_key)
    session = SessionManager(supa)
    coordinator = OauthCoordinator(supa)
    _SYNC_TICK_MS = 60 * 1000  # automatic compare-and-merge cadence

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
    )
    window.show()

    # ── incoming auth callbacks (pulsehwm:// handoff from a relaunched process)
    from pulse_hwm.single_instance import SingleInstance

    single = SingleInstance()

    def handle_incoming_url(url: str) -> None:
        callback = parse_callback_url(url)
        if not callback.ok:
            window.show_account_feedback(f"login link problem: {callback.error}")
            return
        pending = coordinator.pending
        if pending is None:
            window.show_account_feedback("no sign-in in progress — link expired")
            return
        result = session.adopt_pkce_result(
            callback.code, pending.flow_id, pending.provider
        )
        coordinator.pending = None  # verifier already consumed (single-use)
        window.on_oauth_result(result.ok, result.error)
        if result.ok:
            session_started()

    single.url_received.connect(handle_incoming_url)

    # silent session restore from Credential Manager on boot
    def resume_session() -> None:
        resumed = session.try_resume()
        if resumed:
            window.on_oauth_result(True, "")

    QTimer.singleShot(20, resume_session)

    def session_started() -> None:
        # sign-in (and OAuth adoption) both kick a sync immediately
        sync_engine.sync_now()

    # ── sync engine (automatic ~60 s compare-and-merge; payloads tiny) ─
    from PySide6.QtCore import QThreadPool

    from pulse_hwm.auth.sync_engine import SyncEngine

    sync_engine = SyncEngine(session, supa, db, parent=None)
    sync_engine.attach_pool(QThreadPool.globalInstance())
    sync_engine.finished.connect(
        lambda summary, ok, error: window.show_account_feedback(summary or error)
    )
    if window._account_tab is not None:
        window._account_tab.sync_requested.connect(sync_engine.sync_now)
    sync_timer = QTimer()
    sync_timer.timeout.connect(sync_engine.sync_now)
    sync_timer.start(_SYNC_TICK_MS)

    def on_sync_done(summary: str, ok: bool, _error: str) -> None:
        if not ok or not summary or summary == "not signed in":
            return
        # cloud may have updated syncable settings (theme etc.) — reapply
        merged = app_settings.load(db)
        theme_manager.apply(merged.theme_color, merged.theme_font, persist=False)
        theme_manager.set_body_px(merged.font_size)

    sync_engine.finished.connect(on_sync_done)

    def shutdown() -> None:
        websites_thread.quit()
        hardware_thread.quit()
        processes_thread.quit()
        websites_thread.wait(3000)
        hardware_thread.wait(2500)
        processes_thread.wait(2500)
        single.close()
        supa.close()
        db.close()

    app.aboutToQuit.connect(shutdown)

    # being a good citizen: apply boot-time resource mode from settings
    set_low_priority_mode(settings.limit_resources)

    def trim_memory() -> None:
        # re-read on every fire so flipping the toggle in Settings applies
        # without a restart
        if app_settings.load(db).limit_resources:
            trim_working_set()

    trim_timer = QTimer()
    trim_timer.timeout.connect(trim_memory)
    trim_timer.start(15 * 60 * 1000)

    alerts.attach_tray(window.tray)
    monitor.site_state_changed.connect(alerts.handle_site_transition)
    monitor.checked.connect(window.on_site_checked)

    def prune_now() -> dict:
        kept = app_settings.load(db)
        return db.prune(kept.retention_days)

    prune_timer = QTimer()
    prune_timer.timeout.connect(prune_now)
    prune_timer.start(24 * 3600 * 1000)
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

    # ── sound probe ──────────────────────────────────────
    try:
        from pulse_hwm.alerts import notifier as _notifier

        probes["sound_played"] = _notifier.play_alert_sound()
        if not probes["sound_played"] and _notifier.last_sound_error:
            probes["sound_played"] = f"FAILED: {_notifier.last_sound_error}"
    except Exception:
        probes["sound_played"] = f"EXC: {traceback.format_exc(limit=2)}"

    # ── add-site probe against the real DB ───────────────
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
