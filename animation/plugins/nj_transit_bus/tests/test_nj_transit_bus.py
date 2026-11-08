"""Departure-board behavior for the NJ Transit 126 toward Port Authority."""

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

import numpy as np

from animation.core.base import RenderedFrame
from animation.plugins.nj_transit_bus import (
    BUS_ROWS,
    DEFAULT_STOP,
    ArrivalBoard,
    BusArrival,
    NjTransitBusAnimation,
    NjTransitClient,
    NjTransitError,
    displayed_minutes,
    encode_multipart,
    minutes_from_phrase,
    parse_board,
    text_width,
)


EASTERN = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 7, 8, 0, tzinfo=EASTERN)


class _Controller:
    strip_count = 33
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip
    debug = False


class _FixedBus(NjTransitBusAnimation):
    fixed_now = NOW

    def _clock_now(self):
        return self.fixed_now


def _board(*minutes, message="", now=NOW):
    arrivals = tuple(
        BusArrival(
            due_at=now + timedelta(minutes=value),
            minutes_reported=value,
            route="126",
            destination="PORT AUTHORITY",
            vehicle_id=str(index),
        )
        for index, value in enumerate(minutes)
    )
    return ArrivalBoard(arrivals=arrivals, message=message, fetched_at=now, status="live")


def _visual(rendered):
    physical = rendered.pixels.reshape(_Controller.strip_count, _Controller.leds_per_strip, 3)
    return physical[:, ::-1, :]


class DepartureParsingTests(unittest.TestCase):
    def test_live_phrases_become_minutes(self):
        self.assertEqual(minutes_from_phrase("in 18 mins", NOW), 18)
        self.assertEqual(minutes_from_phrase("in 1 min", NOW), 1)
        self.assertEqual(minutes_from_phrase("Due", NOW), 0)
        self.assertEqual(minutes_from_phrase("Approaching", NOW), 0)

    def test_clock_and_scheduled_stamps_are_relative_to_the_supplied_time(self):
        self.assertEqual(minutes_from_phrase("8:18 AM", NOW), 18)
        self.assertIsNone(minutes_from_phrase("7:40 AM", NOW))
        payload = {
            "DVTrip": {
                "public_route": "126",
                "header": "NEW YORK",
                "departuretime": "",
                "sched_dep_time": "11/7/2026 8:07:00 AM",
                "vehicle_id": "9",
            }
        }
        board = parse_board(payload, route="126", now=NOW)
        self.assertEqual(board.arrivals[0].minutes_reported, 7)

    def test_next_three_matching_buses_ignore_other_routes_and_boilerplate(self):
        payload = {
            "message": {"message": "NJ TRANSIT Bus Service Customer Service Message"},
            "DVTrip": [
                {"public_route": "126", "header": "NEW YORK", "departuretime": "in 18 mins", "vehicle_id": "1"},
                {"public_route": "22", "header": "NORTH BERGEN", "departuretime": "in 1 min", "vehicle_id": "2"},
                {"public_route": "126", "header": "PABT", "departuretime": "Due", "vehicle_id": "3"},
                {"public_route": "1260", "header": "SOMEWHERE", "departuretime": "in 2 mins", "vehicle_id": "4"},
                {"public_route": "126", "header": "NEW YORK", "departuretime": "in 9 mins", "vehicle_id": "5"},
                {"public_route": "126", "header": "NEW YORK", "departuretime": "in 4 mins", "vehicle_id": "6"},
                {"public_route": "126", "header": "NEW YORK", "departuretime": "in 41 mins", "vehicle_id": "7"},
            ],
        }
        board = parse_board(payload, route="126", now=NOW)
        shown = displayed_minutes(board, NOW, 45, count=3)
        self.assertEqual(shown, (0, 4, 9))
        self.assertEqual(board.message, "")
        self.assertEqual(board.arrivals[0].vehicle_id, "3")

    def test_real_service_message_is_kept_and_promoted_after_a_bus_drops(self):
        payload = {
            "message": "Lincoln Tunnel delay",
            "DVTrip": [
                {"public_route": "126N", "departuretime": "in 1 min", "vehicle_id": "a", "header": "PABT"},
                {"public_route": "126", "departuretime": "in 12 mins", "vehicle_id": "b", "header": "PABT"},
                {"public_route": "126", "departuretime": "in 20 mins", "vehicle_id": "c", "header": "PABT"},
                {"public_route": "126", "departuretime": "in 28 mins", "vehicle_id": "d", "header": "PABT"},
            ],
        }
        board = parse_board(payload, route="126", now=NOW)
        self.assertEqual(board.message, "Lincoln Tunnel delay")
        later = NOW + timedelta(seconds=110)
        self.assertEqual(displayed_minutes(board, later, 45, count=3), (11, 19, 27))

    def test_duplicate_vehicle_keeps_the_soonest_trip(self):
        payload = {
            "DVTrip": [
                {"public_route": "126", "departuretime": "in 15 mins", "vehicle_id": "same"},
                {"public_route": "126", "departuretime": "in 6 mins", "vehicle_id": "same"},
            ]
        }
        board = parse_board(payload, route="126", now=NOW)
        self.assertEqual([item.minutes_reported for item in board.arrivals], [6])


