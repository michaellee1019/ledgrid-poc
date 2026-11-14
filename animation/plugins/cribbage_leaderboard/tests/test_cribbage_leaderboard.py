"""Cribbage leaderboard snapshot parsing and wall painting."""

from __future__ import annotations

from pathlib import Path
import unittest

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.plugins.cribbage_leaderboard import CribbageLeaderboardAnimation
from animation.plugins.cribbage_leaderboard.state import (
    PROTOCOL_VERSION,
    MemoryLeaderboardSource,
    LeaderboardState,
    parse_state_line,
)


def _state(**overrides: object) -> LeaderboardState:
    payload = {
        "started": True,
        "turn": 2,
        "scores": (8, 15, 4, 21),
        "connected": 0x0F,
        "roster": 0x0F,
        "term": 3,
        "version": 9,
    }
    payload.update(overrides)
    return LeaderboardState(**payload)


def _animation(state: LeaderboardState | None, **params: object) -> CribbageLeaderboardAnimation:
    return CribbageLeaderboardAnimation(
        PreviewLEDController(33, 138),
        params,
        source=MemoryLeaderboardSource(state),
    )


def _row(pixels: np.ndarray, index: int) -> np.ndarray:
    view = pixels.reshape(33, 138, 3)[:, ::-1, :]
    start = index * 9
    return view[:, start : start + 9]


def _pixel(pixels: np.ndarray, logical_y: int) -> np.ndarray:
    return pixels[(138 - 1) - logical_y]


class CribbageLeaderboardTests(unittest.TestCase):
    def test_state_line_accepts_the_leaderboard_snapshot(self) -> None:
        line = (
            'STATE {"type":"state","protocol":4,"game":1,"term":2,"version":5,'
            '"leader":2934577084,"started":true,"turn":3,"scores":[1,2,3,4],'
            '"operations":[1,1,1,1],"connected":15,"local":0,"enabled":15,'
            '"explicit":15,"roster":15}'
        )
        parsed = parse_state_line(line)
        assert parsed is not None
        self.assertEqual(parsed.scores, (1, 2, 3, 4))
        self.assertEqual(parsed.turn, 3)
        self.assertTrue(parsed.started)
        self.assertEqual(parsed.revision(), (2, 5))

    def test_state_line_rejects_other_protocol_and_boot_noise(self) -> None:
        self.assertIsNone(parse_state_line("STATE-WAIT id=00000001 advertising=1"))
        self.assertIsNone(parse_state_line('STATE {"type":"state","protocol":3,"started":true,"turn":2,"scores":[0,0,0,0],"term":1,"version":1,"connected":0,"roster":0}'))
        self.assertIsNone(parse_state_line('{"type":"player","protocol":4}'))

    def test_protocol_constant_matches_the_cribbage_header(self) -> None:
        header = Path(__file__).resolve().parents[5] / "cribbage_board_robot" / "lib" / "scorebot" / "src" / "Protocol.hpp"
        if not header.is_file():
            self.skipTest("the separate cribbage firmware checkout is unavailable")
        self.assertIn(f"kWireProtocolVersion = {PROTOCOL_VERSION}", header.read_text(encoding="utf-8"))

    def test_current_player_is_brighter_and_the_score_is_drawn(self) -> None:
        animation = _animation(_state(), glow=0.5)
        peak = animation.generate_frame(0.5, 0)
        # Leave the two-pixel glow rim and read the flat color inside the band.
        red = tuple(int(channel) for channel in _pixel(peak.pixels, 4))
        blue = tuple(int(channel) for channel in _pixel(peak.pixels, 12))
        self.assertEqual(red, (255, 0, 0))
        self.assertLess(blue[2], 120)
        self.assertEqual(tuple(int(channel) for channel in peak.pixels[134]), (255, 0, 0))
        self.assertEqual(tuple(int(channel) for channel in _pixel(peak.pixels, 40)), (0, 0, 0))
        self.assertFalse(animation.generate_frame(0.5, 1).changed)
        peak_pixels = peak.pixels.copy()

        dim = animation.generate_frame(1.5, 2)
        self.assertLess(int(_pixel(dim.pixels, 4)[0]), 160)
        self.assertGreater(int(_pixel(dim.pixels, 4)[0]), int(_pixel(dim.pixels, 12)[2]))

        other = _animation(_state(scores=(0, 15, 4, 21)), glow=0.5)
        other.generate_frame(0.5, 0)
        self.assertFalse(np.array_equal(_row(peak_pixels, 0), _row(other._pixels, 0)))
        self.assertTrue(np.any(np.all(_row(peak_pixels, 0) == (255, 255, 255), axis=2)))
        self.assertTrue(np.all(_pixel(peak_pixels, 40) == 0))

    def test_players_outside_the_roster_stay_dim(self) -> None:
        playing = _animation(_state(roster=0x0D))
        playing.generate_frame(0.0, 0)
        self.assertLess(int(_pixel(playing._pixels, 9)[2]), 40)
        self.assertFalse(np.any(np.all(_row(playing._pixels, 1) == (255, 255, 255), axis=2)))

    def test_preview_port_shows_a_sample_game_without_opening_serial(self) -> None:
        animation = CribbageLeaderboardAnimation(
            PreviewLEDController(33, 138), {"glow": 0.5, "serial_port": "preview"}
        )
        frame = animation.generate_frame(0.5, 0)
        self.assertIsNone(animation._serial)
        self.assertIsNone(animation._spi)
        self.assertEqual(tuple(int(channel) for channel in _pixel(frame.pixels, 4)), (255, 0, 0))
        self.assertTrue(np.any(np.all(_row(frame.pixels, 0) == (255, 255, 255), axis=2)))
        self.assertEqual(tuple(int(channel) for channel in _pixel(frame.pixels, 40)), (0, 0, 0))
        blue_turn = animation.generate_frame(4.5, 1)
        self.assertGreater(int(_pixel(blue_turn.pixels, 12)[2]), int(_pixel(blue_turn.pixels, 4)[0]))

    def test_waiting_for_the_bridge_still_shows_four_colors(self) -> None:
        animation = _animation(None)
        frame = animation.generate_frame(0.0, 0)
        self.assertGreater(int(_pixel(frame.pixels, 4)[0]), 0)
        self.assertGreater(int(_pixel(frame.pixels, 13)[2]), 0)
        self.assertGreater(int(_pixel(frame.pixels, 22)[1]), 0)
        self.assertEqual(tuple(int(channel) for channel in _pixel(frame.pixels, 80)), (0, 0, 0))
        waiting = _row(frame.pixels, 0)
        self.assertTrue(np.any(np.all(waiting == (255, 255, 255), axis=2)))
