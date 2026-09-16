"""ModePlanner + AlertClock tests — pure logic, injected clock, FakeDriver
device ids. No Qt, no DB, no I/O."""

from __future__ import annotations

from pulse_hwm.rgb.effects.catalog import EffectCatalog
from pulse_hwm.rgb.engine import DeviceAssignment
from pulse_hwm.rgb.manager import (
    ALERT_PARAM_COLOR,
    AlertClock,
    ModePlanner,
    parse_assignment_blob,
)

DEVICES = ["fake:0", "fake:1"]


def make_planner(mode: str, alert_active: bool = False, **kwargs) -> ModePlanner:
    return ModePlanner(
        mode=mode,
        driver_id="fake",
        device_ids=DEVICES,
        catalog=EffectCatalog(),
        override_effect_id=kwargs.get("override_effect_id", "breathe"),
        override_color=kwargs.get("override_color", "#123456"),
        alert_active=alert_active,
        alert_color=kwargs.get("alert_color", "#FF3B30"),
        **{key: value for key, value in kwargs.items() if key.startswith("reactive")},
    )


class TestOffMode:
    def test_clears_all_devices(self):
        plan = make_planner("off").plan()
        assert plan.assignments == {"fake:0": None, "fake:1": None}

    def test_unknown_mode_degrades_to_off(self):
        plan = make_planner("chaos").plan()
        assert all(value is None for value in plan.assignments.values())


class TestOverrideMode:
    def test_forces_effect_on_every_device(self):
        plan = make_planner(
            "override", override_effect_id="static", override_color="#00FF41"
        ).plan()
        for device_id in DEVICES:
            assignment = plan.assignments[device_id]
            assert assignment is not None
            assert assignment.effect_id == "static"
            assert assignment.params["color"] == "#00FF41"

    def test_unknown_override_effect_clears_everything(self):
        # refuse to take control on an unknown effect: guessing would fight
        # vendor software with garbage
        plan = make_planner("override")
        plan.override_effect_id = "vanished"
        assert all(value is None for value in plan.plan().assignments.values())

    def test_params_validated_against_spec(self):
        planner = make_planner("override", override_effect_id="breathe")
        plan = planner.plan()
        assignment = plan.assignments["fake:0"]
        assert assignment.params["color"] == "#123456"

    def test_plan_equality_detects_changes(self):
        planner = make_planner("override")
        first = planner.plan()
        second = planner.plan()
        assert first.is_noop(second)
        assert not first.is_noop(None)

    def test_color_change_means_new_plan(self):
        planner = make_planner("override", override_color="#000000")
        first = planner.plan()
        planner.override_color = "#FFFFFF"
        assert not first.is_noop(planner.plan())


class TestReactiveMode:
    def test_temp_effect_all_devices(self):
        plan = make_planner("reactive", reactive_effect_id="static").plan()
        assert all(plan.assignments[d].effect_id == "static" for d in DEVICES)

    def test_alert_replaces_temp_effect(self):
        plan = make_planner(
            "reactive", reactive_effect_id="static", alert_active=True
        ).plan()

        # the alert effect is not registered in this catalog → empty plan
        # (safe degrade until phase 16 registers it)
        assert plan.assignments == {}

    def test_alert_effect_applies_when_registered(self):
        from pulse_hwm.rgb.effects.base import Effect, EffectContext, ParamSpec
        from pulse_hwm.rgb.manager import REACTIVE_ALERT_EFFECT_ID

        class FakeAlertEffect(Effect):
            effect_id = REACTIVE_ALERT_EFFECT_ID
            name = "FAKE ALERT"
            # real phase-16 effect declares the same color spec
            params = (ParamSpec("color", "COLOR", "color", "#FF3B30"),)

            def render(self, ctx: EffectContext) -> list:
                return []

        catalog = EffectCatalog()
        catalog.register(FakeAlertEffect())
        planner = ModePlanner(
            mode="reactive",
            driver_id="fake",
            device_ids=DEVICES,
            catalog=catalog,
            override_effect_id="static",
            override_color="#000000",
            alert_active=True,
            alert_color="#110022",
        )
        plan = planner.plan()
        for device_id in DEVICES:
            assignment = plan.assignments[device_id]
            assert assignment.effect_id == REACTIVE_ALERT_EFFECT_ID
            assert assignment.params[ALERT_PARAM_COLOR] == "#110022"

    def test_unknown_reactive_effect_is_empty_not_off(self):
        # empty plan ("don't touch") is different from off-in-override:
        # reacting must NOT clear devices that vendor software may be driving
        plan = make_planner("reactive", reactive_effect_id="nope").plan()
        assert plan.assignments == {}


class TestEffectsMode:
    def test_assignments_from_loader(self):
        plan = make_planner("effects").plan(
            assignment_loader=lambda device_id: (
                DeviceAssignment("static") if device_id == "fake:0" else None
            )
        )
        assert plan.assignments["fake:0"].effect_id == "static"
        assert plan.assignments["fake:1"] is None


class TestAssignmentBlobParsing:
    def test_valid_entry(self):
        blob = '{"fake/fake:0": {"effect": "static", "params": {"color": "#AB0102"}}}'
        assignment = parse_assignment_blob(blob, EffectCatalog(), "fake", "fake:0")
        assert assignment is not None
        assert assignment.effect_id == "static"
        assert assignment.params["color"] == "#AB0102"
        assert assignment.enabled is True

    def test_missing_entry_is_none(self):
        assert parse_assignment_blob("{}", EffectCatalog(), "fake", "fake:0") is None

    def test_corrupt_json_is_none(self):
        assert parse_assignment_blob("nope", EffectCatalog(), "fake", "fake:0") is None

    def test_non_dict_blob_is_none(self):
        assert (
            parse_assignment_blob("[1, 2]", EffectCatalog(), "fake", "fake:0") is None
        )

    def test_unknown_effect_id_is_none(self):
        blob = '{"fake/fake:0": {"effect": "vanished"}}'
        assert parse_assignment_blob(blob, EffectCatalog(), "fake", "fake:0") is None

    def test_disabled_flag(self):
        blob = '{"fake/fake:0": {"effect": "static", "enabled": false}}'
        assignment = parse_assignment_blob(blob, EffectCatalog(), "fake", "fake:0")
        assert assignment is not None
        assert assignment.enabled is False

    def test_bad_params_dropped(self):
        blob = (
            '{"fake/fake:0": {"effect": "static", "params": {"color": "x", "junk": 1}}}'
        )
        assignment = parse_assignment_blob(blob, EffectCatalog(), "fake", "fake:0")
        assert assignment is not None
        assert "junk" not in assignment.params


class TestAlertClock:
    def test_inactive_before_trigger(self):
        clock = AlertClock(1000, clock=lambda: 5.0)
        assert clock.active() is False

    def test_active_within_hold(self):
        clock = AlertClock(1000, clock=lambda: 5.0)
        clock.trigger()
        assert clock.active() is True  # deadline 6.0, now 5.0

    def test_expires_after_hold(self):
        times = iter([5.0, 6.5])
        clock = AlertClock(1000, clock=lambda: next(times))
        clock.trigger()
        assert clock.active() is False  # re-trigger evaluated at 6.5

    def test_hold_ms_clamped(self):
        assert AlertClock(-5).hold_ms == 0
