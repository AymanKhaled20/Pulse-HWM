"""Declarative JSON effects — the shareable/importable effect format.

Effect definitions: {"id", "name", "params": {<name>: <ParamSpec-ish>},
"layers": [...]}. Each layer is one of:

  {"type":"solid","color":"#RRGGBB"}
  {"type":"gradient","direction":"horizontal|vertical",
   "stops":[["#RRGGBB",0.0],["#FF0000",1.0]]}
  {"type":"wave","speed":<0..1 %>}
  {"type":"sparkle","density":<0..1>}
  {"type":"reactive_temp","sensor":"cpu_temp",
   "range":[40,85],
   "stops":[["#00FF41",0.0],["#FF3B30",1.0]]}
  {"type":"reactive_alert","color":"#FF3B30"}

Rendering semantics: layers stack in order; solid/gradient paints the base,
wave scrolls the CURRENT base (horizontal sine over device distance),
sparkle modulates random LEDs, reactive_* override by sensor/alert state.
Time comes from ctx.now (engine clock) — the SAME deterministic base as the
built-in effects, so imported effects can be unit-tested.

Security: the interpreter only understands the ops above. Arbitrary code
never executes; a malformed layer is a VALIDATION error at import (loader)
and a hard skip at render time.
"""

from __future__ import annotations

import random

from pulse_hwm.rgb.effects.base import Effect, EffectContext, ParamSpec
from pulse_hwm.rgb.model import RgbColor

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


def _color_of(value) -> RgbColor:
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return RgbColor(int(value[0]), int(value[1]), int(value[2]))
    return RgbColor.from_hex(str(value or ""))


def _stops_of(raw) -> list[tuple[float, RgbColor]]:
    stops: list[tuple[float, RgbColor]] = []
    if isinstance(raw, list):
        for entry in raw:
            if (
                isinstance(entry, (list, tuple))
                and len(entry) == 2
                and isinstance(entry[1], (int, float))
            ):
                stops.append((max(0.0, min(1.0, float(entry[1]))), _color_of(entry[0])))
    stops.sort(key=lambda item: item[0])
    return stops or [(0.0, RgbColor()), (1.0, RgbColor(255, 212, 0))]


def _geometry(ctx: EffectContext) -> tuple[list, tuple[int, int] | None]:
    """(positions, matrix) from the device layout; synthetic row layout as
    the fallback so effects never crash unknown devices."""
    device = ctx.device
    layout = device.layout
    positions = list(layout.positions) if layout is not None else []
    if layout is not None:
        matrix = layout.matrix
    else:
        matrix = None
    return (positions, matrix)


def _sample_stops(stops: list[tuple[float, RgbColor]], position: float) -> RgbColor:
    """Piecewise-linear interpolation between sorted stops."""
    if position <= stops[0][0]:
        return stops[0][1]
    if position >= stops[-1][0]:
        return stops[-1][1]
    for index in range(len(stops) - 1):
        left = stops[index]
        right = stops[index + 1]
        if left[0] <= position <= right[0]:
            span = max(1e-9, right[0] - left[0])
            t = (position - left[0]) / span
            lr, lg, lb = left[1].r, left[1].g, left[1].b
            rr, rg, rb = right[1].r, right[1].g, right[1].b
            return RgbColor(
                round(lr + (rr - lr) * t),
                round(lg + (rg - lg) * t),
                round(lb + (rb - lb) * t),
            )
    return stops[-1][1]


def parse_params_schema(raw) -> tuple[ParamSpec, ...]:
    """Declarative params → validated ParamSpec tuple. Unknown kinds drop —
    the effect renders with defaults, never crashes on a bad import."""
    specs: list[ParamSpec] = []
    if not isinstance(raw, dict):
        return ()
    for key, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        kind = str(spec.get("type", "float"))
        if kind not in ("float", "int", "choice", "color"):
            continue
        label = str(spec.get("label") or key or "PARAM").upper()
        default = spec.get("default", 0.5 if kind == "float" else 0)
        minimum = float(spec.get("min", 0.0))
        maximum = float(spec.get("max", 1.0 if kind == "float" else 100))
        choices = tuple(str(c) for c in spec.get("choices", ()))
        specs.append(
            ParamSpec(
                key=str(key),
                label=label,
                kind=kind,
                default=default,
                minimum=minimum,
                maximum=maximum,
                choices=choices,
            )
        )
    return tuple(specs)


