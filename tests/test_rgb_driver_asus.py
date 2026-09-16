"""ASUS Aura probe-stub tests (all offline)."""

from __future__ import annotations

from pulse_hwm.rgb.drivers.asus_aura import ASUSAuraDriver, AuraNotImplementedError


def test_missing_dll_means_probe_false(tmp_path):
    driver = ASUSAuraDriver(dll_path=tmp_path / "absent.dll")
    probe = driver.probe()
    assert probe.available is False
    assert "Armoury Crate" in probe.needs_install


def test_dll_present_still_not_active(tmp_path):
    # probe NEVER claims control for a stub-driver: manual safety net
    present = tmp_path / "present.dll"
    present.write_bytes(b"x")
    driver = ASUSAuraDriver(dll_path=present)
    probe = driver.probe()
    assert probe.available is False
    assert "not active" in probe.reason


def test_stub_vendor_id_and_labels():
    assert ASUSAuraDriver.driver_id == "asus_aura"
    assert ASUSAuraDriver.name == "ASUS Aura"


def test_stub_exports_error_class():
    # contract freeze placeholder: open() will raise AuraNotImplementedError
    assert issubclass(AuraNotImplementedError, RuntimeError)
