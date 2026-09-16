"""Phase 16 closers: ReactiveAlertEffect rendering, alert listener hook on
AlertManager, manager maintain()-expiry sweep."""

from __future__ import annotations

from pathlib import Path

from pulse_hwm.alerts.notifier import AlertManager
from pulse_hwm.db import Database
from pulse_hwm.rgb.effects.base import EffectContext
from pulse_hwm.rgb.effects.builtin import ReactiveAlertEffect
from pulse_hwm.rgb.manager import RgbManager
from pulse_hwm.rgb.model import LedLayout, RgbDevice


def device():
    return RgbDevice(
        device_id="d",
        name="D",
        driver_id="fake",
        leds=4,
        layout=LedLayout(led_count=4),
    )


def test_alert_frame_strobes():
    effect = ReactiveAlertEffect()
    params = {"color": "#FF0000"}
    t0 = effect.render(EffectContext(device=device(), params=params))[0]
    t1 = effect.render(EffectContext(device=device(), params=params, now=0.25))[0]
    assert t0 != t1  # 4 Hz strobe: brightness differs between ticks


def test_alert_default_color():
    effect = ReactiveAlertEffect()
    frame = effect.render(EffectContext(device=device()))
    assert len(frame) == 4
    assert all(c.r > 0 for c in frame)  # red by default, never black


class TestDispatchListener:
    def test_error_level_calls_listeners(self):
        manager = AlertManager(None)
        calls = []
        manager.add_dispatch_listener(lambda level, title: calls.append((level, title)))
        manager.notify("error", "SITE DOWN", "unreachable")
        assert calls == [("error", "SITE DOWN")]

    def test_info_level_does_not_call(self):
        manager = AlertManager(None)
        calls = []
        manager.add_dispatch_listener(lambda level, title: calls.append(level))
        manager.notify("info", "SITE RECOVERED", "back up")
        assert calls == []

    def test_broken_listener_is_isolated(self):
        manager = AlertManager(None)
        healthy = []
        manager.add_dispatch_listener(lambda level, title: 1 / 0)
        manager.add_dispatch_listener(lambda level, title: healthy.append(level))
        manager.notify("error", "X", "y")
        assert healthy == ["error"]  # second listener still ran


class TestMaintainExpiry:
    def test_expiry_triggers_replan_once(self, tmp_path: Path):
        db = Database(tmp_path / "maintain.db")
        db.set_setting("rgb_mode", "reactive")
        clock_state = {"now": 0.0}
        manager = RgbManager(db, alert_hold_ms=1000)
        manager._alert_clock._clock = lambda: clock_state["now"]
        pushes: list = []
        manager.apply_assignments = lambda a: pushes.append(dict(a))
        manager.set_driver("fake", ["fake:0"])

        manager.reconsider()  # initial plan
        manager.handle_alert()  # alert → flash plan pushed
        assert any(
            assignment is not None and assignment.effect_id == "reactive_alert"
            for assignments in pushes
            for assignment in assignments.values()
        )
        clock_state["now"] = 0.5  # still held → no change
        pushes.clear()
        manager.maintain()
        assert pushes == []
        clock_state["now"] = 2.0  # expired
        manager.maintain()
        assert pushes and pushes[-1]["fake:0"].effect_id == "reactive_temp"
        pushes.clear()
        manager.maintain()  # idempotent after expiry
        assert pushes == []
        db.close()
