from __future__ import annotations

from PySide6.QtCore import QObject, Slot


class SiteAlertRelay(QObject):
    """Moves website up/down transitions onto the UI thread.

    WebsiteMonitor emits site_state_changed from the websites thread.
    AlertManager is a plain object, so connecting it directly ran its
    handler on THAT thread — and the handler shows tray balloons and
    flashes RGB, which must only happen on the UI thread. Connecting to
    this QObject's slot instead makes Qt queue the call onto the UI thread.
    """

    def __init__(self, alerts, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._alerts = alerts

    @Slot(dict)
    def on_site_state_changed(self, result: dict) -> None:
        self._alerts.handle_site_transition(result)
