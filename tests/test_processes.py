from __future__ import annotations

from typing import Optional

import psutil
import pytest

from pulse_hwm.processes import (
    CATEGORY_APPS,
    CATEGORY_BG,
    CATEGORY_PULSE,
    CATEGORY_SERVICES,
    CATEGORY_WINDOWS,
    ProcessRow,
    ProcessScanner,
    cap_rows,
    classify_process,
    cpu_percent_from_delta,
    group_processes,
    in_windows_dir,
    set_low_priority_mode,
    terminate_process,
    trim_working_set,
)

WIN = r"C:\\Windows"


def row(
    pid=100, name="app.exe", exe="", username="", visible=None, cpu=0.0, mem_pct=0.0
):
    return ProcessRow(
        pid=pid,
        name=name,
        cpu=cpu,
        mem_pct=mem_pct,
        username=username,
        exe=exe,
        visible_window=visible,
    )


# ── path helper ────────────────────────────────────────────────────────────


def test_in_windows_dir_matches_prefix_not_partial_dir():
    assert in_windows_dir(WIN + "\\System32\\svchost.exe", WIN)
    assert not in_windows_dir(
        WIN + "apps\\fake.exe", WIN
    )  # C:\\Windowsapps != C:\\Windows
    assert not in_windows_dir("", WIN)
    assert not in_windows_dir(WIN + "\\svchost.exe", "")


# ── classification matrix ──────────────────────────────────────────────────


def test_pulse_classifies_self():
    assert classify_process(row(pid=42), self_pid=42, system_root=WIN) == CATEGORY_PULSE


def test_core_names_are_windows():
    for name in ("System", "registry", "MemCompression"):
        assert classify_process(row(pid=9, name=name), 1, WIN) == CATEGORY_WINDOWS


def test_exe_under_windows_dir_is_windows():
    assert (
        classify_process(row(pid=5, exe=WIN + "\\System32\\csrss.exe"), 1, WIN)
        == CATEGORY_WINDOWS
    )


def test_service_account_without_window_is_service():
    assert (
        classify_process(row(pid=7, username="NT AUTHORITY\\LOCAL SERVICE"), 1, WIN)
        == CATEGORY_SERVICES
    )


def test_visible_window_is_application():
    assert classify_process(row(pid=8, visible=True), 1, WIN) == CATEGORY_APPS


def test_everyday_user_no_window_is_background():
    assert classify_process(row(pid=9, username="AYMAN"), 1, WIN) == CATEGORY_BG


# ── grouping & ranking ─────────────────────────────────────────────────────


def test_group_processes_order_and_membership():
    rows = [
        row(pid=10, exe=WIN + "\\System32\\csrss.exe", cpu=5),
        row(pid=11, visible=True, cpu=80),
        row(pid=12, username="NT AUTHORITY\\SYSTEM", cpu=40),
        row(pid=13, username="AYMAN", cpu=1),
    ]
    for r in rows:
        r.category = classify_process(r, self_pid=1, system_root=WIN)
    groups = group_processes(rows)
    assert list(groups) == [
        CATEGORY_WINDOWS,
        CATEGORY_SERVICES,
        CATEGORY_APPS,
        CATEGORY_BG,
    ]
    assert groups[CATEGORY_WINDOWS][0].pid == 10
    assert groups[CATEGORY_APPS][0].pid == 11
    assert groups[CATEGORY_BG][0].pid == 13


def test_group_processes_drops_empty_categories():
    assert group_processes([]) == {}
    solo = row(pid=3, username="AYMAN")
    solo.category = CATEGORY_BG
    assert list(group_processes([solo])) == [CATEGORY_BG]


def test_weight_ranks_cpu_above_memory():
    cpu_heavy = row(pid=1, cpu=90, mem_pct=1)
    mem_heavy = row(pid=2, cpu=0, mem_pct=95)
    assert cpu_heavy.weight() > mem_heavy.weight()