class ClientRequestTests(unittest.TestCase):
    def test_departures_post_the_126_stop_and_keep_the_token_out_of_the_url(self):
        calls = []

        def post(url, fields, timeout):
            calls.append((url, dict(fields), timeout))
            self.assertNotIn("secret-token", url)
            return {
                "DVTrip": [
                    {"public_route": "126", "departuretime": "in 5 mins", "vehicle_id": "1", "header": "PABT"}
                ]
            }

        client = NjTransitClient(post=post)
        board = client.board(
            base_url="https://pcsdata.njtransit.com/api/BUSDV2/",
            username="",
            password="",
            token="secret-token",
            route="126",
            stop=DEFAULT_STOP,
            direction="New York",
            now=NOW,
        )

        self.assertEqual(board.arrivals[0].minutes_reported, 5)
        url, fields, _timeout = calls[0]
        self.assertEqual(url, "https://pcsdata.njtransit.com/api/BUSDV2/getBusDV")
        self.assertEqual(fields["token"], "secret-token")
        self.assertEqual(fields["stop"], "20491")
        self.assertEqual(fields["route"], "126")
        self.assertEqual(fields["direction"], "New York")
        body, _boundary = encode_multipart(fields)
        self.assertIn(b'name="stop"', body)
        self.assertIn(b"20491", body)

    def test_username_and_password_mint_a_token_before_the_departure_call(self):
        calls = []

        def post(url, fields, _timeout):
            calls.append((url, dict(fields)))
            if url.endswith("/authenticateUser"):
                self.assertEqual(fields["username"], "rider")
                self.assertEqual(fields["password"], "secret")
                self.assertNotIn("token", fields)
                return {"Authenticated": "True", "UserToken": "minted"}
            return {"DVTrip": []}

        NjTransitClient(post=post).board(
            base_url="https://example.test/api/BUSDV2",
            username="rider",
            password="secret",
            token="",
            route="126",
            stop="20491",
            direction="",
            now=NOW,
        )

        self.assertTrue(calls[0][0].endswith("/authenticateUser"))
        self.assertEqual(calls[1][1]["token"], "minted")
        self.assertEqual(calls[1][1]["direction"], "")

    def test_invalid_token_reauthenticates_once(self):
        calls = []

        def post(url, fields, _timeout):
            calls.append(url)
            if url.endswith("/authenticateUser"):
                return {"Authenticated": "True", "UserToken": "fresh"}
            if fields["token"] == "stale":
                return {"errorMessage": "Invalid token."}
            return {"DVTrip": []}

        NjTransitClient(post=post).board(
            base_url="https://example.test/api/BUSDV2",
            username="rider",
            password="secret",
            token="stale",
            route="126",
            stop="20491",
            direction="New York",
            now=NOW,
        )
        self.assertEqual(calls, [
            "https://example.test/api/BUSDV2/getBusDV",
            "https://example.test/api/BUSDV2/authenticateUser",
            "https://example.test/api/BUSDV2/getBusDV",
        ])

    def test_invalid_key_without_a_password_does_not_loop(self):
        def post(_url, _fields, _timeout):
            return {"errorMessage": "Invalid token."}

        with self.assertRaises(NjTransitError):
            NjTransitClient(post=post).board(
                base_url="https://example.test/api/BUSDV2",
                username="",
                password="",
                token="nope",
                route="126",
                stop="20491",
                direction="New York",
                now=NOW,
            )


