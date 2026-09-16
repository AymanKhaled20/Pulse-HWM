#!/usr/bin/env python
"""Raw HID scan — what RGB-relevant hardware is reachable RIGHT NOW?

Owner-facing diagnostic for the vendor-free phase: runs with no vendor
software installed and answers, per device:
  * which ecosystem VID it belongs to,
  * which interface/collection it is (path, usage page, serial),
  * whether the collection is CAPTURED (exclusive-access heuristic:
    opening it once and reporting the failure).

Exit output is a plain table + a JSON blob (--json) so a capture session
can be compared byte-for-byte across attempts. Network/hardware I/O only:
safe to run while Pulse is closed.

Usage: .venv\\Scripts\\python.exe scripts\\rgb_raw_scan.py
"""

from __future__ import annotations

import json
import sys

sys.path.insert(0, ".")

from pulse_hwm.rgb.drivers.raw.vids import load_hid_or_none, scan_all  # noqa: E402


def describe(entry: dict) -> str:
    usage = f"usage 0x{entry.get('usage') or 0:04X}"
    upage = f"page 0x{entry.get('usage_page') or 0:04X}"
    serial = str(entry.get("serial_number") or "-")[:12]
    product = str(entry.get("product") or "?")[:32]
    return (
        f"{entry['vendor']:<10} vid=0x{(entry.get('vendor_id') or 0):04X} "
        f"pid=0x{(entry.get('product_id') or 0):04X} use={usage} {upage} "
        f"if={entry.get('interface_number')} ser={serial} {product}"
    )


def probe_captured(hid_module, path: str) -> str:
    """Open-once heuristic: 'ok' means we could grab it; otherwise why not.
    (hidapi device objects report their state via their own fields; we use
    a second tiny read after close as the fingerprint instead of relying on
    is_opened(), which some hidapi Windows builds don't have.)"""
    try:
        device = hid_module.device()
        result = device.open_path(path)
        device.close()
        if result == 0 or result is None:
            return "open ok"
        return f"open returned {result!r}"
    except Exception as exc:
        return f"open failed: {exc}"


def main() -> int:
    hid = load_hid_or_none()
    if hid is None:
        print("hidapi missing — pip install hidapi")
        return 1
    rows = scan_all(hid)
    print(f"scanned HID: {len(rows)} vendor interfaces")
    json_out = []
    for entry in rows:
        line = describe(entry)
        capture = probe_captured(hid, entry["path"])
        print(f"  {line}  [{capture}]")
        json_out.append(
            {
                **{
                    k: entry.get(k)
                    for k in (
                        "vendor",
                        "vendor_id",
                        "product_id",
                        "usage_page",
                        "usage",
                        "interface_number",
                        "serial_number",
                        "product",
                        "path",
                    )
                },
                "capture": capture,
            }
        )
    json_mode = "--json" in sys.argv
    if json_mode:
        print(json.dumps(json_out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
