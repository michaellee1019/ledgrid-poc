"""Centered bus-time overlay for the NJ Transit 126 toward Port Authority."""

from datetime import datetime, timedelta
import os
import threading
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

import numpy as np

from animation.core.compositing import BaseFrame, HostSceneCompositor, PlacedOverlay
from animation.core.manager import PreviewLEDController
from animation.core.presentation_contracts import OverlayFrame
from animation.plugins.aurora_curtains import AuroraCurtainsAnimation
from animation.plugins.nj_transit_bus import (
    BUS_EMOJI,
    DEFAULT_STOP,
    ArrivalBoard,
    BusArrival,
    NjTransitBusAnimation,
    NjTransitClient,
    NjTransitError,
    displayed_minutes,
    encode_multipart,
    minutes_from_phrase,
    overlay_layout,
    parse_board,
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
    return rendered.pixels.reshape(_Controller.strip_count, _Controller.leds_per_strip, 4)[:, ::-1]


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


class BusOverlayRenderingTests(unittest.TestCase):
    def test_schema_defaults_to_the_pabt_bound_first_street_stop(self):
        schema = _FixedBus(_Controller()).get_parameter_schema()
        self.assertEqual(schema["route"]["default"], "126")
        self.assertEqual(schema["stop"]["default"], "20491")
        self.assertEqual(schema["direction"]["default"], "New York")
        self.assertNotIn("speed", schema)
        self.assertNotIn("glow", schema)
        self.assertNotIn("plant_aware", schema)

    def test_waiting_overlay_is_a_cached_transparent_rgba_plane(self):
        animation = _FixedBus(_Controller())
        first = animation.generate_frame(0.0, 0)
        second = animation.generate_frame(0.05, 1)

        self.assertIsInstance(first, OverlayFrame)
        self.assertEqual(first.pixels.shape, (_Controller.total_leds, 4))
        self.assertEqual(first.pixels.dtype, np.uint8)
        self.assertTrue(first.pixels.flags.c_contiguous)
        self.assertTrue(np.all(first.pixels[:, :3] <= first.pixels[:, 3:4]))
        self.assertGreater(int(first.pixels[:, 3].max()), 0)
        self.assertGreater(int(np.count_nonzero(first.pixels[:, 3] == 0)), _Controller.total_leds // 2)
        self.assertTrue(first.changed)
        self.assertFalse(second.changed)
        self.assertIs(first.pixels, second.pixels)
        self.assertEqual(animation.get_runtime_stats()["credentials"], "missing")
        self.assertEqual(animation.get_runtime_stats()["minutes"], ["", "", ""])

    def test_bus_emoji_sits_above_three_centered_minute_counts(self):
        animation = _FixedBus(_Controller())
        animation._snapshot = _board(4, 12, 27)
        rendered = animation.generate_frame(0.0, 0)
        visual = _visual(rendered)
        layout = animation.overlay_layout((4, 12, 27))
        expected = overlay_layout((4, 12, 27), width=33, height=138)

        self.assertEqual(layout, expected)
        emoji_x, emoji_y = layout.emoji
        self.assertEqual(emoji_x, (33 - len(BUS_EMOJI[0])) // 2)
        emoji = visual[emoji_x:emoji_x + len(BUS_EMOJI[0]), emoji_y:emoji_y + len(BUS_EMOJI)]
        self.assertGreater(int(emoji[..., 3].max()), 0)
        self.assertLess(emoji_y + len(BUS_EMOJI), layout.times[0][1])
        self.assertLess(layout.times[0][1], layout.times[1][1])
        self.assertLess(layout.times[1][1], layout.times[2][1])
        self.assertEqual([text for _x, _y, text in layout.times], ["4", "12", "27"])

        x, y, text = layout.times[1]
        self.assertEqual(text, "12")
        self.assertEqual(x, (33 - 7) // 2)
        top = visual[x:x + 3, y, 3]
        self.assertTrue(np.all(top[1:2] > 80))
        self.assertTrue(np.all(top[:1] < 30))
        self.assertTrue(np.all(top[2:] < 30))
        base = visual[x:x + 3, y + 4, 3]
        self.assertTrue(np.all(base > 80))

        single_x, single_y, single_text = layout.times[0]
        self.assertEqual(single_text, "4")
        self.assertGreater(single_x, layout.times[1][0])
        self.assertGreater(int(visual[single_x:single_x + 3, single_y:single_y + 5, 3].max()), 80)

        opaque = np.any(visual[..., 3] > 0, axis=0)
        occupied_rows = np.flatnonzero(opaque)
        self.assertGreater(occupied_rows[0], 40)
        self.assertLess(occupied_rows[-1], 100)

    def test_legacy_board_parameters_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "non-local"):
            _FixedBus(_Controller(), {"glow": 0.2, "speed": 1.0})

    def test_minutes_count_down_without_another_fetch(self):
        animation = _FixedBus(_Controller())
        animation._snapshot = _board(2, 9, 16)
        animation.fixed_now = NOW + timedelta(seconds=61)
        self.assertEqual(
            displayed_minutes(animation._snapshot, animation.fixed_now, 45),
            (1, 8, 15),
        )
        rendered = animation.generate_frame(0.0, 0)
        self.assertEqual(animation.overlay_layout((1, 8, 15)).times[0][2], "1")
        self.assertGreater(int(rendered.pixels[:, 3].max()), 0)

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

    def test_composed_overlay_does_not_cover_the_whole_scene(self):
        controller = PreviewLEDController(strips=33, leds_per_strip=138)
        aurora = AuroraCurtainsAnimation(controller, {"seed": 812, "source_fps": 20.0})
        background = aurora.generate_frame(1.0, 0)
        semantic_before = aurora.semantic_snapshot()
        overlay = _FixedBus(controller)
        overlay._snapshot = _board(4, 12, 27)
        plane = overlay.generate_frame(0.0, 0)
        compositor = HostSceneCompositor(33, 138)
        composed = compositor.compose(BaseFrame(background.pixels), (PlacedOverlay(plane),))
        self.assertFalse(np.array_equal(composed.pixels, background.pixels))
        coverage = plane.pixels[:, 3] > 0
        self.assertLess(int(np.count_nonzero(coverage)), 33 * 138 // 4)
        np.testing.assert_array_equal(composed.pixels[~coverage], background.pixels[~coverage])
        self.assertEqual(semantic_before, aurora.semantic_snapshot())
        disabled = compositor.compose(BaseFrame(background.pixels, changed=False), (PlacedOverlay(plane, enabled=False),))
        np.testing.assert_array_equal(disabled.pixels, background.pixels)


if __name__ == "__main__":
    unittest.main()
