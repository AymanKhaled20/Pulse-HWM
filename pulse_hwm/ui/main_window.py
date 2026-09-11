from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMenu,
    QSystemTrayIcon,
    QTabWidget,
)

from pulse_hwm import APP_NAME, __version__
from pulse_hwm.ui.theme import app_icon, status_icon


class MainWindow(QMainWindow):
    """Top-level window: 4 tabs + system-tray behavior."""

    def __init__(
        self,
        hardware_collector=None,
        websites_monitor=None,
        db=None,
        alerts=None,
        processes_collector=None,
        theme_manager=None,
    ):
        super().__init__()
        self._theme_manager = theme_manager
        self.setWindowTitle(f"{APP_NAME} v{__version__}")
        self.setWindowIcon(app_icon())
        self.resize(1280, 840)
        # windowed (restored-down) size can never shrink past this, so the
        # layout keeps ALL text readable with no scrollbars needed
        self.setMinimumSize(1180, 820)

        from pulse_hwm.ui.widgets.pixel_panel import StdoutPlaceholder

        self.tabs = QTabWidget()
        if hardware_collector is not None:
            from pulse_hwm.ui.dashboard_tab import DashboardTab

            self.tabs.addTab(DashboardTab(hardware_collector), "DASHBOARD")
        else:
            self.tabs.addTab(
                StdoutPlaceholder("DASHBOARD — hardware collector unavailable"),
                "DASHBOARD",
            )
        if processes_collector is not None:
            from pulse_hwm.ui.processes_tab import ProcessesTab

            # the tab toggles collector scans on visibility, so the full scan
            # only costs CPU while the user is actually looking at it
            self._processes_tab = ProcessesTab(processes_collector, db=db)
            self.tabs.addTab(self._processes_tab, "PROCESSES")
        else:
            self._processes_tab = None
            self.tabs.addTab(
                StdoutPlaceholder("PROCESSES — collector unavailable"), "PROCESSES"
            )
        if websites_monitor is not None:
            from pulse_hwm.ui.sites_tab import SitesTab

            self._sites_tab = SitesTab(websites_monitor.db, websites_monitor)
            self.tabs.addTab(self._sites_tab, "WEBSITES")
        else:
            self._sites_tab = None
            self.tabs.addTab(
                StdoutPlaceholder("WEBSITES — monitor unavailable"), "WEBSITES"
            )
        if db is not None:
            from pulse_hwm.ui.history_tab import HistoryTab

            self.tabs.addTab(HistoryTab(db), "HISTORY")
        else:
            self.tabs.addTab(StdoutPlaceholder("HISTORY — db unavailable"), "HISTORY")
        if theme_manager is not None:
            from pulse_hwm.ui.themes_tab import ThemesTab

            self._themes_tab = ThemesTab(theme_manager)
            self.tabs.addTab(self._themes_tab, "THEMES")
        else:
            self._themes_tab = None
            self.tabs.addTab(
                StdoutPlaceholder("THEMES — manager unavailable"), "THEMES"
            )
        if db is not None and alerts is not None and websites_monitor is not None:
            from pulse_hwm.ui.settings_tab import SettingsTab

            self._settings_tab = SettingsTab(
                db,
                websites_monitor,
                alerts,
                processes_collector=processes_collector,
            )
            self.tabs.addTab(self._settings_tab, "SETTINGS")
        else:
            self._settings_tab = None
            self.tabs.addTab(StdoutPlaceholder("SETTINGS — unavailable"), "SETTINGS")
        # LIMIT PULSE RESOURCES has two controls (Settings checkbox + PROCESSES
        # LOW PRIORITY button). Both persist the same key; these connections
        # keep the two controls visually in sync the moment one changes.
        if self._settings_tab is not None and self._processes_tab is not None:
            self._settings_tab.limit_resources_changed.connect(
                self._processes_tab.set_low_priority_state
            )
            self._processes_tab.priority_changed.connect(
                self._settings_tab.set_limit_resources_state
            )
        self.setCentralWidget(self.tabs)

        from pulse_hwm.ui.widgets.scanline import ScanlineOverlay

        self._scanlines = ScanlineOverlay(self)
        self._scanlines.raise_()

        self._build_tray()
        self._first_close = True
        self._down_sites: set[int] = set()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_scanlines"):
            self._scanlines.setGeometry(0, 0, self.width(), self.height())

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
        # theme switches repaint the tray icon in the new palette
        if self._theme_manager is not None:
            self._theme_manager.add_listener(self._on_theme_applied)

    def _on_theme_applied(self) -> None:
        # status_icon() re-reads the live palette globals, so re-setting it
        # is all that's needed after a theme switch
        # getattr guards init-order: the listener can fire before the tray
        # or _down_sites attribute has been set up
        tray = getattr(self, "tray", None)
        if tray is not None:
            down = getattr(self, "_down_sites", set())
            tray.setIcon(status_icon("error" if down else "ok"))

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
