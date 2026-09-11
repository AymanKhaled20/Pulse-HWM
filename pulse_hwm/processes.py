from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

import psutil

# ── categories ──────────────────────────────────────────────────────────────
# Fixed display order for the grouped Processes tab. The keys are stable
# identifiers; the UI shows CATEGORY_LABELS.get(key, key) for each header.
CATEGORY_PULSE = "PULSE-HWM"
CATEGORY_WINDOWS = "WINDOWS"
CATEGORY_SERVICES = "SERVICES"
CATEGORY_APPS = "APPLICATIONS"
CATEGORY_BG = "BACKGROUND"

CATEGORY_ORDER = (
    CATEGORY_PULSE,
    CATEGORY_WINDOWS,
    CATEGORY_SERVICES,
    CATEGORY_APPS,
    CATEGORY_BG,
)

CATEGORY_LABELS = {
    CATEGORY_PULSE: "PULSE-HWM (this app)",
    CATEGORY_WINDOWS: "WINDOWS (system)",
    CATEGORY_SERVICES: "SERVICES (background jobs)",
    CATEGORY_APPS: "APPLICATIONS",
    CATEGORY_BG: "BACKGROUND",
}

# Account fragments that mean "an OS / machine job runs under this identity".
# Matched case-insensitively so "NT AUTHORITY\\LOCAL SERVICE" etc. all land
# in SERVICES. Windows service pseudo-accounts end in "$" (e.g. "user1$").
SERVICE_ACCOUNT_HINTS = ("system", "local service", "network service", "service$")

# Name-only kernel-ish processes (no .exe path exists for these).
SYSTEM_CORE_NAMES = {
    "system",
    "system idle process",
    "registry",
    "memcompression",
    "memory compression",
    "secure system",
}

# PIDs that terminate/kill must never touch (System Idle Process=0, System=4).
PROTECTED_PIDS = (0, 4)


@dataclass
class ProcessRow:
    """One row of the process table, already classified."""

    pid: int
    name: str
    cpu: float = 0.0  # % of total CPU (shared across all logical cores)
    mem_pct: float = 0.0  # % of physical RAM
    mem_rss: int = 0  # resident bytes
    username: str = ""
    status: str = ""
    exe: str = ""  # may be blank when access was denied
    create_time: Optional[float] = None
    category: str = CATEGORY_BG
    visible_window: Optional[bool] = None

    def weight(self) -> float:
        """Interest score used to rank rows (CPU counts more than RAM)."""
        return self.cpu * 3.0 + self.mem_pct


@dataclass
class TerminateResult:
    ok: bool
    message: str
    forced: bool = False  # True when kill() was needed after terminate()


# ── classification (pure functions — the unit-test heart) ──────────────────


def norm_path(path: str) -> str:
    return os.path.normpath(path).lower() if path else ""


def in_windows_dir(exe_path: str, system_root: str) -> bool:
    """True when an exe lives under %SystemRoot% (e.g. C:\\Windows\\...)."""
    if not exe_path or not system_root:
        return False
    exe = norm_path(exe_path)
    root = norm_path(system_root).rstrip("\\")
    sep = os.path.sep.lower()
    return exe.startswith(root + sep)


def _is_service_account(username: str) -> bool:
    if not username:
        return False
    lowered = username.lower()
    return any(hint in lowered for hint in SERVICE_ACCOUNT_HINTS)


def classify_process(row: ProcessRow, self_pid: int, system_root: str) -> str:
    """Assign one category per process; first matching rule wins."""
    if row.pid == self_pid:
        return CATEGORY_PULSE
    if row.name.lower() in SYSTEM_CORE_NAMES:
        return CATEGORY_WINDOWS
    if in_windows_dir(row.exe, system_root):
        return CATEGORY_WINDOWS
    # OS service accounts that are NOT under %SystemRoot% (Office
    # ClickToRun, update services...) still belong to the SERVICE group.
    if _is_service_account(row.username):
        return CATEGORY_SERVICES
    if row.visible_window:
        return CATEGORY_APPS
    return CATEGORY_BG


def group_processes(rows: list[ProcessRow]) -> dict[str, list[ProcessRow]]:
    """Bucket into the candidate categories; empty groups are dropped."""
    buckets: dict[str, list[ProcessRow]] = {}
    for row in sorted(rows, key=lambda r: r.weight(), reverse=True):
        buckets.setdefault(row.category, []).append(row)
    return {cat: buckets[cat] for cat in CATEGORY_ORDER if cat in buckets}