# ── caps ───────────────────────────────────────────────────────────────────


def test_cap_rows_keeps_heaviest_plus_self():
    rows = [row(pid=i, cpu=float(100 - i)) for i in range(1, 12)]
    for r in rows:
        r.category = CATEGORY_BG
    own = row(pid=77, cpu=0.0)
    own.category = CATEGORY_PULSE
    rows.append(own)
    kept = cap_rows(rows, max_rows=3, self_pid=77)
    assert len(kept) == 4
    assert any(r.pid == 77 for r in kept)
    assert kept[:3] == sorted(rows[:11], key=lambda r: r.weight(), reverse=True)[:3]


def test_cap_rows_noop_when_under_limit():
    rows = [row(pid=1, cpu=1), row(pid=2, cpu=2)]
    assert len(cap_rows(rows, 10)) == 2


# ── CPU math ───────────────────────────────────────────────────────────────


def test_cpu_percent_delta_two_cores(monkeypatch):
    monkeypatch.setattr(psutil, "cpu_count", lambda logical=True: 2)
    # 0.2 cpu-seconds during a 0.1 s window on a 2-core machine = 100%
    assert cpu_percent_from_delta(1.0, 1.2, 0.1) == pytest.approx(100.0)
    # a shrinking counter (clock skew) stays at zero, never negative
    assert cpu_percent_from_delta(5.0, 4.0, 1.0) == 0.0


# ── scanner fakes (never real processes) ───────────────────────────────────


class FakeCpuTimes:
    def __init__(self, total: float):
        self.user = total
        self.system = 0.0


class FakeProc:
    """Mimics the slice of psutil.Process the scanner touches."""

    def __init__(
        self,
        pid: int,
        name: str,
        username: str = "",
        cpu_total: float = 0.0,
        create_time: Optional[float] = None,
    ):
        self.pid = pid
        self.info = {
            "pid": pid,
            "name": name,
            "memory_info": None,
            "memory_percent": 1.0,
            "status": "running",
            "create_time": create_time if create_time is not None else pid * 1.5,
        }
        self._username = username
        self._cpu_total = cpu_total

    def username(self):
        if self._username == "<denied>":
            raise psutil.AccessDenied("test")
        return self._username

    def exe(self):
        return ""

    def cpu_times(self):
        return FakeCpuTimes(self._cpu_total)


