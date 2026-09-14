from __future__ import annotations

import os

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pulse_hwm import app_settings
from pulse_hwm.processes import (
    CATEGORY_LABELS,
    PROTECTED_PIDS,
    set_low_priority_mode,
    terminate_process,
    trim_working_set,
)
from pulse_hwm.util import human_bytes

COL_PID, COL_CPU, COL_MEM_PCT, COL_RAM, COL_USER = 1, 2, 3, 4, 5

_RAM_UNITS = {
    "B": 1.0,
    "KB": 1024.0,
    "MB": 1024.0**2,
    "GB": 1024.0**3,
    "TB": 1024.0**4,
}


def _ram_bytes(text: str) -> float:
    """Turn a human_bytes() cell like '1,023.5 KB' back into bytes for sorting."""
    try:
        number, unit = text.strip().split(" ", 1)
        return float(number.replace(",", "")) * _RAM_UNITS.get(unit.upper(), 1.0)
    except (ValueError, IndexError):
        return -1.0  # N/A / unknown sorts low on purpose


class _TerminateSignals(QObject):
    """Signal carrier so a QRunnable can talk back to the UI thread."""

    done = Signal(int, str, bool)  # pid, message, ok


class TerminateTask(QRunnable):
    """Runs the (blocking) terminate/kill on the thread pool, not the UI."""

    def __init__(self, pid: int, mode: str, signals: _TerminateSignals):
        super().__init__()
        self._pid = pid
        self._mode = mode
        self._signals = signals

    def run(self) -> None:  # worker thread
        result = terminate_process(self._pid, self._mode, self_pid=os.getpid())
        self._signals.done.emit(self._pid, result.message, result.ok)


def _matches(child_text: str, pid_text: str, needle: str) -> bool:
    if not needle:
        return True
    return needle in child_text.lower() or needle in pid_text


