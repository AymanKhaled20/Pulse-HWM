#!/usr/bin/env python
"""HID inspector for the RGB custom drivers (development tool).

Subcommands:
  desc V[:PID] [PAGE]   dump report descriptors for every matching interface
  list                  list all vendor-HID interfaces (same data, compact)

Read-only against hardware: opens each collection, asks Windows for the
report descriptor, prints it. Nothing is written to devices.

Usage: .venv\\Scripts\\python.exe scripts\\rgb_hid_tool.py desc 0x1532:0x0537 0xFFA0
"""

from __future__ import annotations

import sys


def _load_hid():
    import hid

    return hid


def parse_target(text: str) -> tuple[int, int]:
    text = text.lstrip("$")
    text = text[2:] if text.lower().startswith("0x") and ":" in text else text
    if ":" in text:
        vid_text, pid_text = text.split(":", 1)
        return int(vid_text, 16), int(pid_text, 16)
    raise SystemExit("use VID[:PID] in hex, e.g. 0x1532:0x0537")


def list_interfaces(args: list[str]) -> int:
    hid = _load_hid()
    vid, pid = parse_target(args[0]) if args else (0, 0)
    entries = hid.enumerate(vid, pid)
    for entry in entries:
        print(
            "vid=0x%04X pid=0x%04X page=0x%04X usage=0x%04X if=%s : %s"
            % (
                entry.get("vendor_id", 0),
                entry.get("product_id", 0),
                entry.get("usage_page", 0),
                entry.get("usage", 0),
                entry.get("interface_number"),
                str(entry.get("product") or "-")[:32],
            )
        )
    return 0


def dump_descriptors(args: list[str]) -> int:
    hid = _load_hid()
    vid, pid = parse_target(args[0])
    page = int(args[1], 16) if len(args) > 1 else None
    for entry in hid.enumerate(vid, pid):
        if page is not None and (entry.get("usage_page") or 0) != page:
            continue
        device = hid.device()
        try:
            device.open_path(entry["path"])
            descriptor = device.get_report_descriptor()
            tag = "page 0x%04X usage 0x%04X if=%s" % (
                entry.get("usage_page", 0),
                entry.get("usage", 0),
                entry.get("interface_number"),
            )
            length = len(descriptor) if descriptor else 0
            print("=== %04X:%04X %s descriptor_len=%d" % (vid, pid, tag, length))
            if descriptor:
                for start in range(0, length, 28):
                    row = descriptor[start : start + 28]
                    print("   " + " ".join("%02X" % b for b in row))
        except Exception as exc:
            print("ERR %04X:%04X %s" % (vid, pid, exc))
        finally:
            try:
                device.close()
            except Exception:
                pass
    return 0


def main() -> int:
    argv = list(sys.argv[1:])
    if not argv:
        print(__doc__)
        return 1
    command, args = argv[0], argv[1:]
    if command == "desc":
        return dump_descriptors(args)
    if command == "list":
        return list_interfaces(args)
    print(f"unknown subcommand {command!r}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
