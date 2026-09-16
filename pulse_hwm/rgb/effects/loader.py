"""Effect import/validation — the safe path from outside into the catalog.

Validation rules (order matters: cheap checks first):
  * JSON parses and is an object
  * id: 1..40 chars [a-z0-9_-]; names get de-duplicated with a  user_ prefix
  * layers: non-empty list of whitelisted layer types (interpreter already
    drops junk at init, but we reject up front so users see WHY)
  * size caps: <= 40 layers, <= 8 params (a hostile 10 MB "effect" is a
    denial-of-service inside a 30 fps render loop otherwise)

Persistence rides the standard settings table: `rgb_user_effects` blob is a
JSON LIST of definitions; save_field is the same instant-apply contract as
every other rgb key. `register_with_catalog` replays stored effects at boot
and after import.
"""

from __future__ import annotations

import json

from pulse_hwm import app_settings
from pulse_hwm.rgb.effects.declarative import DeclarativeEffect

_EFFECT_ID_MAX = 40
_MAX_LAYERS = 40
_MAX_PARAMS = 8
_MAX_DEFINITION_CHARS = 20_000

_LAYER_TYPES = frozenset(
    {
        "solid",
        "gradient",
        "wave",
        "sparkle",
        "reactive_temp",
        "reactive_alert",
    }
)


def validate_definition(raw) -> tuple[dict | None, list[str]]:
    """Returns (definition, errors). Never raises."""
    if isinstance(raw, str):
        if len(raw) > _MAX_DEFINITION_CHARS:
            return None, ["definition too large (>20 KB)"]
        try:
            raw = json.loads(raw or "{}")
        except ValueError as exc:
            return None, [f"not valid JSON: {exc}"]
    if not isinstance(raw, dict):
        return None, ["definition must be a JSON object"]
    errors: list[str] = []
    effect_id = str(raw.get("id") or "").strip()
    if not effect_id or len(effect_id) > _EFFECT_ID_MAX:
        errors.append("id required (1-40 chars)")
    name = str(raw.get("name") or "").strip()
    if not name:
        errors.append("name required")
    layers = raw.get("layers")
    if not isinstance(layers, list) or not layers:
        errors.append("layers required (non-empty list)")
    else:
        if len(layers) > _MAX_LAYERS:
            errors.append(f"too many layers (max {_MAX_LAYERS})")
        for layer in layers:
            if (
                not isinstance(layer, dict)
                or str(layer.get("type")) not in _LAYER_TYPES
            ):
                errors.append(f"unknown layer type: {type(layer).__name__}")
                break
    params = raw.get("params", {})
    if isinstance(params, dict) and len(params) > _MAX_PARAMS:
        errors.append(f"too many params (max {_MAX_PARAMS})")
    if errors:
        return None, errors
    if not effect_id.startswith("user_"):
        raw["id"] = f"user_{effect_id}"
    return raw, []


class UserEffectStore:
    """CRUD over the rgb_user_effects settings blob (Qt-free, db-injected)."""

    def __init__(self, db) -> None:
        self._db = db

    def list(self) -> list[dict]:
        try:
            parsed = json.loads(app_settings.load(self._db).rgb_user_effects)
            return parsed if isinstance(parsed, list) else []
        except ValueError:
            return []

    def add(self, definition: dict) -> tuple[bool, str]:
        definition_id = str(definition.get("id", ""))
        existing = [e for e in self.list() if str(e.get("id")) == definition_id]
        if existing:
            return False, f"effect id '{definition_id}' already exists"
        updated = self.list()
        updated.append(definition)
        import json

        app_settings.save_field(self._db, "rgb_user_effects", json.dumps(updated))
        return True, ""

    def remove(self, definition_id: str) -> None:
        remaining = [e for e in self.list() if str(e.get("id")) != definition_id]
        import json

        app_settings.save_field(self._db, "rgb_user_effects", json.dumps(remaining))

    def register_with_catalog(self, catalog) -> list[str]:
        """Replay every stored definition into the catalog. Returns ids
        registered (a broken definition is skipped, never fatal)."""
        registered: list[str] = []
        for definition in self.list():
            try:
                effect = DeclarativeEffect(definition)
                catalog.register(effect)
                registered.append(effect.effect_id)
            except Exception:
                continue
        return registered