def _exe_dir(exe_path: str) -> str:
    path = norm_path(exe_path)
    return os.path.dirname(path) if path else ""


def _name_stem(name: str) -> str:
    """'bravesvc.exe' -> 'bravesvc'; case folded for family matching."""
    return name.lower().rsplit(".", 1)[0] if "." in name else name.lower()


def app_families(rows: list[ProcessRow]) -> dict[int, list[ProcessRow]]:
    """Task-Manager-style grouping: background processes that belong to a
    visible app become its children (so 'Brave' shows its real ~1GB RAM
    instead of a bare 400MB app row plus orphan background rows).

    Membership is deliberately conservative — a child qualifies ONLY when:
      * its exe lives in the SAME install directory as a visible app
        (e.g. Brave's helper processes run from Brave's own folder), or
      * its exe name family matches the app ('bravesvc' -> 'brave').
    WINDOWS / SERVICES / PULSE rows are never adopted, so OS jobs can't
    be mis-parented onto an app by accident.
    """
    apps = [r for r in rows if r.category == CATEGORY_APPS and r.visible_window]
    if not apps:
        return {}
    dir_owner: dict[str, int] = {}
    stem_owner: dict[str, int] = {}
    for app in apps:
        app_dir = _exe_dir(app.exe)
        if app_dir:
            # first visible app wins a directory; a rogue second exe there
            # is almost certainly the same program's other window
            dir_owner.setdefault(app_dir, app.pid)
        stem_owner.setdefault(_name_stem(app.name), app.pid)

    families: dict[int, list[ProcessRow]] = {}
    for r in rows:
        if r.category != CATEGORY_BG:
            continue
        parent = None
        r_dir = _exe_dir(r.exe)
        if r_dir and r_dir in dir_owner:
            parent = dir_owner[r_dir]
        else:
            # name-FAMILY match, not exact: "bravesvc" belongs to "brave".
            # 4-char minimum so a 1-2-letter exe can't chain onto an
            # unrelated app by sharing a short prefix.
            r_stem = _name_stem(r.name)
            if len(r_stem) >= 4:
                for app_stem, pid in stem_owner.items():
                    if r_stem.startswith(app_stem) or app_stem.startswith(r_stem):
                        parent = pid
                        break
        # own-PID guard is paranoia: a PULSE row can't be BACKGROUND, but a
        # crash between classify and here must never self-adopt
        if parent is not None and parent != r.pid:
            families.setdefault(parent, []).append(r)
    for kids in families.values():
        kids.sort(key=lambda r: r.weight(), reverse=True)
    return families


def cap_rows(
    rows: list[ProcessRow], max_rows: int, self_pid: Optional[int] = None
) -> list[ProcessRow]:
    """Keep only the heaviest `max_rows` rows, never dropping Pulse itself."""
    if max_rows <= 0 or len(rows) <= max_rows:
        return list(rows)
    ranked = sorted(rows, key=lambda r: r.weight(), reverse=True)
    keep = ranked[:max_rows]
    if self_pid is not None and all(r.pid != self_pid for r in keep):
        own = next((r for r in rows if r.pid == self_pid), None)
        if own is not None:
            keep.append(own)
    return keep


# ── CPU math ────────────────────────────────────────────────────────────────


# ── Task-Manager memory formula ("Working Set - Private") ────────────────


