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
    from pulse_hwm.ui.theme import app_icon, load_fonts, load_theme

    QGuiApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)   # close = minimize to tray

    load_fonts()
    load_theme(app)

    db = open_default()
    db.seed_default_sites()

    from pulse_hwm import app_settings
    settings = app_settings.load(db)

    hardware_thread = QThread()
    hardware_thread.setObjectName("hardware-collector")
    collector = HardwareThreadBridge().attach(hardware_thread, settings.hardware_interval_ms)

    from pulse_hwm.collectors.websites import WebsiteThreadBridge
    websites_thread = QThread()
    websites_thread.setObjectName("websites-monitor")
    monitor = WebsiteThreadBridge().attach(
        websites_thread, db,
        interval_s=settings.website_interval_s,
        timeout_s=settings.website_timeout_s,
        ssl_warn_days=settings.ssl_warn_days,
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

    from pulse_hwm.alerts.notifier import AlertManager, AlertChannels
    alerts = AlertManager(db, AlertChannels(
        sound=config.env().alert_sound_enabled,
        desktop=True,
        webhooks=True,
    ))

    window = MainWindow(hardware_collector=collector, websites_monitor=monitor,
                        db=db, alerts=alerts)
    window.show()

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

    def shutdown() -> None:
        websites_thread.quit()
        hardware_thread.quit()
        websites_thread.wait(3000)
        hardware_thread.wait(2500)
        db.close()

    app.aboutToQuit.connect(shutdown)

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

    from pulse_hwm.collectors.lhm import is_available, LibreSensors

    report = {"admin": admin, "lhm_runtime": is_available(), "sensors": [], **probes}
    if is_available():
        sensors = LibreSensors()
        rows = sensors.read()
        if rows:
            report["sensors"] = [{"label": r["label"], "temp": round(r["temp"], 1)} for r in rows]
        else:
            report["lhm_error"] = sensors.last_error or "no temperature sensors returned"
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
                    "temps": [s.Name for s in hw.Sensors if s.SensorType.ToString() == "Temperature"][:6],
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
