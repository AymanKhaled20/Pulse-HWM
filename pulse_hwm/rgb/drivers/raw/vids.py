"""Tiny vendor-VID table + enumerate helper shared by the raw drivers.

Not an abstraction layer — each driver keeps its own PID knowledge. This
file exists only so scripts/rgb_raw_scan.py and the probes agree on which
ecosystem a VID belongs to, and so we never hardcode a VID twice.
"""

from __future__ import annotations

VENDOR_VIDS: dict[int, str] = {
    0x1532: "razer",
    0x046D: "logitech",
    0x1B1C: "corsair",
    0x1462: "msi",
    0x258A: "sinowealth (aula)",
}


def load_hid_or_none():
    try:
        import hid

        return hid
    except Exception:
        return None


def scan_all(hid_module=None) -> list[dict]:
    """Every HID interface whose vendor VID is in VENDOR_VIDS. Returns the
    raw hidapi dicts plus a 'vendor' tag (info only — no side effects)."""
    if hid_module is None:
        hid_module = load_hid_or_none()
    if hid_module is None:
        return []
    found: list[dict] = []
    try:
        all_entries = hid_module.enumerate()
    except Exception:
        return []
    for entry in all_entries:
        vid = int(entry.get("vendor_id") or 0) & 0xFFFF
        if vid in VENDOR_VIDS:
            row = dict(entry)
            row["vendor"] = VENDOR_VIDS[vid]
            found.append(row)
    return found
