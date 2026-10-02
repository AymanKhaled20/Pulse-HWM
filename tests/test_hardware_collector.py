"""Hardware collector + dashboard fixes: GPU retry backoff, COM init for the
WMI fallbacks, logged persist failures, unique temp names, CPU LED color."""

from __future__ import annotations

import logging

import pytest

from pulse_hwm.collectors import hardware
from pulse_hwm.collectors.hardware import GPU_RETRY_TICKS, HardwareCollector


class _FakeNvmlError(Exception):
    pass


class _FailingNvml:
    """Stands in for pynvml on a machine with no NVIDIA GPU."""

    NVMLError = _FakeNvmlError

    def __init__(self) -> None:
        self.init_calls = 0

    def nvmlInit(self) -> None:
        self.init_calls += 1
        raise _FakeNvmlError("no NVIDIA driver")


class TestGpuRetry:
    def test_failed_init_is_retried_once_per_window_not_every_tick(self, monkeypatch):
        fake = _FailingNvml()
        monkeypatch.setattr(hardware, "pynvml", fake)
        collector = HardwareCollector()

        for _ in range(GPU_RETRY_TICKS * 2):
            assert collector._gpu_snapshot() is None

        # tick 0 and tick GPU_RETRY_TICKS — not 120 attempts
        assert fake.init_calls == 2

    def test_successful_retry_resets_the_failure_count(self, monkeypatch):
        fake = _FailingNvml()
        monkeypatch.setattr(hardware, "pynvml", fake)
        collector = HardwareCollector()
        collector._gpu_snapshot()  # first probe fails
        assert collector._gpu_state["fail_count"] == 1

        # the GPU shows up (driver installed) by the next retry window
        def init_ok() -> None:
            collector._gpu_state["tried"] = True

        monkeypatch.setattr(collector, "_gpu_try_init", init_ok)
        collector._gpu_state["fail_count"] = GPU_RETRY_TICKS
        collector._gpu_snapshot()
        assert collector._gpu_state["fail_count"] == 0


class TestComInit:
    def test_without_pywin32_com_is_skipped(self, monkeypatch):
        monkeypatch.setattr(hardware, "pythoncom", None)
        assert hardware._com_init() is False

    def test_com_is_initialised_and_released_on_the_collector_thread(self, monkeypatch):
        calls: list[str] = []

        class FakeCom:
            def CoInitialize(self) -> None:
                calls.append("init")

            def CoUninitialize(self) -> None:
                calls.append("uninit")

        monkeypatch.setattr(hardware, "pythoncom", FakeCom())
        collector = HardwareCollector()
        collector._com_ready = hardware._com_init()
        collector.stop()
        assert calls == ["init", "uninit"]

    def test_com_failure_is_reported_not_raised(self, monkeypatch, caplog):
        class BrokenCom:
            def CoInitialize(self) -> None:
                raise OSError("RPC_E_CHANGED_MODE")

        monkeypatch.setattr(hardware, "pythoncom", BrokenCom())
        with caplog.at_level(logging.WARNING, logger="pulse.hardware"):
            assert hardware._com_init() is False
        assert "COM init failed" in caplog.text


class TestPersist:
    def test_db_failure_is_logged_instead_of_swallowed(self, monkeypatch, caplog):
        from pulse_hwm.db import Database

        class BrokenDb:
            def insert_hardware_samples(self, samples) -> None:
                raise RuntimeError("database is locked")

        monkeypatch.setattr(Database, "current", classmethod(lambda cls: BrokenDb()))
        snap = {
            "ts": 1.0,
            "cpu": {"total": 10.0},
            "mem": {"pct": 50.0},
            "net": {"rx_bps": 1.0, "tx_bps": 2.0},
        }
        with caplog.at_level(logging.WARNING, logger="pulse.hardware"):
            HardwareCollector()._persist(snap)
        assert "could not save hardware samples" in caplog.text


class TestTempNames:
    @staticmethod
    def _names(temps: list[dict]) -> list[str]:
        from pulse_hwm.ui.dashboard_tab import DashboardTab

        return [row["name"] for row in DashboardTab._categorize_temps(temps)]

    def test_two_ssds_get_their_own_rows(self):
        names = self._names(
            [
                {"label": "Samsung 980 — Composite Temperature", "temp": 40.0},
                {"label": "WD Black — Composite Temperature", "temp": 45.0},
            ]
        )
        assert names == ["SSD", "SSD #2"]

    def test_long_names_stay_unique_after_truncation(self):
        long_sensor = "Some Extremely Long Sensor Name That Overflows"
        names = self._names(
            [
                {"label": f"Board A — {long_sensor} 1", "temp": 30.0},
                {"label": f"Board B — {long_sensor} 2", "temp": 31.0},
            ]
        )
        assert len(set(names)) == 2

    def test_unique_names_are_left_alone(self):
        names = self._names(
            [
                {"label": "Intel CPU — CPU Package", "temp": 55.0},
                {"label": "NVIDIA — GPU Core", "temp": 50.0},
            ]
        )
        assert names == ["CPU", "GPU Core"]


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class TestCpuLed:
    @staticmethod
    def _center_color(led) -> str:
        image = led.grab().toImage()
        return image.pixelColor(led.width() // 2, led.height() // 2).name().upper()

    def test_led_shows_the_color_it_is_given(self, app):
        from pulse_hwm.ui import theme as T
        from pulse_hwm.ui.dashboard_tab import GaugeLed

        led = GaugeLed()
        led.set_state(True, T.DANGER)
        assert self._center_color(led) == T.DANGER.upper()

    def test_led_without_a_color_falls_back_to_on_off(self, app):
        from pulse_hwm.ui import theme as T
        from pulse_hwm.ui.dashboard_tab import GaugeLed

        led = GaugeLed()
        led.set_state(False)
        assert self._center_color(led) == T.DANGER.upper()
        led.set_state(True)
        assert self._center_color(led) == T.SUCCESS.upper()
