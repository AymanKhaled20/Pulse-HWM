from __future__ import annotations

import sys
import traceback


def run() -> int:
    from pulse_hwm import config

    config.ensure_dirs()

    from PySide6.QtCore import Qt, QThread
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

    hardware_thread = QThread()
    hardware_thread.setObjectName("hardware-collector")
    collector = HardwareThreadBridge().attach(hardware_thread, 1000)

    def excepthook(etype, value, tb) -> None:
        traceback.print_exception(etype, value, tb)

    sys.excepthook = excepthook

    window = MainWindow(hardware_collector=collector)
    window.show()

    hardware_thread.start()

    def shutdown() -> None:
        hardware_thread.quit()
        hardware_thread.wait(2500)
        db.close()

    app.aboutToQuit.connect(shutdown)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("[pulse] system tray unavailable")
    return app.exec()
