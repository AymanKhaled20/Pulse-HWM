"""Effect import validation + store tests — pure logic, temp SQLite."""

from __future__ import annotations

from pathlib import Path

import pytest

from pulse_hwm.db import Database
from pulse_hwm.rgb.effects.catalog import EffectCatalog
from pulse_hwm.rgb.effects.loader import UserEffectStore, validate_definition

GOOD = {
    "id": "my-effect",
    "name": "MY EFFECT",
    "layers": [{"type": "solid", "color": "#101010"}],
}


class TestValidateDefinition:
    def test_valid_definition_gets_user_prefix(self):
        definition, errors = validate_definition(GOOD)
        assert definition is not None and errors == []
        assert definition["id"] == "user_my-effect"

    def test_keeps_existing_user_prefix(self):
        definition, _ = validate_definition({**GOOD, "id": "user_x"})
        assert definition["id"] == "user_x"

    def test_invalid_json_string(self):
        result, errors = validate_definition("{not json")
        assert result is None and errors

    def test_non_object(self):
        assert validate_definition("[1]")[0] is None

    def test_missing_id_name_layers(self):
        result, errors = validate_definition({})
        assert result is None
        assert any("id" in error for error in errors)
        assert any("name" in error for error in errors)
        assert any("layers" in error for error in errors)

    def test_unknown_layer_type_rejected(self):
        definition = {**GOOD, "layers": [{"type": "nuclear"}]}
        assert validate_definition(definition)[0] is None

    def test_too_many_layers_rejected(self):
        layers = [{"type": "solid", "color": "#101010"}] * 41
        assert validate_definition({**GOOD, "layers": layers})[0] is None

    def test_oversized_string_rejected(self):
        huge = "#" * 21_000
        assert validate_definition(huge)[0] is None


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "loader-test.db")


class TestUserEffectStore:
    def test_add_lists_and_dedypes(self, db):

        store = UserEffectStore(db)
        definition, _ = validate_definition(GOOD)
        ok, _ = store.add(definition)
        assert ok is True
        ok, reason = store.add(definition)
        assert ok is False and "already exists" in reason
        assert len(store.list()) == 1

    def test_remove(self, db):

        store = UserEffectStore(db)
        definition, _ = validate_definition(GOOD)
        store.add(definition)
        store.remove("user_my-effect")
        assert store.list() == []

    def test_register_with_catalog_replays(self, db):

        store = UserEffectStore(db)
        definition, _ = validate_definition(GOOD)
        store.add(definition)
        catalog = EffectCatalog()
        registered = store.register_with_catalog(catalog)
        assert registered == ["user_my-effect"]
        assert catalog.get("user_my-effect") is not None

    def test_corrupt_blob_degrades_to_empty(self, db):

        db.set_setting("rgb_user_effects", "nope")
        assert UserEffectStore(db).list() == []

    def test_registered_effect_renders(self, db):
        from pulse_hwm.rgb.effects.base import EffectContext
        from pulse_hwm.rgb.model import LedLayout, RgbColor, RgbDevice

        store = UserEffectStore(db)
        definition, _ = validate_definition(GOOD)
        store.add(definition)
        catalog = EffectCatalog()
        store.register_with_catalog(catalog)
        device = RgbDevice(
            device_id="d",
            name="D",
            driver_id="fake",
            leds=4,
            layout=LedLayout(led_count=4),
        )
        frame = catalog.get("user_my-effect").render(EffectContext(device=device))
        assert len(frame) == 4
        assert all(c == RgbColor(0x10, 0x10, 0x10) for c in frame)
