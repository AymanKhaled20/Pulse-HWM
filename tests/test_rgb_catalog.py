"""EffectCatalog: registration, lookup, param validation, request building."""

from __future__ import annotations

from pulse_hwm.rgb.effects.base import Effect, EffectContext, ParamSpec
from pulse_hwm.rgb.effects.builtin import StaticEffect
from pulse_hwm.rgb.effects.catalog import EffectCatalog
from pulse_hwm.rgb.model import RgbDevice


class _FakeEffect(Effect):
    effect_id = "fake"
    name = "FAKE"
    description = "test-only"

    params = (ParamSpec("level", "LEVEL", "int", 5, 0, 10),)

    def render(self, ctx: EffectContext) -> list:
        return []


def make_catalog() -> EffectCatalog:
    catalog = EffectCatalog()
    catalog.register(_FakeEffect())
    return catalog


class TestCatalogLookup:
    def test_builtins_registered(self):
        catalog = EffectCatalog()
        assert "static" in catalog.ids()
        assert "breathe" in catalog.ids()

    def test_get_unknown_returns_none(self):
        assert EffectCatalog().get("nope") is None

    def test_register_replaces(self):
        catalog = EffectCatalog()
        catalog.register(StaticEffect())
        assert len([e for e in catalog.all() if e.effect_id == "static"]) == 1

    def test_ids_sorted(self):
        catalog = make_catalog()
        ids = catalog.ids()
        assert ids == sorted(ids)


class TestValidateParams:
    def test_clamps_and_drops_unknown_keys(self):
        catalog = make_catalog()
        safe = catalog.validate_params("fake", {"level": 99, "bogus": 1})
        assert safe == {"level": 10}

    def test_fills_defaults(self):
        catalog = make_catalog()
        assert catalog.validate_params("fake", {}) == {"level": 5}

    def test_unknown_effect_returns_empty(self):
        assert make_catalog().validate_params("nope", {"level": 1}) == {}


class TestBuildRequest:
    def test_builds_validated_request(self):
        catalog = make_catalog()
        request = catalog.build_request("fake", {"level": 99})
        assert request is not None
        assert request.mode == "fake"
        assert request.params == {"level": 10}

    def test_unknown_id_returns_none(self):
        assert make_catalog().build_request("nope") is None

    def test_built_request_feeds_render(self):
        catalog = EffectCatalog()
        device = RgbDevice(device_id="d", name="T", driver_id="fake", leds=4)
        request = catalog.build_request("static", {"color": "#123456"})
        assert request is not None
        ctx = EffectContext(device=device, params=dict(request.params))
        frame = catalog.get("static").render(ctx)
        assert all(c.r == 0x12 and c.g == 0x34 and c.b == 0x56 for c in frame)
