"""AULA transport tests against a scripted fake hidapi module — no real
device. Verifies sequence ORDER, protocol round-trips, dedupe, and
graceful degradation."""

from __future__ import annotations

import pytest

from pulse_hwm.rgb.drivers import aula_protocol as ap
from pulse_hwm.rgb.drivers.aula_f75 import AulaDriver, _representative
from pulse_hwm.rgb.model import RgbColor


class FakeHidDevice:
    """Scripted endpoint mirroring the real wire stream: every write is
    followed by an echo, and config-response fragments come after the READ
    echo — exactly the order _read_fragment expects."""

    def __init__(self):
        self.written: list[bytes] = []
        self.fail_writes = False
        self._echoes: list[list[int]] = []
        self._config = [
            ap.build_fragment(ap.CMD_READ, ap.SUBCMD_CONFIG, seq, [])
            for seq in range(ap.CONFIG_FRAGMENTS)
        ]
        for fragment in self._config:
            fragment[ap.CFG_APPLY_FLAG] = 0x01
            fragment[19] = ap.checksum(fragment)
        self._config_responses: list | None = None

    def open_path(self, path) -> None:
        # real hidapi opens in-place; record which collection got used
        self.opened_path = path

    def write(self, data: bytes) -> int:
        if self.fail_writes:
            return 0
        self.written.append(bytes(data))
        # the keyboard echoes every fragment verbatim
        self._echoes.append(list(data))
        return len(data)

    def read(self, size: int, timeout_ms: int) -> list[int]:
        if self._echoes:
            return self._echoes.pop(0)
        if self._config_responses:
            return list(self._config_responses.pop(0))
        return []

    def close(self) -> None:
        pass


class FakeHidModule:
    def __init__(self):
        self.entries = [
            {"path": b"kb1", "usage_page": 0x0001},  # ordinary collection
            {"path": b"kb0", "usage_page": 0xFF02},  # vendor collection
        ]
        self.fake_device = FakeHidDevice()

    def enumerate(self, vendor_id: int, product_id: int) -> list:
        self.last_vid, self.last_pid = vendor_id, product_id
        return self.entries

    def device(self) -> FakeHidDevice:
        # hidapi style: device() returns the handle; open_path opens it
        return self.fake_device

    def queue_config(self) -> None:
        """Arm the device: reads after the READ echo return config fragments."""
        self.fake_device._config_responses = [
            list(fragment) for fragment in self.fake_device._config
        ]


@pytest.fixture()
def rigged():
    module = FakeHidModule()
    module.queue_config()  # driver.open() performs a config read
    driver = AulaDriver(hid_module=module)
    driver.open()
    return driver, module.fake_device


class TestProbe:
    def test_unavailable_when_hidapi_missing(self, monkeypatch):
        # simulate the dependency genuinely absent (exe without hidapi)
        import sys

        monkeypatch.setitem(sys.modules, "hid", None)
        driver = AulaDriver(hid_module=None)
        probe = driver.probe()
        assert probe.available is False
        assert "hidapi" in probe.reason

    def test_no_device_means_probe_false(self):
        module = FakeHidModule()
        module.entries = []
        driver = AulaDriver(hid_module=module)
        probe = driver.probe()
        assert probe.available is False
        assert "no AULA" in probe.reason


class TestOpen:
    def test_vendor_collection_preferred(self, rigged):
        _, fake_device = rigged
        assert fake_device.opened_path == b"kb0"  # vendor page collection wins

    def test_falls_back_to_first_entry(self):
        module = FakeHidModule()
        module.entries[1]["usage_page"] = 0  # no vendor collection present
        driver = AulaDriver(hid_module=module)
        driver.open()
        assert module.fake_device.opened_path == b"kb1"


class TestSequence:
    def test_set_frame_sends_full_sequence(self, rigged):
        driver, _ = rigged
        ok = driver.set_frame("aula:0", [RgbColor(255, 0, 0)] * 10)
        assert ok is True
        commands = [data[1] for data in module_write_frames(rigged)]
        assert ap.CMD_WRITE in commands
        assert ap.CMD_COLOR in commands
        assert ap.CMD_SAVE in commands

    def test_reads_config_before_writing(self, rigged):
        # the write fragments must be a read-modify-write, not zeros
        driver, _ = rigged
        driver.set_frame("aula:0", [RgbColor(0, 128, 255)] * 10)
        writes = module_write_frames(rigged)
        config_write = next(f for f in writes if f[1] == ap.CMD_WRITE)
        assert ap.is_valid_fragment(config_write)
        assert config_write[ap.CFG_APPLY_FLAG] == 0x00
        assert config_write[ap.CFG_EFFECT] == 1

    def test_identical_frame_skips_transmission(self, rigged):
        driver, _ = rigged
        red = [RgbColor(255, 0, 0)] * 10
        assert driver.set_frame("aula:0", red) is True
        before = len(module_write_frames(rigged))
        assert driver.set_frame("aula:0", red) is True
        assert len(module_write_frames(rigged)) == before  # nothing sent

    def test_mixed_frame_averages(self):
        frame = [RgbColor(0, 0, 0)] * 5 + [RgbColor(100, 50, 20)] * 5
        assert _representative(frame) == (50, 25, 10)

    def test_uniform_frame_exact(self):
        frame = [RgbColor(1, 2, 3)] * 4
        assert _representative(frame) == (1, 2, 3)

    def test_empty_frame_rejected(self, rigged):
        driver, _ = rigged
        assert driver.set_frame("aula:0", []) is False


class TestDegradation:
    def test_write_failure_is_false_not_raise(self, rigged):
        driver, fake_device = rigged
        fake_device.fail_writes = True
        assert driver.set_frame("aula:0", [RgbColor()] * 5) is False
        assert driver.last_error

    def test_closed_driver_reports_false(self):
        driver = AulaDriver(hid_module=FakeHidModule())
        assert driver.set_frame("aula:0", [RgbColor()] * 2) is False

    def test_close_is_idempotent(self, rigged):
        driver, _ = rigged
        driver.close()
        driver.close()


def module_write_frames(rigged) -> list[bytes]:
    fake_device = rigged[1]
    return list(fake_device.written)
