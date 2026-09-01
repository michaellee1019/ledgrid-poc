"""Focused installed-final parity coverage for the Composer preview seam."""

from __future__ import annotations

import base64
from datetime import datetime
import unittest

import numpy as np

from web.app import AnimationWebInterface
from web.composer_final_preview import (
    ComposerFinalPreview, NATIVE_AURORA_BUNDLE_DIGEST,
    NATIVE_AURORA_COMPONENT_ID,
)


class _Controller:
    strip_count = 33
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip


class _PreviewManager:
    controller = _Controller()
    plugin_loader = None


class _WallChannel:
    def __init__(self) -> None:
        self.commands: list[dict] = []

    def send_command(self, action: str, **data: object) -> None:
        self.commands.append({"action": action, **data})


def _component(component_id: str, role: str, parameters: dict | None = None) -> dict:
    value = {
        "component_id": component_id,
        "version": 1,
        "provider": "receiver_native" if role == "background" else "python",
        "role": role,
        "parameters": parameters or {},
    }
    if role == "background":
        value["bundle_digest"] = NATIVE_AURORA_BUNDLE_DIGEST
    return value


def _scene(*, animation: str = "conway_life", widgets: list[dict] | None = None,
           palette: str = "mist", pace: float = 1.0, brightness: float = 1.0,
           plants: dict | None = None) -> dict:
    return {
        "schema": "ledgrid.scene.v2",
        "background": _component(NATIVE_AURORA_COMPONENT_ID, "background", {
            "seed": 812, "source_fps": 30.0, "gain": 0.72,
        }),
        "animation": _component(animation, "animation", {
            "seed": 1971, "rule": "B3/S23", "initial_density": 0.14,
            "generations_per_second": 5.0,
        } if animation == "conway_life" else {
            "seed": 4201, "source_fps": 30.0, "curtain_density": .56,
            "fold_depth": .58, "glow_intensity": .62,
        }),
        "widgets": widgets or [],
        "plants": plants or {"effects": {"version": 1, "active": [], "strengths": {}}},
        "look": {"palette_id": palette, "pace": pace, "presentation_brightness": brightness},
    }


def _clock(widget_id: str, color: list[int], *, led: int = 0) -> dict:
    return {
        "id": widget_id,
        "component": _component("clock_overlay", "widget", {"color": color, "show_seconds": True}),
        "visible": True,
        "placement": {"mode": "manual", "strip_translation": 0, "led_translation": led},
    }


def _request(scene: dict, elapsed: float = 1.25) -> dict:
    return {
        "origin": "composer", "scene": scene,
        "preview": {"monotonic_elapsed": elapsed, "wall_time": "2026-08-31T13:47:10+00:00"},
    }


class ComposerRuntimePreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.wall = _WallChannel()
        self.interface = AnimationWebInterface(self.wall, _PreviewManager(), local_mode=True)
        self.client = self.interface.app.test_client()

    def _pixels(self, response) -> np.ndarray:
        self.assertEqual(response.status_code, 200, response.get_json())
        frame = response.get_json()["frame"]
        return np.frombuffer(base64.b64decode(frame["pixels"]), dtype=np.uint8).reshape((33 * 138, 3))

    def test_preview_matches_the_host_final_runtime_at_real_wall_geometry(self) -> None:
        payload = _request(_scene(widgets=[_clock("clock", [255, 224, 128])], plants={
            "effects": {"version": 1, "active": ["illuminate"], "strengths": {"illuminate": .45}},
        }), elapsed=1.25)
        response = self.client.post("/api/composer/preview", json=payload)
        pixels = self._pixels(response)
        canonical = self.interface._composer_canonical({"origin": "composer", "scene": payload["scene"]})
        independent = ComposerFinalPreview(self.interface.composer_catalog, self.interface.project_root)
        expected = independent.render(canonical, 1.25, datetime.fromisoformat("2026-08-31T13:47:10+00:00")).pixels
        np.testing.assert_array_equal(pixels, expected)
        body = response.get_json()
        self.assertEqual(body["basis"], canonical.identity.to_dict())
        self.assertEqual({key: body["frame"][key] for key in ("width", "height", "encoding", "orientation")}, {
            "width": 33, "height": 138, "encoding": "rgb_u8_base64", "orientation": "strip_major_led_zero_bottom",
        })
        self.assertEqual(body["wall_mutations"], 0)
        self.assertEqual(self.wall.commands, [])
        self.assertEqual(self.interface.composer_control.commands, [])

    def test_alpha_and_opaque_animation_paths_are_both_final_compositions(self) -> None:
        alpha = self._pixels(self.client.post("/api/composer/preview", json=_request(_scene(animation="conway_life"))))
        opaque = self._pixels(self.client.post("/api/composer/preview", json=_request(_scene(animation="aurora_curtains"))))
        self.assertFalse(np.array_equal(alpha, opaque))
        native_only = self._pixels(self.client.post("/api/composer/preview", json=_request(_scene(animation="conway_life", pace=0.0))))
        self.assertTrue(np.any(alpha == native_only))
        self.assertFalse(np.array_equal(opaque, native_only))

    def test_widget_order_plant_effects_and_look_presentation_change_final_pixels(self) -> None:
        first = self._pixels(self.client.post("/api/composer/preview", json=_request(_scene(widgets=[
            _clock("red", [255, 0, 0]), _clock("blue", [0, 0, 255]),
        ]))))
        second = self._pixels(self.client.post("/api/composer/preview", json=_request(_scene(widgets=[
            _clock("blue", [0, 0, 255]), _clock("red", [255, 0, 0]),
        ]))))
        planted = self._pixels(self.client.post("/api/composer/preview", json=_request(_scene(plants={
            "effects": {"version": 1, "active": ["illuminate", "shadow"], "strengths": {"illuminate": .7, "shadow": .4}},
        }, palette="ember", pace=.7, brightness=.5))))
        # Semantic Clock Widgets intentionally share the resolved Look color;
        # author order stays canonical, but literal per-widget colors no
        # longer affect their final pixels.
        np.testing.assert_array_equal(first, second)
        self.assertFalse(np.array_equal(first, planted))

    def test_final_preview_delivers_the_resolved_palette_to_the_clock_plane(self) -> None:
        preview = ComposerFinalPreview(self.interface.composer_catalog, self.interface.project_root)
        mist_scene = _scene(widgets=[_clock("clock", [255, 224, 128])], palette="mist")
        ember_scene = _scene(widgets=[_clock("clock", [255, 224, 128])], palette="ember")
        mist = self.interface._composer_canonical({"origin": "composer", "scene": mist_scene})
        ember = self.interface._composer_canonical({"origin": "composer", "scene": ember_scene})

        preview.render(mist, 1.25, datetime.fromisoformat("2026-08-31T13:47:10+00:00"))
        clock = preview._runtime._widgets["clock"].instance
        mist_clock = clock._last_pixels.copy()
        preview.render(ember, 1.25, datetime.fromisoformat("2026-08-31T13:47:10+00:00"))
        ember_clock = clock._last_pixels.copy()

        np.testing.assert_array_equal(mist_clock[:, 3], ember_clock[:, 3])
        self.assertFalse(np.array_equal(mist_clock[:, :3], ember_clock[:, :3]))

    def test_native_cadence_advances_continuously_without_live_side_effects(self) -> None:
        scene = _scene(animation="conway_life")
        first = self._pixels(self.client.post("/api/composer/preview", json=_request(scene, elapsed=.001)))
        inside_native_tick = self._pixels(self.client.post("/api/composer/preview", json=_request(scene, elapsed=.010)))
        next_native_tick = self._pixels(self.client.post("/api/composer/preview", json=_request(scene, elapsed=.040)))
        np.testing.assert_array_equal(first, inside_native_tick)
        self.assertFalse(np.array_equal(inside_native_tick, next_native_tick))
        status = self.client.get("/api/composer/status").get_json()
        self.assertIsNone(status["desired"])
        self.assertIsNone(status["observed"])

    def test_invalid_preview_rejects_without_replacing_the_current_frame_or_state(self) -> None:
        good = _request(_scene())
        before = self.client.post("/api/composer/preview", json=good).get_json()
        bad = _request(_scene())
        bad["preview"] = {"wall_time": "not-a-time"}
        rejected = self.client.post("/api/composer/preview", json=bad)
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("wall_time", rejected.get_json()["error"])
        after = self.client.post("/api/composer/preview", json=good).get_json()
        self.assertEqual(before["basis"], after["basis"])
        self.assertEqual(self.wall.commands, [])
        self.assertEqual(self.interface.composer_control.commands, [])

    def test_preview_surface_has_no_modes_or_simulation_controls_and_refreshes_at_component_cadence(self) -> None:
        html = self.client.get("/").get_data(as_text=True)
        script = (self.interface.project_root / "web" / "static" / "js" / "composer_slice.js").read_text(encoding="utf-8")
        scheduler = (self.interface.project_root / "web" / "static" / "js" / "composer_preview_scheduler.js").read_text(encoding="utf-8")
        preview = html[html.index('class="preview-pane"'):html.index('class="operations-pane"')]
        for obsolete in ("Draft", "Original", "Split", "timeline", "FPS", "plant simulation"):
            self.assertNotIn(obsolete, preview)
        self.assertIn("Installed final", preview)
        self.assertIn("previewScheduler.start", script)
        self.assertIn("ComposerPreviewScheduler", script)
        self.assertIn("setIntervalFn", scheduler)
        self.assertIn("frame.height - 1 - led", script)


if __name__ == "__main__":
    unittest.main()