class BusBoardRenderingTests(unittest.TestCase):
    def test_bus_sprite_is_a_rectangular_bus(self):
        width = len(BUS_ROWS[0])
        self.assertTrue(all(len(row) == width for row in BUS_ROWS))
        picture = "\n".join(BUS_ROWS)
        self.assertIn("H", picture)
        self.assertIn("K", picture)
        self.assertIn("W", picture)

    def test_schema_defaults_to_the_pabt_bound_first_street_stop(self):
        schema = _FixedBus(_Controller()).get_parameter_schema()
        self.assertEqual(schema["route"]["default"], "126")
        self.assertEqual(schema["stop"]["default"], "20491")
        self.assertEqual(schema["direction"]["default"], "New York")
        self.assertEqual(schema["destination"]["default"], "PABT")
        self.assertFalse(schema["plant_aware"]["default"])

    def test_waiting_board_is_a_canonical_cached_frame(self):
        animation = _FixedBus(_Controller(), {"glow": 0.0, "speed": 1.0})
        first = animation.generate_frame(0.0, 0)
        second = animation.generate_frame(0.05, 1)

        self.assertIsInstance(first, RenderedFrame)
        self.assertEqual(first.pixels.shape, (_Controller.total_leds, 3))
        self.assertEqual(first.pixels.dtype, np.uint8)
        self.assertTrue(first.pixels.flags.c_contiguous)
        self.assertGreater(int(first.pixels.max()), 0)
        self.assertTrue(first.changed)
        self.assertFalse(second.changed)
        self.assertIs(first.pixels, second.pixels)
        self.assertEqual(animation.get_runtime_stats()["credentials"], "missing")
        self.assertEqual(animation.get_runtime_stats()["minutes"], ["", "", ""])

    def test_bus_sits_above_three_upright_minute_counts(self):
        animation = _FixedBus(_Controller(), {"glow": 0.0, "speed": 0.0, "brightness": 1.0})
        animation._snapshot = _board(4, 12, 27)
        rendered = animation.generate_frame(0.0, 0)
        visual = _visual(rendered)
        layout = animation.stack_layout((4, 12, 27))

        bus_x, bus_y = layout.bus
        bus = visual[bus_x:bus_x + len(BUS_ROWS[0]), bus_y:bus_y + len(BUS_ROWS)]
        self.assertGreater(int(bus[..., 1].max()), 150)
        self.assertTrue(np.any(np.all(bus > 200, axis=2)))
        self.assertLess(bus_y + len(BUS_ROWS), layout.times[0][1])
        self.assertLess(layout.route[1], layout.destination[1])
        self.assertLess(layout.times[0][1], layout.times[1][1])
        self.assertLess(layout.times[1][1], layout.times[2][1])
        self.assertEqual(layout.route[2], "126")
        self.assertEqual(layout.destination[2], "PABT")
        self.assertEqual(layout.unit[2], "MIN")

        scale = layout.scale
        x, y, text = layout.times[1]
        self.assertEqual(text, "12")
        self.assertEqual(text_width(text, scale), text_width("00", scale))
        top = visual[x:x + 3 * scale, y:y + scale, 0]
        self.assertTrue(np.all(top[scale:2 * scale] > 80))
        self.assertTrue(np.all(top[:scale] < 30))
        self.assertTrue(np.all(top[2 * scale:] < 30))
        base = visual[x:x + 3 * scale, y + 4 * scale:y + 5 * scale, 0]
        self.assertTrue(np.all(base > 80))

        single_x, single_y, single_text = layout.times[0]
        self.assertEqual(single_text, "4")
        self.assertGreater(single_x, layout.times[1][0])
        self.assertGreater(int(visual[single_x:single_x + 3 * scale, single_y:single_y + 5 * scale].max()), 80)

    def test_motion_is_source_rated_and_a_service_message_stays_in_the_bottom_band(self):
        quiet = {"glow": 0.0, "speed": 1.0, "brightness": 1.0}
        still = _FixedBus(_Controller(), quiet)
        still._snapshot = _board(12, 18, 27, message="LINCOLN TUNNEL DELAY")
        first = still.generate_frame(0.0, 0)
        moved = still.generate_frame(0.2, 1)
        self.assertTrue(moved.changed)
        self.assertFalse(np.array_equal(first.pixels, moved.pixels))

        without = _FixedBus(_Controller(), quiet)
        without._snapshot = _board(12, 18, 27)
        baseline = without.generate_frame(0.2, 1)
        diff = np.any(_visual(moved) != _visual(baseline), axis=2)
        self.assertTrue(diff[:, -7:].any())
        self.assertFalse(diff[:, :-7].any())
        center = np.any(_visual(first) != _visual(moved), axis=2)
        self.assertFalse(center[1:-1, :-7].any())
        self.assertTrue(center[1:-1, -7:].any())

    def test_minutes_count_down_without_another_fetch(self):
        animation = _FixedBus(_Controller(), {"glow": 0.0, "speed": 0.0})
        animation._snapshot = _board(2, 9, 16)
        animation.fixed_now = NOW + timedelta(seconds=61)
        self.assertEqual(
            displayed_minutes(animation._snapshot, animation.fixed_now, 45),
            (1, 8, 15),
        )
        rendered = animation.generate_frame(0.0, 0)
        self.assertEqual(animation.stack_layout((1, 8, 15)).times[0][2], "1")
        self.assertGreater(int(rendered.pixels.max()), 0)

    def test_live_scale_change_redraws_the_board(self):
        animation = _FixedBus(_Controller(), {"scale": 4, "glow": 0.0, "speed": 0.0})
        animation._snapshot = _board(6, 11, 19)
        before = animation.generate_frame(0.0, 0).pixels.copy()
        animation.update_parameters({"scale": 2})
        after = animation.generate_frame(0.0, 1).pixels
        self.assertFalse(np.array_equal(before, after))
        self.assertEqual(animation.stack_layout((6, 11, 19)).scale, 2)

    def test_fetch_uses_the_configured_stop_and_not_render_time_network(self):
        seen = {}

        class _Client:
            def board(self, **kwargs):
                seen.update(kwargs)
                return _board(3, 8, 14)

        animation = _FixedBus(_Controller())
        animation._client = _Client()
        animation._fetch_calls = 0
        original = animation._fetch_snapshot

        def counting():
            animation._fetch_calls += 1
            return original()

        animation._fetch_snapshot = counting
        animation.generate_frame(0.0, 0)
        self.assertEqual(animation._fetch_calls, 0)
        animation.update_parameters({"stop": "20529", "direction": ""})
        board = animation._fetch_snapshot()
        self.assertEqual(seen["stop"], "20529")
        self.assertEqual(seen["route"], "126")
        self.assertEqual(seen["direction"], "")
        self.assertEqual(displayed_minutes(board, NOW, 45), (3, 8, 14))

    def test_start_without_credentials_does_not_poll(self):
        animation = _FixedBus(_Controller())
        animation._fetch_snapshot = lambda: (_ for _ in ()).throw(AssertionError("fetched"))
        cleared = {name: "" for name in ("NJT_API_KEY", "NJT_TOKEN", "NJT_USERNAME", "NJT_PASSWORD")}
        with mock.patch.dict(os.environ, cleared, clear=False):
            animation.start()
            animation.generate_frame(0.1, 0)
            animation.cleanup()
        self.assertIsNone(animation._thread)

    def test_poller_publishes_arrivals_and_stops(self):
        animation = _FixedBus(_Controller(), {"poll_seconds": 15})
        ready = threading.Event()

        def fetch():
            ready.set()
            return _board(5, 10, 22)

        animation._fetch_snapshot = fetch
        animation._has_credentials = lambda: True
        animation.start()
        self.assertTrue(ready.wait(2))
        thread = animation._thread
        deadline = datetime.now() + timedelta(seconds=2)
        while animation.get_runtime_stats()["status"] != "live" and datetime.now() < deadline:
            threading.Event().wait(0.01)
        stats = animation.get_runtime_stats()
        animation.cleanup()
        self.assertEqual(stats["minutes"], [5, 10, 22])
        self.assertFalse(thread.is_alive())


