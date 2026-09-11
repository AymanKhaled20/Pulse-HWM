from __future__ import annotations

import time
from typing import Optional

import psutil
from PySide6.QtCore import QObject, QTimer, Signal

try:
    import pynvml
except Exception:  # NVIDIA driver/CLI absent
    pynvml = None

try:
    import wmi as wmi_module
except Exception:
    wmi_module = None


TOP_PROC_REFRESH = 5
GPU_RETRY_TICKS = 60
TEMP_REFRESH = 5


class HardwareCollector(QObject):
    """Polls psutil + GPU/temp providers; emits one snapshot dict per tick."""

    updated = Signal(dict)
    persisted = Signal(int)  # rows written

    def __init__(
        self,
        interval_ms: int = 1000,
        persist_every: int = 10,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.interval_ms = max(250, interval_ms)
        self.persist_every = max(1, persist_every)
        self._tick = 0
        self._lhm = None
        self._last_io: Optional[tuple[int, int, float]] = None  # (read, write, ts)
        self._last_nic: Optional[tuple[int, int, float]] = None  # (rx, tx, ts)
        self._gpu_state: dict = {"tried": False, "handles": [], "fail_count": 0}
        self._temp_cache: tuple[float, list[dict]] = (0.0, [])
        self._procs_cache: tuple[float, list[dict]] = (0.0, [])
        psutil.cpu_percent(interval=None, percpu=True)  # prime per-core counters

    # -- main tick --------------------------------------------------------
    def collect(self) -> dict:
        snap = self._collect_core()
        self._tick += 1
        if self.persist_every and self._tick % self.persist_every == 0:
            self._persist(snap)
        self.updated.emit(snap)
        return snap

    def start(self) -> None:
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.collect)
        self._timer.start(self.interval_ms)
        self.collect()

    def stop(self) -> None:
        if getattr(self, "_timer", None):
            self._timer.stop()
        if isinstance(getattr(self, "_lhm", None), object) and self._lhm not in (
            None,
            False,
        ):
            try:
                self._lhm.close()
            except Exception:
                pass

    # -- core snapshot ------------------------------------------------------
    def _collect_core(self) -> dict:
        ts = time.time()
        cpu_total = psutil.cpu_percent(interval=None)
        cores = psutil.cpu_percent(interval=None, percpu=True)
        freq = None
        try:
            f = psutil.cpu_freq()
            freq = getattr(f, "current", None) if f else None
        except Exception:
            pass

        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        mem = {
            "total": vm.total,
            "used": vm.used,
            "available": vm.available,
            "pct": vm.percent,
            "swap_total": sw.total,
            "swap_used": sw.used,
            "swap_pct": sw.percent,
        }

        disks = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except (OSError, PermissionError):
                continue
            disks.append(
                {
                    "mount": part.mountpoint,
                    "fs": part.fstype,
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free,
                    "pct": usage.percent,
                }
            )

        diskio = self._disk_rate(ts)
        net = self._net_rate(ts)

        cpu = {
            "total": cpu_total,
            "cores": cores,
            "freq_mhz": freq,
            "name": _cpu_name(),
            "logical": psutil.cpu_count(),
            "physical": psutil.cpu_count(logical=False),
            "uptime_s": max(0, int(time.time() - psutil.boot_time())),
        }
        return {
            "ts": ts,
            "cpu": cpu,
            "mem": mem,
            "disks": disks,
            "diskio": diskio,
            "net": net,
            "gpu": self._gpu_snapshot(),
            "temps": self._temps(),
            "battery": self._battery(),
            "procs": self._procs_norm(),
        }

    def _disk_rate(self, ts: float) -> dict:
        out = {"read_bps": None, "write_bps": None}
        try:
            io = psutil.disk_io_counters()
        except Exception:
            io = None
        if io is not None:
            read = io.read_bytes
            write = io.write_bytes
            if self._last_io is not None:
                prev_read, prev_write, prev_ts = self._last_io
                dt = ts - prev_ts
                if dt > 0:
                    out["read_bps"] = max(0.0, (read - prev_read) / dt)
                    out["write_bps"] = max(0.0, (write - prev_write) / dt)
            self._last_io = (read, write, ts)
        return out

    def _net_rate(self, ts: float) -> dict:
        out = {"rx_bps": 0.0, "tx_bps": 0.0}
        try:
            nic = psutil.net_io_counters(pernic=False)
        except Exception:
            nic = None
        if nic is not None:
            if self._last_nic is not None:
                prev_rx, prev_tx, prev_ts = self._last_nic
                dt = ts - prev_ts
                if dt > 0:
                    out["rx_bps"] = max(0.0, (nic.bytes_recv - prev_rx) / dt)
                    out["tx_bps"] = max(0.0, (nic.bytes_sent - prev_tx) / dt)
            self._last_nic = (nic.bytes_recv, nic.bytes_sent, ts)
            out["rx_total"] = nic.bytes_recv
            out["tx_total"] = nic.bytes_sent
        return out

    # -- top processes ----------------------------------------------------
    def _procs_norm(self) -> list[dict]:
        rows = self._procs_refresh()
        count = max(1, psutil.cpu_count() or 1)
        return [
            {
                "pid": r["pid"],
                "name": r["name"],
                "cpu": r["cpu"] / count,
                "mem": r["mem"],
            }
            for r in rows
        ]

    def _procs_refresh(self) -> list[dict]:
        now = time.time()
        if self._procs_cache[0] and now - self._procs_cache[0] < TOP_PROC_REFRESH:
            return self._procs_cache[1]
        rows = []
        for p in psutil.process_iter(["pid", "name", "memory_percent"]):
            try:
                cpu = p.cpu_percent(interval=None)
                mem = p.info.get("memory_percent") or 0.0
                rows.append(
                    {
                        "pid": p.info["pid"],
                        "name": p.info.get("name") or "?",
                        "cpu": cpu,
                        "mem": mem,
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        rows.sort(key=lambda r: (r["cpu"], r["mem"]), reverse=True)
        rows = rows[:6]
        self._procs_cache = (now, rows)
        return rows

    # -- GPU ------------------------------------------------------------------
    def _gpu_snapshot(self) -> dict | None:
        state = self._gpu_state
        if pynvml is None:
            return None
        if not state["tried"]:
            self._gpu_try_init()
        if not state["tried"]:
            state["fail_count"] += 1
            if state["fail_count"] % GPU_RETRY_TICKS == 0:
                state["tried"] = False
            return None
        devices = []
        try:
            for handle in state["handles"]:
                util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                temp = pynvml.nvmlDeviceGetTemperature(
                    handle, pynvml.NVML_TEMPERATURE_GPU
                )
                devices.append(
                    {
                        "name": _decode(pynvml.nvmlDeviceGetName(handle)),
                        "util_pct": float(util.gpu),
                        "vram_used": float(mem.used),
                        "vram_total": float(mem.total),
                        "temp_c": float(temp),
                    }
                )
        except pynvml.NVMLError:
            state["tried"] = False
            return None
        return {"devices": devices} if devices else None

    def _gpu_try_init(self) -> None:
        state = self._gpu_state
        try:
            pynvml.nvmlInit()
            count = pynvml.nvmlDeviceGetCount()
            state["handles"] = [
                pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(count)
            ]
            state["tried"] = bool(state["handles"])
        except Exception:
            state["tried"] = False

    # -- temperatures ------------------------------------------------------------
    def _temps(self) -> list[dict] | None:
        now = time.time()
        if self._temp_cache[0] and now - self._temp_cache[0] < TEMP_REFRESH:
            return self._temp_cache[1]
        temps = self._temps_lhm_lib() or self._temps_lhm() or self._temps_acpi()
        self._temp_cache = (now, temps)
        return temps

    def _temps_lhm_lib(self) -> list[dict] | None:
        """In-process LibreHardwareMonitorLib (one-time runtime fetch)."""
        if self._lhm is None:
            try:
                from pulse_hwm.collectors.lhm import LibreSensors, is_available

                if not is_available():
                    return None
                self._lhm = LibreSensors()
            except Exception:
                self._lhm = False
                return None
        if self._lhm is False:
            return None
        return self._lhm.read()

    def _temps_lhm(self) -> list[dict] | None:
        if wmi_module is None:
            return None
        try:
            c = wmi_module.WMI(namespace="root\\LibreHardwareMonitor")
            sensors = c.query(
                "SELECT Name, Value FROM Sensor WHERE SensorType='Temperature'"
            )
        except Exception:
            return None
        rows = [
            {"label": s.Name, "temp": s.Value} for s in sensors if s.Value is not None
        ]
        return rows or None

    def _temps_acpi(self) -> list[dict] | None:
        if wmi_module is None:
            return None
        try:
            c = wmi_module.WMI(namespace="root\\WMI")
            rows = []
            for zone in c.MSAcpi_ThermalZoneTemperature():
                celsius = (float(zone.CurrentTemperature) - 2732.0) / 10.0
                if -40.0 < celsius < 120.0:
                    rows.append(
                        {"label": zone.InstanceName or "ACPI zone", "temp": celsius}
                    )
            return rows or None
        except Exception:
            return None

    # -- battery -------------------------------------------------------------------
    def _battery(self) -> dict | None:
        try:
            bat = psutil.sensors_battery()
        except Exception:
            bat = None
        if bat is None:
            return None
        return {
            "pct": bat.percent,
            "plugged": bool(bat.power_plugged),
            "secs_left": bat.secsleft,
        }

    # -- persistence ------------------------------------------------------------------
    def _persist(self, snap: dict) -> None:
        try:
            from pulse_hwm.db import Database

            db = Database.current()
            if db is None:
                return
            ts = snap["ts"]
            samples = [
                (ts, "cpu_total", float(snap["cpu"]["total"])),
                (ts, "mem_pct", float(snap["mem"]["pct"])),
            ]
            if snap["net"].get("rx_bps") is not None:
                samples.append((ts, "net_rx_bps", float(snap["net"]["rx_bps"])))
                samples.append((ts, "net_tx_bps", float(snap["net"]["tx_bps"])))
            n = len(samples)
            db.insert_hardware_samples(samples)
            self.persisted.emit(n)
        except Exception:
            pass


def _cpu_name() -> str:
    try:
        import platform

        return platform.processor() or "Unknown CPU"
    except Exception:
        return "Unknown CPU"


def _decode(value) -> str:
    if value is None:
        return "?"
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


class HardwareThreadBridge:
    """Helper for running HardwareCollector on a background thread."""

    @staticmethod
    def attach(thread_obj, interval_ms: int) -> HardwareCollector:
        collector = HardwareCollector(interval_ms=interval_ms)
        collector.moveToThread(thread_obj)
        thread_obj.started.connect(collector.start)
        thread_obj.finished.connect(collector.stop)
        return collector
