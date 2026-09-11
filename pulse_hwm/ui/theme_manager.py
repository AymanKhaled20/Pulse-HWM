from __future__ import annotations

from PySide6.QtWidgets import QApplication

from pulse_hwm import app_settings
from pulse_hwm.ui import theme
from pulse_hwm.ui.palettes import (
    color_theme,
    font_theme,
)

try:
    import pyqtgraph as pg
except ImportError:  # pragma: no cover — pyqtgraph is a runtime dep
    pg = None


class ThemeManager:
    """Owns the active color + font pair.

    apply() switches EVERYTHING live:
      1. rebind theme.py palette globals (custom painters pick them up);
      2. re-render the QSS template and hand it to the application;
      3. nudge widgets that cached pens/backgrounds (pyqtgraph charts);
      4. ask every widget to repaint;
      5. call listeners (MainWindow refreshes its tray icon).

    The choice is persisted to the settings DB, so the next boot replays it.
    """

    def __init__(self, app: QApplication, db=None):
        self._app = app
        self._db = db
        self.color_id = "amber"
        self.font_id = "classic"
        self.body_px = theme.BASE_BODY_PX
        self._listeners: list = []

    def add_listener(self, callback) -> None:
        """callback() runs after every applied theme (e.g. refresh tray icon)."""
        if callback not in self._listeners:
            self._listeners.append(callback)

    def bootstrap(self, color_id: str, font_id: str, body_px: int) -> None:
        """Boot-time application of the persisted theme (no saving)."""
        self.body_px = body_px
        self.apply(color_id, font_id, persist=False)

    def set_body_px(self, px: int) -> None:
        """User picked a new UI size in THEMES: scale, re-render, persist."""
        self.body_px = int(px)
        self.apply(self.color_id, self.font_id)

    def apply(self, color_id: str, font_id: str, persist: bool = True) -> None:
        color = color_theme(color_id)
        fonts = font_theme(font_id)
        self.color_id = color.id
        self.font_id = fonts.id
        theme.set_body_px(self.body_px)
        self._apply_now(color, fonts)
        if persist and self._db is not None:
            app_settings.save_field(self._db, "theme_color", self.color_id)
            app_settings.save_field(self._db, "theme_font", self.font_id)
            app_settings.save_field(self._db, "font_size", self.body_px)

    # ── internals ─────────────────────────────────────────────────────────
    def _apply_now(self, color, fonts) -> None:
        theme.set_active_theme(color, fonts)
        if pg is not None:
            # pyqtgraph keeps its own global background/foreground config;
            # existing plots additionally need setBackground (apply_palette).
            pg.setConfigOptions(
                antialias=False, background=color.bg, foreground=color.line
            )
        self._app.setStyleSheet(theme.render_qss(color, fonts))
        # Custom painters re-read theme globals at paint time: schedule a
        # repaint everywhere, and ask chart widgets to refresh cached pens.
        for widget in self._app.allWidgets():
            hook = getattr(widget, "apply_theme", None)
            if callable(hook):
                hook()
            widget.update()
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:
                # one broken listener must not break theme switching — log
                # so the failure is at least discoverable in error.log
                import logging

                logging.getLogger("pulse_hwm.theme").exception(
                    "theme listener callback failed"
                )
