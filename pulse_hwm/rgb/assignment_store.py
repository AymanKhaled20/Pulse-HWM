"""Device-assignment storage — pure CRUD on the rgb_device_assignment blob.

The blob lives in the standard settings table as JSON:
    {"<driver_id>/<device_id>": {"effect": id, "params": {...}, "enabled": bool}}

Kept Qt-free and separate from the UI so add/remove/toggle are unit-tested;
the RGB tab (and later, sync/email exports) call these helpers. Corrupt
blobs are treated as empty — the settings loader already guards on load,
but a mid-session rewrite must not trust that alone.
"""

from __future__ import annotations

import json

from pulse_hwm.rgb.engine import DeviceAssignment


def _blob(data) -> dict:
    if isinstance(data, dict):
        return {
            str(key): value for key, value in data.items() if isinstance(value, dict)
        }
    return {}


def parse_blob(blob: str) -> dict:
    try:
        return _blob(json.loads(blob or "{}"))
    except ValueError:
        return {}


def _blob_text(entries: dict) -> str:
    import json

    return json.dumps(entries, indent=None)


def assign(
    blob: str, driver_id: str, device_id: str, effect_id: str, enabled: bool
) -> str:
    """One device's assignment upsert. Returns the NEW blob text (immutable
    flow: callers save the returned string via save_field)."""
    entries = parse_blob(blob)
    entries[f"{driver_id}/{device_id}"] = {
        "effect": effect_id,
        "params": entries.get(f"{driver_id}/{device_id}", {}).get("params", {}),
        "enabled": bool(enabled),
    }
    return _blob_text(entries)


def clear(blob: str, driver_id: str, device_id: str) -> str:
    entries = parse_blob(blob)
    entries.pop(f"{driver_id}/{device_id}", None)
    return _blob_text(entries)


def entry_of(blob: str, driver_id: str, device_id: str) -> dict | None:
    return parse_blob(blob).get(f"{driver_id}/{device_id}")


def assignments_from_blob(blob: str, catalog, driver_id: str) -> dict:
    """Validation pass across the WHOLE blob → {device_id: DeviceAssignment}
    for one driver. Drops unknown effects + unparseable entries; used by
    tests and (later) a bulk editor. The live path uses the planner's
    loader in manager.py. `catalog=None` skips effect-id validation."""
    from pulse_hwm.rgb.manager import parse_assignment_blob

    result: dict = {}
    if catalog is None:
        for key, value in parse_blob(blob).items():
            key_driver, _, device = key.partition("/")
            if key_driver == driver_id:
                result[device] = DeviceAssignment(
                    str(value.get("effect", "")),
                    params=_safe_params(value),
                    enabled=bool(value.get("enabled", True)),
                )
        return result
    result = {}
    for key, value in parse_blob(blob).items():
        key_driver, _, device = key.partition("/")
        if key_driver == driver_id:
            assignment = parse_assignment_blob(blob, catalog, driver_id, device)
            if assignment is not None:
                result[device] = assignment
    return result


def _safe_params(entry: dict) -> dict:
    params = entry.get("params")
    return params if isinstance(params, dict) else {}