class FakeClock:
    def __init__(self, t: float = 1_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def build_scanner(processes, clock: FakeClock, self_pid: int = 1) -> ProcessScanner:
    return ProcessScanner(
        self_pid=self_pid,
        system_root=WIN,
        process_iter=lambda: iter(processes),
        clock=clock,
    )


# ── cpu % meaningful only from second scan ────────────────────────────────


def test_scanner_cpu_zero_first_then_measured(monkeypatch):
    monkeypatch.setattr(psutil, "cpu_count", lambda logical=True: 2)
    clock = FakeClock()
    proc = FakeProc(pid=10, name="game.exe", cpu_total=10.0)
    scanner = build_scanner([proc], clock)
    first = scanner.scan()
    assert first[0].cpu == 0.0
    clock.advance(0.5)
    proc._cpu_total = 11.0  # 1 cpu-second in a 0.5 s window
    second = scanner.scan()
    assert second[0].cpu == pytest.approx(100.0)  # 1 s / (0.5 s * 2 cores)


# ── cache eviction (the leak guard tests) ─────────────────────────────────


def test_scanner_cache_evicts_dead_pids():
    clock = FakeClock()

    def scan_with(live_pids: list[int], scanner=None) -> ProcessScanner:
        procs = [FakeProc(pid=p, name=f"proc{p}.exe") for p in live_pids]
        if scanner is None:
            return build_scanner(procs, clock)
        scanner._process_iter = lambda: iter(procs)
        return scanner

    scanner = scan_with([1, 2, 3])
    scanner.scan()
    assert set(scanner._cache) == {1, 2, 3}
    assert set(scanner._cpu_prev) == {1, 2, 3}

    scan_with([1, 2], scanner).scan()  # pid 3 died
    assert set(scanner._cache) == {1, 2}
    assert set(scanner._cpu_prev) == {1, 2}

    scan_with([], scanner).scan()  # everything died
    assert scanner._cache == {} and scanner._cpu_prev == {}


def test_scanner_cache_bounded_over_many_repeats():
    """Hammer the scanner: cache must never grow past the live process count."""
    clock = FakeClock()
    procs = [FakeProc(pid=p, name=f"p{p}.exe") for p in range(1, 31)]
    scanner = build_scanner(procs, clock)
    for _ in range(50):
        scanner.scan()
    assert len(scanner._cache) <= 30
    assert len(scanner._cpu_prev) <= 30


def test_scanner_reused_pid_invalidates_static_cache():
    clock = FakeClock()
    p1 = FakeProc(pid=42, name="same.exe", username="AYMAN", create_time=900.0)
    scanner = build_scanner([p1], clock)
    scanner.scan()
    assert scanner._cache[42]["username"] == "AYMAN"
    p2 = FakeProc(pid=42, name="same.exe", username="root", create_time=1900.0)
    scanner._process_iter = lambda: iter([p2])
    scanner.scan()
    assert scanner._cache[42]["username"] == "root"


# ── terminate / kill with fakes ────────────────────────────────────────────


class FakeTerminable:
    """Fake psutil.Process for terminate/kill/wait flows."""

    def __init__(
        self, pid: int, *, wait_extimes_out: bool = False, denied: bool = False
    ):
        self.pid = pid
        self.calls: list[str] = []
        self._wait_times_out = wait_extimes_out
        self._denied = denied

    def terminate(self):
        self.calls.append("terminate")
        if self._denied:
            raise psutil.AccessDenied("test")

    def kill(self):
        self.calls.append("kill")
        if self._denied:
            raise psutil.AccessDenied("test")

    def wait(self, timeout=None):
        if self._wait_times_out:
            raise psutil.TimeoutExpired(self.pid, timeout)


def test_terminate_refuses_protected_and_self():
    assert terminate_process(0, "kill").ok is False
    assert terminate_process(4, "kill").ok is False
    result = terminate_process(1234, "kill", self_pid=1234)
    assert result.ok is False and "refused" in result.message


def test_end_task_graceful_no_kill():
    fake = FakeTerminable(50)
    result = terminate_process(50, "end", process_factory=lambda pid: fake, grace_s=0.1)
    assert result.ok and not result.forced
    assert fake.calls == ["terminate"]


def test_end_task_forces_after_wait_timeout():
    fake = FakeTerminable(51, wait_extimes_out=True)
    result = terminate_process(51, "end", process_factory=lambda pid: fake, grace_s=0.1)
    assert result.ok and result.forced
    assert fake.calls == ["terminate", "kill"]


def test_kill_mode_skips_terminate():
    fake = FakeTerminable(52)
    result = terminate_process(52, "kill", process_factory=lambda pid: fake)
    assert result.ok
    assert fake.calls == ["kill"]


def test_terminate_access_denied_reports_failure():
    fake = FakeTerminable(53, denied=True)
    result = terminate_process(53, "kill", process_factory=lambda pid: fake)
    assert result.ok is False
    assert "denied" in result.message.lower()


def test_terminate_missing_process_is_not_an_error():
    def factory(_pid):
        raise psutil.NoSuchProcess(99)

    result = terminate_process(99, "kill", process_factory=factory)
    assert result.ok and "gone" in result.message


# ── good-citizen helpers degrade, never raise ─────────────────────────────


def test_trim_working_set_import_safe():
    assert trim_working_set() in (True, False)


def test_set_low_priority_never_raises():
    assert set_low_priority_mode(True) in (True, False)
    assert set_low_priority_mode(False) in (True, False)
