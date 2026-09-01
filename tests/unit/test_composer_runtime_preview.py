"""Focused installed-final parity coverage for the Composer preview seam."""

from __future__ import annotations

import base64
from datetime import datetime
import unittest

import numpy as np

from web.app import AnimationWebInterface
from animation.core.manager import AnimationManager
from animation.core.scene_runtime import ScenePresentationContext
from web.composer_final_preview import (
    ComposerFinalPreview, InstalledFinalSceneRuntime, NATIVE_AURORA_BUNDLE_DIGEST,
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
        } if animation == "conway_life" else ({
            "seed": 4201, "tetromino_count": 5, "bot_imperfection": .18,
            "fall_rate": 3.0, "smooth_drop": True, "smooth_drop_strength": .6,
            "smooth_drop_max_pieces": 32, "render_fps": 150.0,
            "high_density_render_fps": 150.0,
        } if animation == "tetris" else {
            "seed": 4201, "source_fps": 30.0, "curtain_density": .56,
            "fold_depth": .58, "glow_intensity": .62,
        })),
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

    def test_live_host_adapter_and_preview_share_exact_resolved_final_context(self) -> None:
        """Independent installed runtimes must agree before browser encoding."""

        wall_time = datetime.fromisoformat("2026-08-31T13:47:10+00:00")
        cases = (
            # Premultiplied alpha, ordered semantic Widgets, enabled optics.
            _scene(
                animation="conway_life", palette="ember", pace=.7, brightness=.5,
                widgets=[_clock("first", [255, 0, 0]), _clock("second", [0, 0, 255], led=-8)],
                plants={"effects": {"version": 1, "active": ["illuminate", "shadow"],
                                    "strengths": {"illuminate": .45, "shadow": .25}}},
            ),
            # Opaque Animation and explicitly disabled plant effects retain
            # their descriptor capability opt-outs without a second final pass.
            _scene(animation="tetris", palette="mist", pace=1.25, brightness=.82,
                   plants={"effects": {"version": 1, "active": [], "strengths": {}}}),
        )
        for scene in cases:
            with self.subTest(animation=scene["animation"]["component_id"]):
                canonical = self.interface._composer_canonical({"origin": "composer", "scene": scene})
                host_runtime = InstalledFinalSceneRuntime(
                    self.interface.composer_catalog, self.interface.project_root,
                    controller=_Controller(),
                )
                # This is the only manager involvement: it accepts exact final
                # bytes and cannot replay any presentation stage itself.
                live_host = object.__new__(AnimationManager)
                live_host.controller = _Controller()
                hosted = live_host.render_scene_v2_presentation(
                    host_runtime, canonical, monotonic_elapsed=1.25, wall_time=wall_time,
                )
                preview = ComposerFinalPreview(self.interface.composer_catalog, self.interface.project_root)
                previewed = preview.render(canonical, 1.25, wall_time)
                np.testing.assert_array_equal(hosted.pixels, previewed.pixels)
                self.assertIs(live_host.current_frame_data, hosted.pixels)

    def test_alpha_and_opaque_animation_paths_are_both_final_compositions(self) -> None:
        alpha = self._pixels(self.client.post("/api/composer/preview", json=_request(_scene(animation="conway_life"))))
        opaque = self._pixels(self.client.post("/api/composer/preview", json=_request(_scene(animation="aurora_curtains"))))
        self.assertFalse(np.array_equal(alpha, opaque))
        native_only = self._pixels(self.client.post("/api/composer/preview", json=_request(_scene(animation="conway_life", pace=0.0))))
        self.assertTrue(np.any(alpha == native_only))
        self.assertFalse(np.array_equal(opaque, native_only))

    def test_tetris_opaque_plane_hides_native_background_and_keeps_clock_above(self) -> None:
        tetris = self._pixels(self.client.post("/api/composer/preview", json=_request(_scene(animation="tetris"))))
        native_only = self._pixels(self.client.post("/api/composer/preview", json=_request(_scene(animation="conway_life", pace=0.0))))
        clocked = self._pixels(self.client.post("/api/composer/preview", json=_request(_scene(
            animation="tetris", widgets=[_clock("clock", [255, 224, 128], led=-8)],
        ))))

        self.assertFalse(np.array_equal(tetris, native_only))
        self.assertFalse(np.array_equal(tetris, clocked))

    def test_paused_tetris_palette_edit_repaints_the_real_final_preview(self) -> None:
        mist = _scene(animation="tetris", palette="mist", pace=0.0)
        ember = _scene(animation="tetris", palette="ember", pace=0.0)

        mist_pixels = self._pixels(self.client.post("/api/composer/preview", json=_request(mist, elapsed=2.0)))
        ember_pixels = self._pixels(self.client.post("/api/composer/preview", json=_request(ember, elapsed=2.5)))

        self.assertFalse(np.array_equal(mist_pixels, ember_pixels))

    def test_tetris_pace_rewind_repaints_without_advancing_its_real_runtime(self) -> None:
        running = self.interface._composer_canonical({"origin": "composer", "scene": _scene(animation="tetris", pace=1.0)})
        paused = self.interface._composer_canonical({"origin": "composer", "scene": _scene(animation="tetris", pace=0.0)})
        preview = ComposerFinalPreview(self.interface.composer_catalog, self.interface.project_root)
        preview.render(running, 1.25, datetime.fromisoformat("2026-08-31T13:47:10+00:00"))
        tetris = preview._runtime._animation.instance
        before = (
            tuple(tuple(row) for row in tetris.board),
            tuple((piece.kind, piece.rotation, piece.x, piece.y, piece.fall_progress,
                   piece.action_accumulator) for piece in tetris.active_pieces),
            tetris.random.getstate(),
        )

        rewound = preview.render(paused, 1.25, datetime.fromisoformat("2026-08-31T13:47:10+00:00"))

        self.assertTrue(rewound.changed)
        self.assertEqual(tetris.last_elapsed, 0.0)
        self.assertEqual(before, (
            tuple(tuple(row) for row in tetris.board),
            tuple((piece.kind, piece.rotation, piece.x, piece.y, piece.fall_progress,
                   piece.action_accumulator) for piece in tetris.active_pieces),
            tetris.random.getstate(),
        ))

    def test_invalid_tetris_parameter_preserves_the_last_live_scene(self) -> None:
        scene = _scene(animation="tetris")
        published = self.client.post("/api/composer/scene", json={
            "origin": "composer", "scene": scene, "client_id": "desktop", "client_sequence": 1,
        })
        self.assertEqual(published.status_code, 200)
        original = published.get_json()["current"]
        invalid = _scene(animation="tetris")
        invalid["animation"]["parameters"]["speed"] = 1.0
        rejected = self.client.post("/api/composer/scene", json={
            "origin": "composer", "scene": invalid, "client_id": "desktop", "client_sequence": 2,
        })

        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(self.client.get("/api/composer/status").get_json()["current"], original)

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