class DeclarativeEffect(Effect):
    """Interpreter for one effect definition."""

    def __init__(self, definition: dict) -> None:
        definition = definition if isinstance(definition, dict) else {}
        self._definition = definition
        self._layers: list[dict] = [
            layer
            for layer in (definition.get("layers") or [])
            if isinstance(layer, dict) and str(layer.get("type")) in _LAYER_TYPES
        ]
        self.effect_id = str(definition.get("id") or "unnamed")
        self.name = str(definition.get("name") or self.effect_id).upper()
        self.description = str(definition.get("description") or "")
        self.params = parse_params_schema(definition.get("params"))

    def render(self, ctx: EffectContext) -> list[RgbColor]:
        size = self.frame_size(ctx)
        frame: list[RgbColor | None] = [None] * size
        for layer in self._layers:
            painted = self._render_layer(layer, ctx, size)
            for index in range(size):
                if painted[index] is not None:
                    frame[index] = painted[index]
        return [color or RgbColor(0, 0, 0) for color in frame]

    def _render_layer(self, layer: dict, ctx: EffectContext, size: int) -> list:
        kind = str(layer.get("type"))
        if kind == "solid":
            color = _color_of(layer.get("color"))
            return [color] * size
        if kind == "gradient":
            return self._gradient_frame(layer, ctx, size)
        if kind == "wave":
            return self._wave_frame(layer, ctx, size)
        if kind == "sparkle":
            return self._sparkle_frame(layer, ctx, size)
        if kind == "reactive_temp":
            return self._reactive_temp_frame(layer, ctx, size)
        if kind == "reactive_alert":
            if ctx.last_alert is None:
                return [None] * size  # transparent: keep whatever is below
            return [_color_of(layer.get("color"))] * size
        return [None] * size  # unreachable (validated at init) — safe skip

    def _gradient_frame(self, layer: dict, ctx: EffectContext, size: int) -> list:
        stops = _stops_of(layer.get("stops"))
        vertical = str(layer.get("direction")) == "vertical"
        positions, _ = _geometry(ctx)
        if not positions:
            positions = [((i + 0.5) / size, 0.5) for i in range(size)]
        frame: list[RgbColor | None] = []
        for index in range(size):
            x, y = positions[index] if index < len(positions) else (0.5, 0.5)
            position = y if vertical else x
            frame.append(_sample_stops(stops, float(position)))
        return frame

    def _wave_frame(self, layer: dict, ctx: EffectContext, size: int) -> list:
        # wave = gradient scrolled by time: phase = now * speed
        speed = max(0.0, min(1.0, float(layer.get("speed", 0.5))))
        positions, _ = _geometry(ctx)
        if not positions:
            positions = [((i + 0.5) / size, 0.5) for i in range(size)]
        stops = _stops_of(
            layer.get("stops", [["#FFD400", 0.0], ["#00FF41", 0.5], ["#FF3B30", 1.0]])
        )
        phase = (ctx.now * (0.5 + speed) * 2.0) % 1.0
        frame = []
        for index in range(size):
            position = (
                float(positions[index][1])
                if str(layer.get("direction")) == "vertical"
                else float(positions[index][0])
            )
            sample = _sample_stops(stops, (position + phase) % 1.0)
            frame.append(sample)
        while len(frame) < size:
            frame.append(None)
        return frame[:size]

    def _sparkle_frame(self, layer: dict, ctx: EffectContext, size: int) -> list:
        # deterministic per-tick refresh: the same (now, index) always gives
        # the same sparkle, so testing can step time exactly like BREATHE
        density = max(0.0, min(1.0, float(layer.get("density", 0.15))))
        seed = int(ctx.now * 8.0)  # 8 Hz sparkle refresh
        rng = random.Random(seed)
        color = _color_of(layer.get("color", "#FFFFFF"))
        frame: list[RgbColor | None] = []
        for _ in range(size):
            frame.append(color if rng.random() < density else None)
        return frame

    def _reactive_temp_frame(self, layer, ctx, size) -> list:
        sensor = str(layer.get("sensor", "cpu_temp"))
        raw_range = layer.get("range") or [40, 85]
        if (
            isinstance(raw_range, list)
            and len(raw_range) == 2
            and all(isinstance(v, (int, float)) for v in raw_range)
        ):
            low, high = float(raw_range[0]), float(raw_range[1])
        else:
            low, high = 40.0, 85.0
        value = ctx.sensor(sensor)
        if value is None:
            return [None] * size  # sensor missing → transparent
        position = max(0.0, min(1.0, (value - low) / max(1e-9, high - low)))
        stops = _stops_of(layer.get("stops"))
        return [_sample_stops(stops, position)] * size