class ProcessesTab(QWidget):
    """Task-manager style tab: related processes grouped under banners.

    Resource contract with the collector:
      * scans run only while this tab is visible (showEvent / hideEvent);
      * rows are updated in place by PID — the tree is never torn down and
        rebuilt, so widgets (and the C++ objects behind them) don't churn.
    """

    refresh_requested = Signal()
    # the collector object lives on the worker thread, so it must be reached
    # through queued signals — never called directly from the UI thread
    # (QTimer.start() from the wrong thread is undefined behavior)
    active_changed = Signal(bool)
    # the SETTINGS checkbox is the same preference — emitted only after the
    # OS accepted the priority change and the DB saved it
    priority_changed = Signal(bool)

    def __init__(self, collector, db=None, parent=None):
        super().__init__(parent)
        self.setObjectName("root")
        self._collector = collector
        self._db = db  # optional: persisted LOW PRIORITY needs the settings DB
        self._pool = QThreadPool.globalInstance()
        # signals object must outlive the QRunnable, so it lives here
        self._terminate_signals = _TerminateSignals()
        self._terminate_signals.done.connect(self._on_terminate_done)
        self._pid_items: dict[int, tuple[QTreeWidgetItem, QTreeWidgetItem]] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        # ── toolbar ────────────────────────────────────────────────────────
        bar = QHBoxLayout()
        self.summary = QLabel("0 PROCESSES")
        self.summary.setObjectName("muted")
        bar.addWidget(self.summary)
        bar.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("FILTER…")
        self.search.setFixedWidth(180)
        self.search.textChanged.connect(self._apply_filter)
        bar.addWidget(self.search)
        trim_btn = QPushButton("TRIM MEMORY")
        trim_btn.setToolTip("Hand Pulse's cold memory pages back to the OS")
        trim_btn.clicked.connect(self._trim_now)
        bar.addWidget(trim_btn)
        self.prio_btn = QPushButton("LOW PRIORITY")
        self.prio_btn.setCheckable(True)
        self.prio_btn.setToolTip(
            "Run Pulse below normal CPU priority (same preference as the"
            " LIMIT PULSE RESOURCES checkbox in Settings)"
        )
        self.prio_btn.toggled.connect(self._toggle_priority)
        bar.addWidget(self.prio_btn)
        # start from the persisted preference so the button reflects reality
        # after a restart (blocked: programmatic fill must not apply/save)
        if self._db is not None:
            on = app_settings.load(self._db).limit_resources
            self.prio_btn.blockSignals(True)
            self.prio_btn.setChecked(on)
            self.prio_btn.blockSignals(False)
        outer.addLayout(bar)

        self.status = QLabel("")
        self.status.setObjectName("muted")
        outer.addWidget(self.status)

        # ── grouped tree ───────────────────────────────────────────────────
        self.tree = QTreeWidget()
        self.tree.setColumnCount(6)
        self.tree.setHeaderLabels(["PROCESS", "PID", "CPU %", "MEM %", "RAM", "USER"])
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col, width in (
            (COL_PID, 70),
            (COL_CPU, 70),
            (COL_MEM_PCT, 70),
            (COL_RAM, 110),
            (COL_USER, 140),
        ):
            self.tree.setColumnWidth(col, width)
        self.tree.setUniformRowHeights(True)  # cheaper paint/measure on big lists
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        outer.addWidget(self.tree, 1)

        # ── click-a-header-to-sort ────────────────────────────────────────
        # We sort GROUP MEMBERS only (group banners keep their fixed
        # APPLICATIONS → BACKGROUND order) — like Task Manager, the metric
        # columns re-rank each list inside every group. Numeric columns are
        # parsed back to numbers so '999.0 KB' sorts below '1.0 MB'.
        self._sort_col = -1
        self._sort_desc = True
        self.tree.header().setSectionsClickable(True)
        self.tree.header().sectionClicked.connect(self._on_sort_clicked)

        collector.updated.connect(self._on_snapshot)
        self.refresh_requested.connect(collector.refresh_now)
        self.active_changed.connect(collector.set_active)
        # the tab starts hidden until the main window shows it — queued signal
        # ensures set_active runs on the worker thread
        self.active_changed.emit(False)

    # ── lifecycle: scan only while visible ─────────────────────────────────
    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.active_changed.emit(True)
        self.refresh_requested.emit()  # catch up on data collected while away

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.active_changed.emit(False)

    # ── snapshot application ───────────────────────────────────────────────
    def _on_snapshot(self, snap: dict) -> None:
        groups = snap.get("groups") or {}
        self.summary.setText(
            f"{snap.get('total', 0)} PROCESSES — {len(groups)} GROUPS "
            f"(TOP {snap.get('shown', 0)})"
        )
        seen_pids: set[int] = set()
        for category, rows in groups.items():
            parent = self._group_item(category, len(rows))
            for rd in rows:
                row_item = self._upsert_row(parent, rd)
                seen_pids.add(int(rd["pid"]))
                # app rows carry their adopted background helpers: Task-
                # Manager style, the parent shows the COMBINED footprint
                kids = rd.get("children") or []
                if kids:
                    kids_cpu = rd["cpu"] + sum(k["cpu"] for k in kids)
                    kids_pct = rd["mem_pct"] + sum(k["mem_pct"] for k in kids)
                    kids_rss = rd["mem_rss"] + sum(k["mem_rss"] for k in kids)
                    row_item.setText(COL_CPU, f"{kids_cpu:.1f}")
                    row_item.setText(COL_MEM_PCT, f"{kids_pct:.1f}")
                    row_item.setText(COL_RAM, human_bytes(kids_rss))
                    # auto-expand ONCE (first time kids arrive): a manual
                    # collapse must survive every 3s snapshot redraw
                    if row_item.data(0, Qt.ItemDataRole.UserRole) is None:
                        row_item.setData(0, Qt.ItemDataRole.UserRole, "auto-expanded")
                        row_item.setExpanded(True)
                    for kid in kids:
                        seen_pids.add(int(kid["pid"]))
                        self._upsert_row(row_item, kid)
        # remove rows whose process died since the last snapshot
        for pid in [pid for pid in self._pid_items if pid not in seen_pids]:
            parent, child = self._pid_items.pop(pid)
            parent.removeChild(child)
        self._apply_sort()  # keep the requested order as values tick
        self._apply_filter(self.search.text())

    # ── header sorting: re-orders rows inside each group only ───────────
    def _on_sort_clicked(self, col: int) -> None:
        if col == self._sort_col:
            self._sort_desc = not self._sort_desc  # second click: flip order
        else:
            self._sort_col = col
            self._sort_desc = True  # first click: biggest first
        order = (
            Qt.SortOrder.DescendingOrder
            if self._sort_desc
            else Qt.SortOrder.AscendingOrder
        )
        self.tree.header().setSortIndicator(col, order)
        self._apply_sort()

    def _sort_key(self, child: QTreeWidgetItem, col: int):
        text = child.text(col)
        if col == COL_PID:
            try:
                return (0, int(text), "")
            except ValueError:
                return (1, 0, "")
        if col in (COL_CPU, COL_MEM_PCT):
            try:
                return (0, float(text), "")
            except ValueError:
                return (1, 0.0, "")
        if col == COL_RAM:
            return (0, _ram_bytes(text), "")
        return (0, 0.0, text.lower())  # PROCESS / USER: plain text

    def _apply_sort(self) -> None:
        if self._sort_col < 0:
            return
        col, desc = self._sort_col, self._sort_desc
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            children = parent.takeChildren()
            # hidden state lives on the item, so re-adding keeps the filter
            for child in sorted(
                children, key=lambda c: self._sort_key(c, col), reverse=desc
            ):
                parent.addChild(child)

    def _group_item(self, category: str, count: int) -> QTreeWidgetItem:
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.data(0, Qt.ItemDataRole.UserRole) == category:
                item.setText(0, f"{CATEGORY_LABELS.get(category, category)} — {count}")
                return item
        item = QTreeWidgetItem([f"{CATEGORY_LABELS.get(category, category)} — {count}"])
        item.setData(0, Qt.ItemDataRole.UserRole, category)
        item.setFirstColumnSpanned(True)
        self.tree.addTopLevelItem(item)
        # BACKGROUND is usually the biggest pile: collapsed, but one click away
        item.setExpanded(category != "BACKGROUND")
        return item

    def _upsert_row(self, parent: QTreeWidgetItem, rd: dict) -> QTreeWidgetItem:
        pid = int(rd["pid"])
        user_short = (rd["username"].rsplit("\\", 1)[-1] if rd.get("username") else "")[
            :22
        ]
        entry = self._pid_items.get(pid)
        if entry is None or entry[0] is not parent:
            if entry is not None:
                entry[0].removeChild(entry[1])
            child = QTreeWidgetItem(
                [
                    str(rd["name"])[:44],
                    str(pid),
                    "0.0",
                    "0.0",
                    human_bytes(rd["mem_rss"]),
                    user_short,
                ]
            )
            for col in (COL_PID, COL_CPU, COL_MEM_PCT, COL_RAM):
                child.setTextAlignment(
                    col, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
            parent.addChild(child)
            self._pid_items[pid] = (parent, child)
        else:
            child = entry[1]
            child.setText(0, str(rd["name"])[:44])
            child.setText(COL_RAM, human_bytes(rd["mem_rss"]))
            child.setText(COL_USER, user_short)
        child.setText(COL_CPU, f"{rd['cpu']:.1f}")
        child.setText(COL_MEM_PCT, f"{rd['mem_pct']:.1f}")
        return child

    # ── search filter ──────────────────────────────────────────────────────
    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            matched = 0
            for j in range(parent.childCount()):
                child = parent.child(j)
                hit = _matches(child.text(0), child.text(COL_PID), needle)
                child.setHidden(bool(needle) and not hit)
                if hit:
                    matched += 1
            if needle:
                parent.setHidden(matched == 0)
                parent.setExpanded(True)
            else:
                parent.setHidden(False)

    # ── right-click: END TASK / KILL ───────────────────────────────────────
    def _context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        if item.parent() is None:  # group header clicked
            item = self._first_visible_child(item)
        if item is None:
            return
        pid = int(item.text(COL_PID) or 0)
        name = item.text(0)
        menu = QMenu(self)
        title = menu.addAction(f"{name}  (PID {pid})")
        title.setEnabled(False)
        menu.addSeparator()
        if pid in PROTECTED_PIDS:
            note = menu.addAction("PID OS-PROTECTED")
            note.setEnabled(False)
        else:
            end_action = menu.addAction("END TASK")  # graceful, then force
            kill_action = menu.addAction("KILL")  # immediate force
            end_action.triggered.connect(lambda: self._start_terminate(pid, "end"))
            kill_action.triggered.connect(lambda: self._start_terminate(pid, "kill"))
        menu.addSeparator()
        copy_pid = menu.addAction("COPY PID")
        copy_pid.triggered.connect(lambda: QApplication.clipboard().setText(str(pid)))
        menu.addSeparator()
        open_btn = menu.addAction("OPEN FILE LOCATION")
        open_btn.triggered.connect(lambda: self._open_location(pid, name))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _first_visible_child(self, parent: QTreeWidgetItem):
        for j in range(parent.childCount()):
            child = parent.child(j)
            if not child.isHidden():
                return child
        return None

    def _start_terminate(self, pid: int, mode: str) -> None:
        verb = "ending" if mode == "end" else "killing"
        self._set_status(f"{verb} PID {pid} …")
        self._pool.start(TerminateTask(pid, mode, self._terminate_signals))

    def _on_terminate_done(self, pid: int, message: str, ok: bool) -> None:
        self._set_status(f"PID {pid}: {message}")
        if ok:
            self.refresh_requested.emit()  # rescan now that it's gone

    def _open_location(self, pid: int, name: str) -> None:
        import subprocess

        import psutil as _psutil

        try:
            exe = _psutil.Process(pid).exe()
        except Exception:
            exe = ""
        if not exe:
            self._set_status(f"no exe path available for PID {pid}")
            return
        subprocess.Popen(["explorer", "/select,", exe])

    # ── good-citizen buttons ───────────────────────────────────────────────
    def _trim_now(self) -> None:
        """Trigger EmptyWorkingSet on Pulse itself and report the outcome.

        Trim works (kernel frees cold pages), but Windows may refuse to
        release below the process's true demand — so we measure RSS before
        vs after: a real drop says how much was handed back, while a flat
        result means this IS about as small as the process gets right now.
        """
        import os
        import threading

        def _work() -> None:
            import psutil

            from pulse_hwm.alerts.notifier import send_toast

            me = psutil.Process(os.getpid())
            before = me.memory_info().rss
            ok = trim_working_set()
            after = me.memory_info().rss
            freed = before - after
            if not ok:
                message = "TRIM NOT AVAILABLE ON THIS SYSTEM"
                title = "TRIM MEMORY"
            elif freed < 1_000_000:  # under 1MB: no pages came loose
                message = "ALREADY AT MINIMUM — THIS IS THE LEAST MEMORY IT CAN USE"
                title = "TRIM MEMORY"
            else:
                message = f"FREED {freed / 1_000_000:.1f} MB"
                title = "TRIM MEMORY"
            self._set_status(message)
            try:
                send_toast(title, message)
            except Exception:
                pass  # toast is garnish; the status line still reports it

        # kernel call is quick but never freeze the UI thread on it
        threading.Thread(target=_work, daemon=True).start()

    def _toggle_priority(self, enabled: bool) -> None:
        ok = set_low_priority_mode(bool(enabled))
        self._set_status(
            f"low priority {'ON' if enabled else 'OFF'}"
            + ("" if ok else " (not granted by the OS)")
        )
        if not ok:
            self.prio_btn.blockSignals(True)
            self.prio_btn.setChecked(False)
            self.prio_btn.blockSignals(False)
            return
        # same preference as Settings' LIMIT PULSE RESOURCES: persist it so it
        # survives a restart, then let the Settings tab mirror the control
        if self._db is not None:
            app_settings.save_field(self._db, "limit_resources", bool(enabled))
        self.priority_changed.emit(bool(enabled))

    def set_low_priority_state(self, on: bool) -> None:
        """Mirror a change that came from the Settings checkbox (already
        applied + persisted there). Blocked so it can't re-enter
        _toggle_priority and double-save."""
        self.prio_btn.blockSignals(True)
        self.prio_btn.setChecked(bool(on))
        self.prio_btn.blockSignals(False)

    def _set_status(self, text: str) -> None:
        self.status.setText(text)
