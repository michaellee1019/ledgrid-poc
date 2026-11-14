"""Cribbage scores for the LED wall.

The sixth ESP32 is SPI0 CE2. The host sends it SetRole, and that board's
MISO replies carry the score snapshot. Each player is one short row: two
pixels of that player's color, the score in one-pixel strokes, then two more
pixels of color. The next player starts on the next row.
"""

from __future__ import annotations

import math
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from animation import AnimationBase, RenderedFrame
from animation.core.component_catalog import ComponentDescriptor
from animation.core.presentation_contracts import ResolvedScene, TimingAdapter
from animation.libraries.clock_face import ClockFaceRenderer
from animation.plugins.cribbage_leaderboard.state import (
    PLAYER_COLORS,
    PLAYER_ROLES,
    LeaderboardState,
    MemoryLeaderboardSource,
    SerialLeaderboardSource,
)
from drivers.wall_roles import SpiLeaderboardSource


_FONT = ClockFaceRenderer.FONT
_ROW_PAD = 2
_GLYPH_HEIGHT = 5
_ROW_HEIGHT = _ROW_PAD + _GLYPH_HEIGHT + _ROW_PAD


def _text_width(text: str) -> int:
    if not text:
        return 0
    return len(text) * 4 - 1


def _blit(view: np.ndarray, x: int, y: int, text: str, color: Sequence[int]) -> None:
    strips, leds, _channels = view.shape
    cursor = x
    ink = np.array(color, dtype=np.uint8)
    for character in text:
        rows = _FONT.get(character, _FONT[" "])
        for row_index, bits in enumerate(rows):
            for column in range(3):
                if bits & (1 << (2 - column)):
                    px = cursor + column
                    py = y + row_index
                    if 0 <= px < strips and 0 <= py < leds:
                        view[px, py] = ink
        cursor += 4


def _centered(width: int, text: str) -> int:
    return max(0, (width - _text_width(text)) // 2)


def _pulse(elapsed: float, hz: float) -> float:
    if hz <= 0:
        return 1.0
    return 0.5 + 0.5 * math.sin(max(0.0, elapsed) * math.tau * hz)


def _ink(color: Sequence[int], level: float) -> tuple[int, int, int]:
    luminance = (0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]) * level
    return (16, 16, 16) if luminance >= 140 else (255, 255, 255)


def _band_level(state: Optional[LeaderboardState], index: int, pulse: float) -> float:
    if state is None:
        return 0.12 + 0.06 * pulse
    bit = 1 << index
    active = state.started and state.turn == PLAYER_ROLES[index]
    if state.started and (state.roster & bit) == 0:
        return 0.08
    if active:
        return 0.45 + 0.55 * pulse
    if not state.started and (state.connected & bit) == 0:
        return 0.2
    return 0.36


def _score_text(state: Optional[LeaderboardState], index: int) -> str:
    if state is None:
        return "----"
    bit = 1 << index
    if state.started and (state.roster & bit) == 0:
        return ""
    return str(state.scores[index])