def private_working_set_map(debug_log=None) -> Optional[dict[int, int]]:
    """pid -> private working-set bytes for every process in one PDH pass.

    This is the SAME formula Task Manager's Memory column uses: the
    "Working Set - Private" performance counter, cross-referenced per
    process instance via its "ID Process" counter. PDH instance names are
    the exe image name without .exe; duplicates get a #k suffix. Wildcards
    merge same-name instances, so counters are added EXPLICITLY per
    instance and the query is primed with two collects (PDH needs a
    baseline sample before values become valid). Returns None on failure
    so callers fall back to USS/RSS.
    """
    try:
        import win32pdh
    except Exception:
        return None
    query = None
    counters = []
    result: dict[int, int] = {}
    try:
        try:
            _, instances = win32pdh.EnumObjectItems(
                None, None, "Process", win32pdh.PERF_DETAIL_WIZARD
            )
        except ValueError:  # pywin32 version differences in tuple shape
            instances = win32pdh.EnumObjectItems(
                None, None, "Process", win32pdh.PERF_DETAIL_WIZARD
            )[1]
        # rebuild dedup suffixes PDH expects: first keep plain, subsequent
        # occurrences of the same image name become name#1, name#2, ...
        seen_count: dict[str, int] = {}
        paths: list[str] = []
        for inst in instances:
            stem = inst.lower()
            n = seen_count.get(stem, 0)
            seen_count[stem] = n + 1
            paths.append(inst if n == 0 else f"{inst}#{n}")

        query = win32pdh.OpenQuery()
        for path in paths:
            base = f"\\Process({path})"
            try:
                counters.append(
                    (
                        win32pdh.AddCounter(query, base + "\\ID Process"),
                        win32pdh.AddCounter(query, base + "\\Working Set - Private"),
                    )
                )
            except Exception:
                continue  # one unusable instance must not kill the rest
        win32pdh.CollectQueryData(query)  # baseline (values come on #2)
        try:
            win32pdh.CollectQueryData(query)
        except Exception:
            pass  # a mid-scan process death can abort pass two; pass 1 cached
        for cid, cw in counters:
            try:
                status_pid, pid = win32pdh.GetFormattedCounterValue(
                    cid, win32pdh.PDH_FMT_LARGE
                )
                status_ws, ws = win32pdh.GetFormattedCounterValue(
                    cw, win32pdh.PDH_FMT_LARGE
                )
            except Exception:
                continue
            # 0x10000 = PDH_CSTATUS_VALID_DATA (often ORed with the 0x100
            # format flag by pywin32); the low bits are counter metadata

            def valid(s: int) -> bool:
                return bool(s & 0x10000) and (s & 0xFF) == 0

            if valid(status_pid) and valid(status_ws) and int(pid) > 0 and int(ws) >= 0:
                result[int(pid)] = int(ws)
        return result
    except Exception as exc:
        if debug_log is not None:
            debug_log(f"private_working_set_map failed: {exc!r}")
        return None
    finally:
        if query is not None:
            try:
                win32pdh.CloseQuery(query)
            except Exception:
                pass


def cpu_percent_from_delta(
    prev_cpu_s: float, now_cpu_s: float, wall_dt: float
) -> float:
    """% of ALL cpu cores used in this interval, clamped 0..100.

    Task-manager convention: 100% means one full machine, so cpu-seconds are
    divided by the logical core count.
    """
    if wall_dt <= 0:
        return 0.0
    cpu_dt = max(0.0, now_cpu_s - prev_cpu_s)
    ncpu = psutil.cpu_count() or 1
    return max(0.0, min(100.0, 100.0 * cpu_dt / wall_dt / ncpu))


# ── visible-window map (one EnumWindows pass, not per-process probes) ──────


def visible_window_pids() -> set[int]:
    """PIDs owning at least one visible top-level window; set() when unavailable.

    Imports pywin32 lazily and fails soft, so on non-Windows (or where the
    import fails) apps degrade into BACKGROUND instead of crashing a worker.
    """
    try:
        import win32gui
        import win32process
    except Exception:
        return set()
    pids: set[int] = set()

    def callback(hwnd: int, _lparam) -> bool:
        try:
            if win32gui.IsWindowVisible(hwnd):
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid:
                    pids.add(int(pid))
        except Exception:
            pass  # a dying window between check and query is harmless
        return True

    try:
        win32gui.EnumWindows(callback, None)
    except Exception:
        return set()
    return pids


# ── being a good citizen: priority + working-set trim ──────────────────────


def set_low_priority_mode(enabled: bool) -> bool:
    """Run Pulse below normal priority so other apps win CPU contention.

    Best effort only — returns False (never raises) on unsupported platforms
    or when the OS refuses.
    """
    try:
        me = psutil.Process(os.getpid())
        if os.name == "nt":
            below = psutil.BELOW_NORMAL_PRIORITY_CLASS
            normal = psutil.NORMAL_PRIORITY_CLASS
            me.nice(below if enabled else normal)
            return True
        me.nice(10 if enabled else 0)
        return True
    except (psutil.AccessDenied, OSError):
        return False


def trim_working_set() -> bool:
    """EmptyWorkingSet: hand cold pages of THIS process back to Windows.

    This is a hygiene tool: it doesn't free leaked memory, but it shrinks the
    resident footprint after cache eviction, which is what Task Manager shows.
    """
    if os.name != "nt":
        return False
    try:
        # 64-bit trap: HANDLE must be c_void_p — ctypes' default 32-bit int
        # truncates the pseudo-handle and every call fails with error 6.
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi = ctypes.windll.psapi
        psapi.EmptyWorkingSet.argtypes = (ctypes.c_void_p,)
        handle = kernel32.GetCurrentProcess()
        return bool(psapi.EmptyWorkingSet(handle))
    except Exception:
        return False


