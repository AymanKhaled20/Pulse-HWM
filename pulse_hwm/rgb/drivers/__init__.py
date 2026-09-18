"""Pulse RGB drivers — transports to real hardware.

base.py     RgbDriver ABC + ProbeResult (the frozen driver contract)
registry.py discovery/lookup (built-in drivers + optional user drop-ins)

Drivers are the ONLY layer that touches vendor SDKs, DLLs, or raw HID.
They receive validated frames from the engine and must never raise from
probe(); open()/set_frame() may raise, and callers (engine/worker) are the
ones that catch and degrade.
"""