class PlantAwareBusTests(unittest.TestCase):
    def test_disabled_and_zero_strength_match_the_plain_board(self):
        config = {"glow": 0.2, "speed": 1.0, "scale": 4}
        plain = _FixedBus(_Controller(), config)
        disabled = _FixedBus(_Controller(), {**config, "plant_aware": False})
        zero = _FixedBus(_Controller(), {
            **config,
            "plant_modifiers": {"active": ["obstacle"], "strengths": {"obstacle": 0.0}},
        })
        plain._snapshot = disabled._snapshot = zero._snapshot = _board(4, 12, 27)
        left = plain.generate_frame(0.4, 0).pixels
        middle = disabled.generate_frame(0.4, 0).pixels
        right = zero.generate_frame(0.4, 0).pixels
        np.testing.assert_array_equal(left, middle)
        np.testing.assert_array_equal(left, right)
        self.assertEqual(zero._plant_layout_offset, (0, 0))

    def test_synthetic_mask_moves_the_whole_board_off_the_glyphs(self):
        probe = _FixedBus(_Controller(), {"glow": 0.0, "speed": 0.0, "plant_clearance": 0})
        probe._snapshot = _board(4, 12, 27)
        marks = probe._compose_marks((4, 12, 27), 0)
        occupied = np.any(marks > 0, axis=2)
        layout = probe.stack_layout((4, 12, 27))
        bus_x, bus_y = layout.bus
        bus_hit = np.zeros_like(occupied)
        bus_hit[bus_x:bus_x + len(BUS_ROWS[0]), bus_y:bus_y + len(BUS_ROWS)] = True
        blocked = occupied & bus_hit
        indices = np.flatnonzero(blocked[:, ::-1].ravel()).tolist()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            foliage = root / "foliage.json"
            globes = root / "globes.json"
            foliage.write_text(json.dumps({"covered_indices": indices}), encoding="utf-8")
            globes.write_text(json.dumps({"globe_indices": indices[:12]}), encoding="utf-8")
            animation = _FixedBus(_Controller(), {
                "glow": 0.0,
                "speed": 0.0,
                "plant_aware": True,
                "plant_clearance": 0,
                "plant_mask_path": str(foliage),
                "plant_globe_mask_path": str(globes),
            })
            animation._snapshot = _board(4, 12, 27)
            animation.generate_frame(0.0, 0)
            masks = animation.get_plant_masks()
            placed = animation._place_marks(animation._compose_marks((4, 12, 27), 0))
            visual_obstacle = masks.obstacle[:, ::-1]
            overlap = int(np.count_nonzero(np.any(placed > 0, axis=2) & visual_obstacle))

        self.assertNotEqual(animation._plant_layout_offset, (0, 0))
        self.assertEqual(overlap, 0)
        self.assertEqual(animation._plant_obstacle_overlap, 0)
        self.assertEqual(
            int(np.count_nonzero(np.any(placed > 0, axis=2))),
            int(np.count_nonzero(occupied)),
        )


if __name__ == "__main__":
    unittest.main()
