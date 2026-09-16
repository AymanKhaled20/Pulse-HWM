"""Pulse RGB — hardware lighting control.

Phase 1 of the RGB engine: pure data model. This package is intentionally
Qt-free so the model, effects, and driver contracts can be unit-tested
without a QApplication. Qt wiring (engine thread, UI tab) lands in later
phases and must live behind this package, never inside it.
"""
