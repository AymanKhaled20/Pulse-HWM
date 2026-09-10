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
    """Headless check: report whether temp sources resolve. No GUI."""
    import ctypes
    import json

    admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    from pulse_hwm.collectors.lhm import is_available, LibreSensors

    report = {"admin": admin, "lhm_runtime": is_available(), "sensors": []}
    if is_available():
        sensors = LibreSensors()
        rows = sensors.read()
        if rows:
            report["sensors"] = [{"label": r["label"], "temp": round(r["temp"], 1)} for r in rows]
        else:
            report["lhm_error"] = sensors.last_error or "no temperature sensors returned"
    rows = report["sensors"]
    cpu_temps = [r for r in rows if "CPU" in r["label"]]
    gpu_temps = [r for r in rows if "GPU" in r["label"]]
    print(json.dumps({
        **report,
        "cpu_temps": len(cpu_temps),
        "gpu_temps": len(gpu_temps),
    }, indent=2))
    return 0 if rows else 2
