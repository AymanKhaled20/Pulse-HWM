from __future__ import annotations

import sys
import threading
import zipfile
from pathlib import Path

LHM_VERSION = "0.9.6"
LHM_ZIP_URL = (
    f"https://github.com/LibreHardwareMonitor/LibreHardwareMonitor"
    f"/releases/download/v{LHM_VERSION}/LibreHardwareMonitor.zip"
)
RUNTIME_DIRNAME = "lhm_runtime"
DLL_NAME = "LibreHardwareMonitorLib.dll"


def runtime_dir() -> Path:
    """Dev: pulse_hwm/assets/lhm_runtime. Packaged: _MEIPASS/lhm_runtime."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass and (Path(meipass) / RUNTIME_DIRNAME / DLL_NAME).exists():
            return Path(meipass) / RUNTIME_DIRNAME
        return Path(sys.executable).parent / RUNTIME_DIRNAME
    return Path(__file__).resolve().parent.parent / "assets" / RUNTIME_DIRNAME


def lib_path() -> Path:
    return runtime_dir() / DLL_NAME


def is_available() -> bool:
    return lib_path().exists()


def download_to(destdir: Path, on_progress=None) -> tuple[bool, str]:
    """Stream the official bundle zip into destdir (one-time fetch)."""
    import httpx

    destdir.mkdir(parents=True, exist_ok=True)
    zip_path = destdir / "LibreHardwareMonitor.zip"
    try:
        total = 0
        with httpx.stream(
            "GET", LHM_ZIP_URL, timeout=120.0, follow_redirects=True
        ) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0)
            with zip_path.open("wb") as fh:
                done = 0
                for chunk in resp.iter_bytes(1 << 16):
                    fh.write(chunk)
                    done += len(chunk)
                    if on_progress is not None:
                        on_progress(done, total)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(destdir)
        zip_path.unlink(missing_ok=True)
        return (is_available(), "LibreHardwareMonitor runtime installed")
    except Exception as exc:
        zip_path.unlink(missing_ok=True)
        return False, str(exc)


class LibreSensors:
    """In-process reflection bridge to LibreHardwareMonitorLib (MPL-2.0).

    Sensor types matched by enum ToString so bumps to the library stay safe.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._obj = None
        self._tried = False
        self._error: str | None = None

    def _ensure(self) -> bool:
        with self._lock:
            if self._tried:
                return self._obj is not None
            self._tried = True
            self._obj = self._create()
            return self._obj is not None

    def _create(self):
        if not is_available():
            self._error = "runtime not fetched yet"
            return None
        try:
            from pythonnet import load

            load("netfx")
            import clr
            from System import Activator
            from System.Reflection import Assembly

            dll_dir = runtime_dir()
            if str(dll_dir) not in sys.path:
                sys.path.insert(0, str(dll_dir))
            clr.AddReference("LibreHardwareMonitorLib")
            asm = Assembly.LoadFrom(str(lib_path()))
            computer_t = [t for t in asm.ExportedTypes if t.Name == "Computer"][0]
            comp = Activator.CreateInstance(computer_t)
            comp.IsCpuEnabled = True
            comp.IsGpuEnabled = True
            comp.IsMotherboardEnabled = True
            comp.IsStorageEnabled = True
            comp.IsMemoryEnabled = True
            comp.IsNetworkEnabled = False
            comp.IsBatteryEnabled = False
            comp.Open()
            return comp
        except Exception as exc:
            self._error = str(exc)
            return None

    def read(self) -> list[dict] | None:
        """[(label, temp)] from all temperature sensors; None when unavailable."""
        if not self._ensure():
            return None
        try:
            out: list[dict] = []
            for hw in list(self._obj.Hardware):
                hw.Update()
                self._collect(hw, out)
            return out or None
        except Exception as exc:
            self._error = str(exc)
            return None

    def _collect(self, hw, out: list[dict]) -> None:
        for s in hw.Sensors:
            if s.SensorType.ToString() == "Temperature" and s.Value is not None:
                temp = float(s.Value)
                if -50.0 < temp < 140.0:
                    out.append({"label": f"{hw.Name} — {s.Name}"[:60], "temp": temp})
        for sub in list(hw.SubHardware):
            try:
                sub.Update()
            except Exception:
                pass
            self._collect(sub, out)

    def close(self) -> None:
        with self._lock:
            if self._obj is not None:
                try:
                    self._obj.Close()
                except Exception:
                    pass
                self._obj = None

    @property
    def last_error(self) -> str | None:
        return self._error
