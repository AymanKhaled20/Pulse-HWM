"""Effect catalog: registry + parameter validation.

The catalog is the single lookup for effect ids → implementations, used by
the engine, the future RGB tab, and the declarative JSON importer (phase
12). Unknown ids resolve to None (callers degrade), never raise.
"""

from __future__ import annotations

from pulse_hwm.rgb.effects.base import Effect, EffectContext, ParamSpec
from pulse_hwm.rgb.effects.builtin import BUILTIN_EFFECTS
from pulse_hwm.rgb.model import RgbEffect

# effects/builtin.py imports names from base.py for re-export convenience;
# keep the public surface importable from the package root too.
__all__ = ["Effect", "EffectContext", "ParamSpec", "EffectCatalog"]


class EffectCatalog:
    """Holds built-in effects; imported effects register here in phase 12."""

    def __init__(self) -> None:
        self._effects: dict[str, Effect] = {}
        for effect in BUILTIN_EFFECTS:
            self.register(effect)

    def register(self, effect: Effect) -> None:
        # last registration wins: a re-register (e.g. reload of an imported
        # effect) intentionally replaces the old definition
        self._effects[effect.effect_id] = effect

    def get(self, effect_id: str) -> Effect | None:
        return self._effects.get(effect_id)

    def ids(self) -> list[str]:
        return sorted(self._effects)

    def all(self) -> list[Effect]:
        return [self._effects[eid] for eid in self.ids()]

    def validate_params(
        self, effect_id: str, params: dict[str, float | int | str]
    ) -> dict[str, float | int | str]:
        """Clamp/coerce a raw params dict against the effect's ParamSpecs.
        Unknown keys are dropped — they can't be rendered, and carrying them
        forward would make stored JSON look valid while doing nothing."""
        effect = self.get(effect_id)
        if effect is None:
            return {}
        return {
            spec.key: spec.validate(params.get(spec.key, spec.default))
            for spec in effect.params
        }

    def build_request(
        self, effect_id: str, params: dict[str, float | int | str] | None = None
    ) -> RgbEffect | None:
        """Settings/UI → validated RgbEffect (the engine's input type).
        Returns None for unknown effect ids."""
        effect = self.get(effect_id)
        if effect is None:
            return None
        safe = self.validate_params(effect_id, params or {})
        return RgbEffect(mode=effect_id, params=dict(safe))
