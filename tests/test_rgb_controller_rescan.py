"""RgbController's automatic rescan decision (pure helper, no Qt)."""

from __future__ import annotations

from pulse_hwm.controllers.rgb_controller import missing_assigned_devices
from pulse_hwm.rgb import assignment_store


def _blob(*device_ids: str, driver_id: str = "openrgb") -> str:
    blob = "{}"
    for device_id in device_ids:
        blob = assignment_store.assign(blob, driver_id, device_id, "solid", True)
    return blob


def test_saved_device_that_did_not_show_up_is_missing() -> None:
    """The real case: the AULA (openrgb:5) had an effect but OpenRGB's
    startup detection only found openrgb:0..4."""
    blob = _blob(*(f"openrgb:{i}" for i in range(6)))
    found = [f"openrgb:{i}" for i in range(5)]
    assert missing_assigned_devices(blob, "openrgb", found) == ["openrgb:5"]


def test_nothing_missing_when_every_saved_device_was_found() -> None:
    blob = _blob("openrgb:0", "openrgb:1")
    assert missing_assigned_devices(blob, "openrgb", ["openrgb:0", "openrgb:1"]) == []


def test_other_drivers_and_empty_blobs_are_ignored() -> None:
    assert (
        missing_assigned_devices(_blob("fake:3", driver_id="fake"), "openrgb", []) == []
    )
    assert missing_assigned_devices("", "openrgb", ["openrgb:0"]) == []