def _sample_game(elapsed: float) -> LeaderboardState:
    """A fixed game used to review the layout when no board is connected.

    The glowing turn moves to the next player every four seconds.
    """

    turn = PLAYER_ROLES[int(max(0.0, elapsed) // 4) % len(PLAYER_ROLES)]
    return LeaderboardState(
        started=True,
        turn=turn,
        scores=(31, 47, 18, 62),
        connected=0x0F,
        roster=0x0F,
        term=1,
        version=1,
    )


class CribbageLeaderboardAnimation(AnimationBase):
    """Stacked player rows. The current turn breathes brighter."""

    ANIMATION_NAME = "Cribbage Leaderboard"
    ANIMATION_DESCRIPTION = "Player colors and scores from the cribbage boards. The current turn glows."
    ANIMATION_AUTHOR = "LED Grid Team"
    ANIMATION_VERSION = "1.0"
    COMPONENT_ID = "cribbage_leaderboard"
    COMPONENT_VERSION = 1
    PROVIDER = "python"
    ROLE = "animation"
    FRAME_FORMAT = "rgb_uint8_strip_major"
    TIMING_POLICY = "scaled_context"
    PALETTE_POLICY = "preserve"
    TIMING_ADAPTER = TimingAdapter.SCALED_CONTEXT
    VIBE_COLOR_POLICY = "preserve"
    CAPABILITIES = frozenset(("scaled_context",))
    PLANT_MODIFIER_SUPPORT = frozenset()
    DEFAULTS = MappingProxyType({"glow": 0.7, "serial_port": ""})
    COMPONENT_DESCRIPTOR = ComponentDescriptor(
        component_id=COMPONENT_ID,
        version=COMPONENT_VERSION,
        provider=PROVIDER,
        role=ROLE,
        timing_policy=TIMING_POLICY,
        alpha_behavior="opaque",
        palette_policy=PALETTE_POLICY,
        plant_capabilities=("none",),
        fidelity_exceptions=("authored_media_colors",),
        defaults=DEFAULTS,
        parameter_normalizer=lambda values: CribbageLeaderboardAnimation._normalized_parameters(values),
    )

    def __init__(
        self,
        controller: Any,
        config: Mapping[str, Any] | None = None,
        source: Any = None,
    ) -> None:
        authored = dict(config or {})
        super().__init__(controller, authored)
        self.default_params = dict(self.DEFAULTS)
        self.params = self._normalized_parameters(authored)
        self._owns_source = source is None
        self._source = source if source is not None else MemoryLeaderboardSource(None)
        self._serial: Optional[SerialLeaderboardSource] = None
        self._spi: Optional[SpiLeaderboardSource] = None
        self._bound_port: Optional[str] = None
        self._context: ResolvedScene | None = None
        strips = int(self.controller.strip_count)
        leds = int(self.controller.leds_per_strip)
        self._pixels = np.zeros((strips * leds, 3), dtype=np.uint8)
        self._last_key: tuple[Any, ...] | None = None
        if self._owns_source:
            self._bind_port(self._hardware_port(self.params))

    @classmethod
    def component_descriptor(cls) -> ComponentDescriptor:
        return cls.COMPONENT_DESCRIPTOR

    @classmethod
    def _normalized_parameters(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        supplied = dict(values)
        unknown = sorted(set(supplied) - set(cls.DEFAULTS))
        if unknown:
            raise ValueError(f"Cribbage Leaderboard does not accept non-local parameters: {unknown!r}")
        result = dict(cls.DEFAULTS)
        result.update(supplied)
        glow = result["glow"]
        if isinstance(glow, bool) or not isinstance(glow, (int, float)) or not math.isfinite(float(glow)) or not 0.0 <= float(glow) <= 3.0:
            raise ValueError("glow must be a finite number from 0 to 3")
        result["glow"] = float(glow)
        port = result["serial_port"]
        if not isinstance(port, str) or "\n" in port or "\x00" in port or len(port) > 160:
            raise ValueError("serial_port must be a short single-line path")
        result["serial_port"] = port.strip()
        return result

    @classmethod
    def get_parameter_schema(cls) -> dict[str, dict[str, Any]]:
        return {
            "glow": {"type": "float", "min": 0.0, "max": 3.0, "default": 0.7, "description": "How fast the current player's color breathes"},
            "serial_port": {"type": "str", "default": "", "description": "Blank reads the score board on SPI0 CE2. 'preview' shows a sample game."},
        }

    def update_parameters(self, values: Mapping[str, Any]) -> None:
        self.params = self._normalized_parameters({**self.params, **dict(values)})
        self._last_key = None
        if self._owns_source:
            self._bind_port(self._hardware_port(self.params))

    def on_presentation_context_changed(self, old: ResolvedScene | None, new: ResolvedScene) -> None:
        del old
        if new.descriptor.component_id != self.COMPONENT_ID:
            raise ValueError("Cribbage Leaderboard received another component context")
        self._context = new

    def set_presentation_context(self, context: ResolvedScene) -> None:
        self.on_presentation_context_changed(self._context, context)

    def render_resolved_scene(self, context: ResolvedScene) -> RenderedFrame:
        self.set_presentation_context(context)
        return self.generate_frame(context.phase_time, self.frame_count)

    def _configured_port(self, params: Mapping[str, Any]) -> str:
        return str(params.get("serial_port") or "").strip()

    def _hardware_port(self, params: Mapping[str, Any]) -> Optional[str]:
        port = self._configured_port(params)
        if port == "preview" or not hasattr(self.controller, "device_map"):
            return None
        return port

    def _close_sources(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None
        if self._spi is not None:
            self._spi.close()
            self._spi = None

    def cleanup(self) -> None:
        self._close_sources()
        self._bound_port = None
        super().cleanup()

    def _bind_port(self, port: Optional[str]) -> None:
        if port == self._bound_port:
            return
        self._close_sources()
        self._bound_port = port
        if port is None:
            self._source = MemoryLeaderboardSource(None)
        elif port:
            self._serial = SerialLeaderboardSource(port)
            self._source = self._serial
        else:
            self._spi = SpiLeaderboardSource()
            self._source = self._spi

    def generate_frame(self, time_elapsed: float, frame_count: int) -> RenderedFrame:
        del frame_count
        context = self._context
        params = self._normalized_parameters(context.parameters if context else self.params)
        self.params = params
        preview = self._owns_source and self._hardware_port(params) is None
        if self._owns_source:
            self._bind_port(None if preview else self._hardware_port(params))
        elapsed = max(0.0, float(context.phase_time if context else time_elapsed))
        if preview:
            state = _sample_game(elapsed)
        else:
            state = self._source.latest() if self._source is not None else None
        pulse = _pulse(elapsed, params["glow"])
        strips = int(self.controller.strip_count)
        leds = int(self.controller.leds_per_strip)
        key = (
            None if state is None else (state.started, state.turn, state.scores, state.connected, state.roster),
            round(pulse, 3),
            strips,
            leds,
            params["glow"],
        )
        if key == self._last_key:
            return RenderedFrame(self._pixels, changed=False, dirty_ranges=())
        self._paint(state, pulse, strips, leds)
        self._last_key = key
        return RenderedFrame(self._pixels, changed=True)

    def _paint(self, state: Optional[LeaderboardState], pulse: float, strips: int, leds: int) -> None:
        # LED 0 is the bottom of each strip and the last LED is the top.
        # Paint in portrait order, then store that image upside down.
        view = self._pixels.reshape(strips, leds, 3)[:, ::-1, :]
        view.fill(0)
        white = np.array((255, 255, 255), dtype=np.float32)
        for index, color in enumerate(PLAYER_COLORS):
            y0 = index * _ROW_HEIGHT
            y1 = y0 + _ROW_HEIGHT
            if y1 > leds:
                break
            level = _band_level(state, index, pulse)
            background = np.rint(np.array(color, dtype=np.float32) * level).astype(np.uint8)
            view[:, y0:y1, :] = background
            active = state is not None and state.started and state.turn == PLAYER_ROLES[index]
            if active:
                rim = np.rint(
                    np.array(color, dtype=np.float32) * level * (1.0 - 0.65 * pulse) + white * (0.65 * pulse)
                ).astype(np.uint8)
                view[:, y0:y0 + _ROW_PAD, :] = rim
                view[:, y1 - _ROW_PAD:y1, :] = rim
            score = _score_text(state, index)
            if score and _text_width(score) <= strips:
                _blit(view, _centered(strips, score), y0 + _ROW_PAD, score, _ink(color, level))

    def get_runtime_stats(self) -> dict[str, Any]:
        state = self._source.latest() if self._source is not None else None
        return {
            "connected": state is not None,
            "turn": None if state is None else state.turn,
            "scores": None if state is None else list(state.scores),
        }
