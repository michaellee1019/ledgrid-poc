"""Scorebot leaderboard view and Nimbus controls; authority lives on the host."""

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
)


_FONT = {**ClockFaceRenderer.FONT, "B": (6, 5, 6, 5, 6), "X": (5, 5, 2, 5, 5),
         "Y": (5, 5, 2, 2, 2), "V": (5, 5, 5, 5, 2), "+": (0, 2, 7, 2, 0)}
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
    text = str(state.scores[index])
    if len(text) > 8:
        mantissa, exponent = format(state.scores[index], ".1e").split("e")
        return mantissa.upper() + "E" + str(int(exponent))
    return text


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

    PLAYER_BUTTONS = {
        "up": ("Select player", False), "down": ("Select player", False),
        "left": ("Draft -1 / lobby mode", True), "right": ("Draft +1 / lobby mode", True),
        "x": ("ADD draft", False), "a": ("OK / confirm", False),
        "b": ("Clear then return", False), "y": ("Resume / New Game", False),
    }
    ANIMATION_NAME = "Cribbage Leaderboard"
    ANIMATION_DESCRIPTION = "Pi-authoritative cribbage scores. Nimbus controls any player; the turn glows."
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
        self._source = source if source is not None else getattr(controller, "cribbage_session", None)
        self._has_source = self._source is not None
        self._preview = not self._has_source or self.params["serial_port"] == "preview"
        self._source = self._source or MemoryLeaderboardSource(None)
        self._control = False
        self._selection = 0
        self._drafts = [0] * 4
        self._page = ""
        self._choice = 0
        self._feedback = ""
        self._context: ResolvedScene | None = None
        strips = int(self.controller.strip_count)
        leds = int(self.controller.leds_per_strip)
        self._pixels = np.zeros((strips * leds, 3), dtype=np.uint8)
        self._last_key: tuple[Any, ...] | None = None

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
        if port.strip() not in ("", "preview"):
            raise ValueError("Serial score bridges are unsupported; use the host Scorebot session")
        result["serial_port"] = port.strip()
        return result

    @classmethod
    def get_parameter_schema(cls) -> dict[str, dict[str, Any]]:
        return {
            "glow": {"type": "float", "min": 0.0, "max": 3.0, "default": 0.7, "description": "How fast the current player's color breathes"},
            "serial_port": {"type": "str", "default": "", "description": "Blank uses the host session; preview shows a sample without hardware."},
        }

    def update_parameters(self, values: Mapping[str, Any]) -> None:
        self.params = self._normalized_parameters({**self.params, **dict(values)})
        self._last_key = None
        self._preview = not self._has_source or self.params["serial_port"] == "preview"
        self._discard_drafts()

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

    def set_player_control(self, enabled):
        self._control = bool(enabled)
        self._discard_drafts()

    def set_player_paused(self, paused):
        self._control = not paused
        self._discard_drafts()

    def _discard_drafts(self):
        self._feedback = "CLEARED" if any(self._drafts) else ""
        self._drafts = [0] * 4
        self._page = ""

    def cleanup(self):
        self._discard_drafts()
        super().cleanup()

    def handle_player_button(self, button):
        if self._preview or not hasattr(self._source, "request"):
            self._feedback = "HOST ONLY"
            return
        try:
            self._button(button)
        except (OSError, RuntimeError, ValueError) as error:
            self._feedback = str(error)
        if not self._control:
            return "leave"

    def _button(self, button):
        state = self._source.latest()
        if state is None:
            self._feedback = "UNAVAILABLE"
            return
        if button == "y":
            self._discard_drafts()
            self._page = "options"
            self._choice = 0
        elif self._page:
            if button == "b":
                self._page = ""
            elif self._page == "options":
                if button in ("up", "down", "left", "right"):
                    self._choice = 1 - self._choice
                elif button == "a":
                    self._page = "new-confirm" if self._choice else ""
            elif button == "a":
                success = (self._source.new_game() if self._page == "new-confirm"
                           else self._source.request("start"))
                self._feedback = self._source.feedback
                if success:
                    self._page = ""
        elif button in ("up", "down"):
            choices = [index for index in range(4) if not state.started or state.roster & (1 << index)]
            offset = 1 if button == "down" else -1
            current = choices.index(self._selection) if self._selection in choices else 0
            self._selection = choices[(current + offset) % len(choices)]
        elif button in ("left", "right"):
            direction = 1 if button == "right" else -1
            if state.started:
                self._drafts[self._selection] = max(-0x80000000, min(0x7FFFFFFF,
                    self._drafts[self._selection] + direction))
            else:
                bit = 1 << self._selection
                mode = 0 if state.local & bit else 1 if state.enabled & bit else 2
                self._source.request("set_lobby_mode", self._selection, (mode + direction) % 3)
                self._feedback = self._source.feedback
        elif button == "b":
            if any(self._drafts):
                self._discard_drafts()
            else:
                self._control = False
        elif button == "a" and not state.started:
            self._page = "start-confirm"
        elif button in ("a", "x") and state.started:
            if self._source.request("local_action", self._selection, self._drafts[self._selection],
                                    1 if button == "a" else 0):
                self._drafts[self._selection] = 0
            self._feedback = self._source.feedback

    def generate_frame(self, time_elapsed: float, frame_count: int) -> RenderedFrame:
        del frame_count
        context = self._context
        params = self._normalized_parameters(context.parameters if context else self.params)
        self.params = params
        preview = self._preview or params["serial_port"] == "preview"
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
            self._control, self._selection, tuple(self._drafts), self._page, self._choice,
            self._feedback, getattr(self._source, "feedback", ""),
            getattr(self._source, "pending", False), getattr(self._source, "error", ""),
            getattr(getattr(self._source, "transport", None), "error", ""),
            None if state is None else (state.local, state.enabled, state.sleeping),
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
            if not self._preview and state is not None:
                status = ((0, 255, 0) if state.local & (1 << index) else
                          (0, 255, 255) if state.connected & (1 << index) else
                          (255, 160, 0) if state.sleeping & (1 << index) else (255, 0, 0))
                view[-1, y0 + 4] = status
                if self._control and index == self._selection:
                    view[0, y0:y1] = (255, 255, 255)
            score = _score_text(state, index)
            if state is not None and not state.started and not self._preview:
                bit = 1 << index
                score = "LOCAL" if state.local & bit else "BOARD" if state.enabled & bit else "OFF"
            if score and _text_width(score) <= strips:
                _blit(view, _centered(strips, score), y0 + _ROW_PAD, score, _ink(color, level))

        if not self._preview:
            error = getattr(self._source, "error", "") or getattr(getattr(self._source, "transport", None), "error", "")
            result = getattr(self._source, "feedback", "")
            feedback = {"OK": "SAVED", "SCORE_OUT_OF_RANGE": "RANGE", "DUPLICATE": "DUP",
                        "WRONG_GAME": "OLD GAME", "INVALID_ARGUMENT": "BAD MSG",
                        "NO_CHANGE": "NO EDIT"}.get(result, result.replace("_", " ")[:8])
            draft = str(self._drafts[self._selection])
            lines = ["A EDIT" if not self._control else "DRAFT", draft[:8], draft[8:],
                     self._feedback[:8].upper(), feedback,
                     "ERROR" if error else "SAVING" if getattr(self._source, "pending", False) else ""]
            if self._control and state is not None and not state.started:
                lines = ["LOBBY", "LR MODE", "A START", feedback, "ERROR" if error else ""]
            if self._page:
                lines = ["RESUME" if not self._choice else "NEW GAME", "A SELECT"]
                if self._page != "options":
                    lines = ["NEW GAME" if self._page == "new-confirm" else "START", "A YES", "B BACK"]
            if state is not None:
                exact = str(state.scores[self._selection])
                lines += ["EXACT", exact[:8], exact[8:]]
            for index, text in enumerate(lines):
                _blit(view, _centered(strips, text), 42 + index * 7, text, (255, 80, 60) if error else (230, 230, 230))

    def get_runtime_stats(self) -> dict[str, Any]:
        state = self._source.latest() if self._source is not None else None
        return {
            "connected": state is not None,
            "turn": None if state is None else state.turn,
            "scores": None if state is None else list(state.scores),
        }