# ── terminate / kill (the task-manager action) ─────────────────────────────


def terminate_process(
    pid: int,
    mode: str = "end",
    *,
    process_factory: Callable[[int], object] = psutil.Process,
    grace_s: float = 2.0,
    self_pid: Optional[int] = None,
) -> TerminateResult:
    """Stop a process like Task Manager does.

    mode "end": terminate() first (lets it clean up briefly), wait grace_s,
                then kill() if it is still alive.
    mode "kill": kill() outright, no grace period.

    `process_factory` is injectable so tests pass a fake and never touch a
    real process.
    """
    if pid in PROTECTED_PIDS:
        return TerminateResult(False, f"refused: PID {pid} is OS-protected")
    if self_pid is not None and pid == self_pid:
        return TerminateResult(False, "refused: not stopping Pulse-HWM itself")
    if grace_s <= 0:
        grace_s = 2.0

    try:
        proc = process_factory(pid)
    except psutil.NoSuchProcess:
        return TerminateResult(True, "already gone")
    except (psutil.AccessDenied, PermissionError) as exc:
        return TerminateResult(False, f"access denied (prepare admin?): {exc}")
    except Exception as exc:
        return TerminateResult(False, f"cannot inspect PID {pid}: {exc}")

    try:
        if mode == "kill":
            proc.kill()
            return TerminateResult(True, "killed")
        proc.terminate()
    except psutil.NoSuchProcess:
        return TerminateResult(True, "already gone")
    except (psutil.AccessDenied, PermissionError) as exc:
        return TerminateResult(False, f"access denied (prepare admin?): {exc}")
    except Exception as exc:
        return TerminateResult(False, f"failed to signal PID {pid}: {exc}")

    if mode == "kill":
        return TerminateResult(True, "killed")

    try:
        proc.wait(timeout=grace_s)
        return TerminateResult(True, "ended")
    except Exception:
        # Not dying cleanly: force exactly like Task Manager does before it
        # finally says 'not respondfilled' -> kill + report as forced.
        try:
            proc.kill()
            return TerminateResult(True, "ended (forced kill)", forced=True)
        except Exception:
            return TerminateResult(True, "gone after signal", forced=True)


# ── scanner ─────────────────────────────────────────────────────────────────

# psutil fields fetched EVERY scan: all of these are fast kernel reads.
FAST_ATTRS = [
    "pid",
    "name",
    "memory_info",
    "memory_percent",
    "status",
    "create_time",
    "cpu_times",
]


