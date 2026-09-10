from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QMainWindow, QTabWidget, QSystemTrayIcon, QMenu, QApplication, QLabel,
)

from pulse_hwm import APP_NAME, __version__
from pulse_hwm.ui.theme import app_icon, status_icon


class MainWindow(QMainWindow):
    """Top-level window: 4 tabs + system-tray behavior."""

    def __init__(self, hardware_collector=None, websites_monitor=None, db=None, alerts=None):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{__version__}")
        self.setWindowIcon(app_icon())
        self.resize(1180, 720)
        self.setMinimumSize(900, 600)

        from pulse_hwm.ui.widgets.pixel_panel import StdoutPlaceholder
        self.tabs = QTabWidget()
        if hardware_collector is not None:
            from pulse_hwm.ui.dashboard_tab import DashboardTab
            self.tabs.addTab(DashboardTab(hardware_collector), "DASHBOARD")
        else:
            self.tabs.addTab(StdoutPlaceholder("DASHBOARD — hardware collector unavailable"), "DASHBOARD")
        if websites_monitor is not None:
            from pulse_hwm.ui.sites_tab import SitesTab
            self._sites_tab = SitesTab(websites_monitor.db, websites_monitor)
            self.tabs.addTab(self._sites_tab, "WEBSITES")
        else:
            self._sites_tab = None
            self.tabs.addTab(StdoutPlaceholder("WEBSITES — monitor unavailable"), "WEBSITES")
        if db is not None:
            from pulse_hwm.ui.history_tab import HistoryTab
            self.tabs.addTab(HistoryTab(db), "HISTORY")
        else:
            self.tabs.addTab(StdoutPlaceholder("HISTORY — db unavailable"), "HISTORY")
        if db is not None and alerts is not None and websites_monitor is not None:
            from pulse_hwm.ui.settings_tab import SettingsTab
            self.tabs.addTab(SettingsTab(db, websites_monitor, alerts), "SETTINGS")
        else:
            self.tabs.addTab(StdoutPlaceholder("SETTINGS — unavailable"), "SETTINGS")
        self.setCentralWidget(self.tabs)

        self._build_tray()
        self._first_close = True
        self._down_sites: set[int] = set()

    # ── overall state LED ──────────────────────────────────
    def on_site_checked(self, result: dict) -> None:
        site_id = int(result["site_id"])
        if result["ok"]:
            self._down_sites.discard(site_id)
        else:
            self._down_sites.add(site_id)
        self.tray.setIcon(status_icon("error" if self._down_sites else "ok"))

    # ── tray ───────────────────────────────────────────────
    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(status_icon("ok"), self)
        self.tray.setToolTip(APP_NAME)

        menu = QMenu()
        show_action = QAction("SHOW", self)
        show_action.triggered.connect(self._show_window)
        quit_action = QAction("QUIT", self)
        quit_action.triggered.connect(self._quit_app)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_window()

    def _show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_app(self) -> None:
        self.tray.hide()
        QApplication.quit()

    # ── close → minimize to tray ───────────────────────────
    def closeEvent(self, event) -> None:
        self.hide()
        if self._first_close and self.tray.isVisible():
            self.tray.showMessage(
                "PULSE-HWM",
                "Still watching your hardware. Monitoring continues in the tray.",
                QSystemTrayIcon.MessageIcon.Information,
                3500,
            )
            self._first_close = False
        event.ignore()
