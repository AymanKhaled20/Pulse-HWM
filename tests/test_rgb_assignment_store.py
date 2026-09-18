"""assignment_store tests — pure CRUD on the blob JSON. No Qt, no I/O."""

from __future__ import annotations

from pulse_hwm.rgb.assignment_store import (
    assign,
    assignments_from_blob,
    clear,
    entry_of,
    parse_blob,
)


def test_empty_and_corrupt_blobs_are_empty():
    assert parse_blob("") == {}
    assert parse_blob("nope") == {}
    assert parse_blob("[1,2,3]") == {}


def test_parse_drops_non_dict_values():
    blob = '{"a": {"effect": "static"}, "junk": 5}'
    assert list(parse_blob(blob)) == ["a"]


def test_assign_writes_effect_and_enabled():
    blob = assign("", "fake", "fake:0", "static", True)
    entry = entry_of(blob, "fake", "fake:0")
    assert entry == {"effect": "static", "params": {}, "enabled": True}


def test_assign_preserves_existing_params():
    blob = '{"fake/fake:1": {"effect": "breathe", "params": {"speed": 0.8}, "enabled": true}}'
    blob = assign(blob, "fake", "fake:1", "rainbow", True)
    entry = entry_of(blob, "fake", "fake:1")
    assert entry["effect"] == "rainbow"
    assert entry["params"] == {"speed": 0.8}  # params survive a mode change


def test_clear_removes_only_target():
    blob = assign(
        assign("", "fake", "fake:0", "static", True), "fake", "fake:1", "breathe", True
    )
    blob = clear(blob, "fake", "fake:0")
    assert entry_of(blob, "fake", "fake:0") is None
    assert entry_of(blob, "fake", "fake:1") is not None


def test_assignments_from_blob_scopes_by_driver():
    blob = (
        '{"fake/fake:0": {"effect": "static"}, "other/other:0": {"effect": "breathe"}}'
    )
    result = assignments_from_blob(blob, None, "fake")
    assert list(result) == ["fake:0"]


def test_assignments_from_blob_needs_catalog():
    from pulse_hwm.rgb.effects.catalog import EffectCatalog

    blob = '{"fake/fake:0": {"effect": "static"}}'
    result = assignments_from_blob(blob, EffectCatalog(), "fake")
    assert result["fake:0"].effect_id == "static"


def test_assignments_from_blob_drops_unknown_effect_WITH_catalog():
    from pulse_hwm.rgb.effects.catalog import EffectCatalog

    blob = '{"fake/fake:0": {"effect": "vanished"}}'
    assert assignments_from_blob(blob, EffectCatalog(), "fake") == {}


class FakeCatalog:
    def get(self, effect_id: str):
        return object() if effect_id == "static" else None

    def validate_params(self, effect_id, params):
        return params


def test_round_trips_through_json_shapes():
    blob = ""
    blob = assign(blob, "fake", "d1", "static", False)
    blob = assign(blob, "fake", "d2", "breathe", True)
    assert entry_of(blob, "fake", "d1")["enabled"] is False
    assert entry_of(blob, "fake", "d2")["enabled"] is True
    blob = clear(blob, "fake", "d1")
    assert entry_of(blob, "fake", "d1") is None
    blob = clear(blob, "fake", "d2")
    assert parse_blob(blob) == {}