class ProcessScanner:
    """Turns psutil's raw process list into grouped, ranked UI rows.

    Cost strategy
      * every scan fetches only FAST_ATTRS (kernel counters, no registry);
      * name/exe/username/create_time are cached per PID (keyed by create_time
        so a re-used PID cannot poison the cache) and re-read only when a PID
        is new or has a different create_time — those three calls are the
        SLOW Windows ones (token + path lookups);
      * both caches are pruned after every scan: dead PIDs vanish, which is
        what makes memory use provably bounded by the process count.
    """

    def __init__(
        self,
        self_pid: Optional[int] = None,
        system_root: Optional[str] = None,
        process_iter: Callable[[], Iterable] | None = None,
        clock: Callable[[], float] | None = None,
    ):
        self.self_pid = os.getpid() if self_pid is None else int(self_pid)
        self.system_root = system_root or os.environ.get("SystemRoot", r"C:\\Windows")
        # injectable for tests: fakes yield objects with a `.info` dict.
        self._process_iter = process_iter or _psutil_process_iter
        self._clock = clock or time.time
        self._cache: dict[int, dict] = {}  # pid -> static info
        self._cpu_prev: dict[int, tuple[float, float]] = {}  # pid -> (cpu s, wall ts)
        self.last_error: str | None = None
        self._priv_map: dict[int, int] | None = None  # pid -> private WS bytes

    def scan(self) -> list[ProcessRow]:
        now = self._clock()
        visible = visible_window_pids()
        # PDH pass BEFORE rows exist: exact Task-Manager private working set
        # per PID ("Working Set - Private" perf counter), independent of the
        # psutil iteration below
        self._priv_map = private_working_set_map()
        rows: list[ProcessRow] = []
        seen: set[int] = set()
        self.last_error = None
        for proc in self._process_iter():
            info = getattr(proc, "info", None) or {}
            pid = int(info.get("pid") or 0)
            if pid <= 0:
                continue
            seen.add(pid)
            row = self._row_for(proc, info, pid, now, visible)
            if row is not None:
                rows.append(row)
        # cache hygiene: keep only entries for processes still alive
        for cache in (self._cache, self._cpu_prev):
            keys = [p for p in cache if p not in seen]
            for p in keys:
                del cache[p]
        if not rows:
            self.last_error = "no readable processes this scan"
        return rows

    def _private_mem(self, proc, info: dict, pid: int) -> tuple[int, float]:
        """Task-Manager-identical memory: PRIVATE WORKING SET ("Working set
        - private" perf counter), read per PID by the PDH pass in scan().

        That counter is exactly what the Task Manager Memory column shows.
        Fallbacks, in order: psutil USS (still correct, slightly higher),
        then RSS when even that is refused (tests / access-denied). RSS is
        only a last resort because Windows DLLs are SHARED between sibling
        processes — summing it reads roughly 2x Task Manager.
        """
        total = psutil.virtual_memory().total or 1

        if self._priv_map is not None:
            priv = self._priv_map.get(pid)
            if priv and priv > 0:
                return int(priv), min(100.0, 100.0 * priv / total)

        full = None
        full_fn = getattr(proc, "memory_full_info", None)
        if callable(full_fn):
            try:
                full = full_fn()
            except Exception:
                full = None
            if full is not None:
                uss = int(getattr(full, "uss", 0) or 0)
                if uss > 0:
                    return uss, min(100.0, 100.0 * uss / total)
        # fallback: RSS (tests / access-denied)
        mem_info = info.get("memory_info")
        mem_rss = int(getattr(mem_info, "rss", 0) or 0) if mem_info is not None else 0
        mem_pct = float(info.get("memory_percent") or 0.0)
        return mem_rss, mem_pct

    def _row_for(
        self, proc, info: dict, pid: int, now: float, visible: set[int]
    ) -> Optional[ProcessRow]:
        try:
            static = self._static(proc, info, pid)
            mem_rss, mem_pct = self._private_mem(proc, info, pid)
            # empty `visible` (pywin32 missing / non-Windows) means "unknown",
            # not "no window" — keep those rows in BACKGROUND rather than
            # pretending we probed them.
            own_window = pid in visible if visible else None
            row = ProcessRow(
                pid=pid,
                name=static["name"],
                cpu=self._cpu_pct(proc, pid, now),
                mem_pct=mem_pct,
                mem_rss=mem_rss,
                username=static["username"],
                status=str(info.get("status") or ""),
                exe=static["exe"],
                create_time=static["create_time"],
                visible_window=own_window,
            )
            row.category = classify_process(row, self.self_pid, self.system_root)
            return row
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return None
        except Exception:
            # one unreadable process must never abort a whole scan
            self.last_error = f"process {pid} unreadable"
            return None

    def _static(self, proc, info: dict, pid: int) -> dict:
        cached = self._cache.get(pid)
        raw_ct = info.get("create_time")
        create_time: Optional[float] = None
        try:
            create_time = float(raw_ct) if raw_ct is not None else None
        except (TypeError, ValueError):
            create_time = None
        # Same PID + same create_time = (almost surely) the same process:
        # reuse cached slow fields instead of paying for them again.
        if cached is not None and cached.get("create_time") == create_time:
            return cached
        name = str(info.get("name") or "?") or "?"
        username = ""
        try:
            username = str(proc.username() or "")
        except Exception:
            username = ""
        exe = ""
        try:
            exe = str(proc.exe() or "")
        except Exception:
            exe = ""
        entry = {
            "name": name,
            "username": username,
            "exe": exe,
            "create_time": create_time,
        }
        self._cache[pid] = entry
        return entry

    def _cpu_pct(self, proc, pid: int, now: float) -> float:
        """% CPU since the previous scan, from accumulated cpu_times."""
        try:
            t = proc.cpu_times()
            total = float(getattr(t, "user", 0.0) or 0.0) + float(
                getattr(t, "system", 0.0) or 0.0
            )
        except Exception:
            return 0.0
        prev = self._cpu_prev.get(pid)
        self._cpu_prev[pid] = (total, now)
        if prev is None:
            return 0.0  # first sighting: no interval to average over yet
        return cpu_percent_from_delta(prev[0], total, now - prev[1])


def _psutil_process_iter() -> Iterable:
    """psutil Process iterator. ad_value=None → AccessDenied yields None
    fields instead of raising mid-iteration."""
    return psutil.process_iter(attrs=FAST_ATTRS, ad_value=None)
