"""Regression tests for the semantic plant-mask painter."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from drivers.frame_codec import decode_frame_data
from web.app import AnimationWebInterface


ROOT = Path(__file__).resolve().parents[2]


class _Controller:
    strip_count = 2
    leds_per_strip = 3
    total_leds = 6


class _PreviewManager:
    controller = _Controller()
    preview_controller = controller

    def list_animations(self):
        return []


class _RecordingChannel:
    def __init__(self):
        self.commands = []

    def read_status(self):
        return {
            "led_info": {
                "strip_count": 2,
                "leds_per_strip": 3,
                "total_leds": 6,
            }
        }

    def send_command(self, action, **data):
        self.commands.append((action, data))
        return {"action": action, "data": data}


class PainterMaskTests(unittest.TestCase):
    def setUp(self):
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_dir.name)
        self.foliage_path = self.root / "plant_pixel_map_32x138.json"
        self.planter_path = self.root / "plant_globe_map_32x138.json"
        geometry = {"strip_count": 2, "leds_per_strip": 3, "total_leds": 6}
        self.foliage_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "geometry": geometry,
                    "source_image": "keep-me.jpg",
                    "covered_indices": [1, 2, 4],
                    "occluded_indices": [1, 2, 4],
                    "covered_count": 3,
                    "occluded_count": 3,
                    "pixels": [
                        {"index": index, "observed": True, "occluded": index in {1, 2, 4}}
                        for index in range(6)
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.planter_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "geometry": geometry,
                    "source_image": "keep-planters.jpg",
                    "regions": [
                        {
                            "id": "left",
                            "strip_start": 0,
                            "led_start": 0,
                            "width": 1,
                            "height": 3,
                        },
                        {
                            "id": "right",
                            "strip_start": 1,
                            "led_start": 0,
                            "width": 1,
                            "height": 3,
                        },
                    ],
                    "region_count": 2,
                    "globe_indices": [2, 3],
                    "covered_indices": [2, 3],
                    "globe_count": 2,
                    "covered_count": 2,
                    "pixels": [
                        {"index": 2, "strip": 0, "led": 2, "region": "left"},
                        {"index": 3, "strip": 1, "led": 0, "region": "right"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.channel = _RecordingChannel()
        self.interface = AnimationWebInterface(self.channel, _PreviewManager())
        self.interface.foliage_mask_path = self.foliage_path
        self.interface.planter_mask_path = self.planter_path
        self.client = self.interface.app.test_client()

    def tearDown(self):
        self.temporary_dir.cleanup()

    def test_get_masks_fails_closed_without_a_managed_selected_profile(self):
        response = self.client.get("/api/painter/masks")

        self.assertEqual(response.status_code, 400)
        self.assertIn("managed installation profile", response.get_json()["error"])

    def test_legacy_save_is_retired_and_never_updates_mask_files(self):
        original_foliage = self.foliage_path.read_bytes()
        original_planter = self.planter_path.read_bytes()
        response = self.client.post(
            "/api/painter/masks",
            json={
                "led_info": {
                    "strip_count": 2,
                    "leds_per_strip": 3,
                    "total_leds": 6,
                },
                "masks": {"foliage": [0, 4], "planter_bowls": [2, 5]},
            },
        )

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers["Allow"], "GET")
        self.assertEqual(response.get_json()["code"], "managed_profile_required")
        self.assertEqual(self.foliage_path.read_bytes(), original_foliage)
        self.assertEqual(self.planter_path.read_bytes(), original_planter)

    def test_save_rejects_overlap_and_geometry_mismatch_without_writing(self):
        original_foliage = self.foliage_path.read_text(encoding="utf-8")
        overlap = self.client.post(
            "/api/painter/masks",
            json={
                "led_info": {"strip_count": 2, "leds_per_strip": 3},
                "masks": {"foliage": [1], "planter_bowls": [1]},
            },
        )
        mismatch = self.client.post(
            "/api/painter/masks",
            json={
                "led_info": {"strip_count": 3, "leds_per_strip": 3},
                "masks": {"foliage": [], "planter_bowls": []},
            },
        )

        self.assertEqual(overlap.status_code, 405)
        self.assertEqual(mismatch.status_code, 405)
        self.assertEqual(self.foliage_path.read_text(encoding="utf-8"), original_foliage)

    def test_full_preview_endpoint_queues_the_exact_submitted_frame(self):
        frame = [[0, 0, 0], [48, 220, 96], [255, 72, 190]] * 2
        response = self.client.post(
            "/api/painter/frame",
            json={
                "led_info": {"strip_count": 2, "leds_per_strip": 3},
                "frame_data": frame,
            },
        )

        self.assertEqual(response.status_code, 200)
        action, command = self.channel.commands[-1]
        self.assertEqual(action, "painter_set_frame")
        self.assertEqual(decode_frame_data(command["frame_data_encoded"]), frame)

    def test_retired_surface_is_cleanly_absent_while_the_adapter_remains(self):
        response = self.client.get("/painter")

        self.assertEqual(response.status_code, 404)
        self.assertFalse((ROOT / "web/templates/painter.html").exists())
        self.assertFalse((ROOT / "web/static/js/painter.js").exists())


if __name__ == "__main__":
    unittest.main()
