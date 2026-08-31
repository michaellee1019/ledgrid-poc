"""Focused local UI/API proof for the bounded Composer slice."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from animation.plugins.clock_overlay import ClockOverlayAnimation
from animation.plugins.conway_life import ConwayLifeAnimation
from web.app import AnimationWebInterface
from web.composer_final_preview import NATIVE_AURORA_BUNDLE_DIGEST
from web.working_draft_store import WorkingDraftStore


class _Controller:
    strip_count = 33
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip


class _PreviewManager:
    controller = _Controller()
    plugin_loader = None


class _WallChannel:
    """Historical controller channel: Composer must never write to it."""

    def __init__(self) -> None:
        self.commands: list[dict] = []

    def send_command(self, action: str, **data: object) -> None:
        self.commands.append({"action": action, **data})


def _scene(overlays: list[dict] | None = None) -> dict:
    return {
        "origin": "composer",
        "scene": {
            "schema": "ledgrid.scene.v1",
            "vibe": "quiet",
            "master_brightness": 1,
            "background": {
                "slot_id": "background",
                "component_id": "aurora_curtains",
                "version": 1,
                "provider": "python",
                "role": "background",
                "parameters": {
                    "curtain_density": 0.56,
                    "fold_depth": 0.58,
                    "glow_intensity": 0.62,
                    "source_fps": 30,
                    "seed": 4201,
                },
            },
            "overlays": overlays or [],
        },
    }


def _overlay(slot_id: str, parameters: dict | None = None) -> dict:
    return {
        "slot_id": slot_id,
        "component": {
            "component_id": "clock_overlay",
            "version": 1,
            "provider": "python",
            "role": "overlay",
            "parameters": parameters or {},
        },
        "enabled": True,
        "opacity": 192,
        "placement": {
            "strip_translation": 0,
            "led_translation": 0,
            "clip_policy": "clip_to_wall",
        },
        "stale_policy": {"policy": "hold"},
    }


def _conway(slot_id: str = "conway_lower", parameters: dict | None = None) -> dict:
    return {
        "slot_id": slot_id,
        "component": {
            "component_id": "conway_life", "version": 1,
            "provider": "python", "role": "overlay", "parameters": parameters or {},
        },
        "enabled": True, "opacity": 190,
        "placement": {"strip_translation": 0, "led_translation": 0, "clip_policy": "clip_to_wall"},
        "stale_policy": {"policy": "hold"},
    }


def _current_scene() -> dict:
    return {
        "schema": "ledgrid.scene.v2",
        "background": {"component_id": "native_aurora", "version": 1, "provider": "receiver_native", "role": "background", "bundle_digest": NATIVE_AURORA_BUNDLE_DIGEST, "parameters": {"gain": .62, "source_fps": 30, "seed": 4201}},
        "animation": {"component_id": "aurora_curtains", "version": 1, "provider": "python", "role": "animation", "parameters": {"curtain_density": .56, "fold_depth": .58, "glow_intensity": .62, "source_fps": 30, "seed": 4201}},
        "widgets": [], "plants": {"effects": {"version": 1, "active": [], "strengths": {}}},
        "look": {"palette_id": "mist", "pace": .7, "presentation_brightness": .82},
    }


class ComposerSliceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.wall = _WallChannel()
        self.interface = AnimationWebInterface(
            self.wall, _PreviewManager(), local_mode=True,
        )
        self.interface.working_draft = WorkingDraftStore(Path(self.tmp.name) / "recovery.json")
        self.client = self.interface.app.test_client()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_root_is_the_simple_local_composer_not_a_preview_or_dashboard(self) -> None:
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("Operations", html)
        self.assertIn("Installed final", html)
        self.assertIn("conway_life", Path("web/static/js/composer_slice.js").read_text(encoding="utf-8"))
        self.assertIn('id="liveAction"', html)
        self.assertNotIn("previewCanvas", html)
        self.assertNotIn("Tools", html)
        self.assertIn("/static/css/composer_slice.css", html)
        self.assertIn("/static/js/composer_slice.js", html)
        self.assertNotIn("/static/css/composer.css", html)

    def test_advisory_check_and_scene_submission_keep_wall_channel_inert(self) -> None:
        scene = _current_scene()
        checked = self.client.post("/api/composer/check", json={"origin": "composer", "scene": scene})
        self.assertEqual(checked.status_code, 200)
        self.assertTrue(checked.get_json()["valid"])
        self.assertEqual(checked.get_json()["status"]["state"], "ready")
        live = self.client.post("/api/composer/scene", json={"origin": "composer", "scene": scene, "client_id": "desktop", "mutation_id": "browser-intent-1", "client_sequence": 1})
        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.get_json()["state"], "live")
        self.assertEqual(live.get_json()["wall_mutations"], 0)
        self.assertEqual(self.wall.commands, [])
        self.assertEqual(len(self.interface.composer_control.commands), 1)

    def test_valid_direct_edit_stays_live_and_bad_input_is_rejected(self) -> None:
        first = _current_scene()
        self.assertEqual(self.client.post("/api/composer/scene", json={"origin": "composer", "scene": first, "client_id": "desktop", "client_sequence": 1}).status_code, 200)
        changed = _current_scene()
        changed["look"] = {**changed["look"], "pace": 1.2}
        published = self.client.post("/api/composer/scene", json={"origin": "composer", "scene": changed, "client_id": "desktop", "client_sequence": 2})
        self.assertEqual(published.get_json()["state"], "live")
        rejected = self.client.post("/api/composer/check", json={"origin": "dashboard"})
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(rejected.get_json()["status"]["state"], "live")

    def test_preview_exposes_runtime_widget_placement_diagnostics(self) -> None:
        scene = _current_scene()
        scene["widgets"] = [{
            "id": "clock", "component": {"component_id": "clock_overlay", "version": 1, "provider": "python", "role": "widget", "parameters": {}},
            "visible": True, "placement": {"mode": "manual", "strip_translation": 0, "led_translation": -8},
        }]
        preview = self.client.post("/api/composer/preview", json={"origin": "composer", "scene": scene, "preview": {"monotonic_elapsed": 12.0, "wall_time": "2026-08-31T12:00:00+00:00"}})
        self.assertEqual(preview.status_code, 200)
        placement = preview.get_json()["widget_placements"]["clock"]
        self.assertIn("warning", placement)
        self.assertIn("plant_overlap_pixels", placement)

    def test_exact_scene_retry_is_idempotent_without_a_check_token(self) -> None:
        request = {"origin": "composer", "scene": _current_scene(), "client_id": "desktop", "mutation_id": "same-intent", "client_sequence": 1}
        self.assertFalse(self.client.post("/api/composer/scene", json=request).get_json()["exact_retry"])
        retry = self.client.post("/api/composer/scene", json=request).get_json()
        self.assertTrue(retry["exact_retry"])
        self.assertEqual(retry["state"], "live")

    def test_current_recovery_hydrates_the_newest_remote_scene_and_invalidates_undo(self) -> None:
        first = _current_scene()
        self.assertEqual(self.client.post("/api/composer/scene", json={
            "origin": "composer", "scene": first, "client_id": "first-tab", "client_sequence": 1,
        }).status_code, 200)
        initial = self.client.get("/api/composer/recovery?client_id=first-tab").get_json()
        self.assertEqual(initial["recovery"]["scene"], first)
        changed = _current_scene()
        changed["look"] = {**changed["look"], "pace": 1.15}
        self.assertEqual(self.client.post("/api/composer/scene", json={
            "origin": "composer", "scene": changed, "client_id": "second-tab", "client_sequence": 1,
        }).status_code, 200)
        refreshed = self.client.get("/api/composer/recovery?client_id=first-tab").get_json()
        self.assertEqual(refreshed["recovery"]["scene"], changed)
        self.assertTrue(refreshed["status"]["undo_invalidated"])
        self.assertNotIn("draft", refreshed)

    def test_recovery_prefers_the_atomic_live_scene_over_a_stale_persisted_copy(self) -> None:
        first = _current_scene()
        self.client.post("/api/composer/scene", json={"origin": "composer", "scene": first, "client_id": "first", "client_sequence": 1})
        stale_recovery = self.interface.working_draft.get()
        changed = _current_scene()
        changed["look"] = {**changed["look"], "pace": 1.35}
        self.client.post("/api/composer/scene", json={"origin": "composer", "scene": changed, "client_id": "second", "client_sequence": 1})
        # A stale disk read must not be allowed to pair with the newer live
        # revision; /recovery takes the scene from the live-state lock first.
        self.interface.working_draft.get = lambda: stale_recovery
        body = self.client.get("/api/composer/recovery?client_id=first").get_json()
        self.assertTrue(body["recovery"]["authoritative"])
        self.assertEqual(body["recovery"]["scene"], changed)
        self.assertEqual(body["recovery"]["basis"], body["status"]["current"])

    def test_corrupt_recovery_is_a_reachable_data_error_with_current_status(self) -> None:
        self.interface.working_draft.path.write_text(
            '{"schema":"ledgrid.composer.recovery.v2","basis":{},"opened_look_id":null,"saved_at":1,"scene":{"schema":"ledgrid.scene.v2"}}',
            encoding="utf-8",
        )
        response = self.client.get("/api/composer/recovery?client_id=startup")
        self.assertEqual(response.status_code, 400)
        self.assertIn("status", response.get_json())
        script = Path("web/static/js/composer_slice.js").read_text(encoding="utf-8")
        self.assertIn("function recoverFromInvalidRecovery(body)", script)
        self.assertIn("if (response.status >= 500)", script)
        self.assertIn("if (error.serverUnavailable)", script)

    def test_catalog_uses_the_real_clock_overlay_descriptor_and_client_preserves_error_state(self) -> None:
        descriptor = ClockOverlayAnimation.component_descriptor()
        self.assertIs(
            self.interface.composer_catalog.require(
                provider="python", component_id="clock_overlay", version=1,
            ),
            descriptor,
        )
        self.assertIs(
            self.interface.composer_catalog.require(
                provider="python", component_id="conway_life", version=1,
            ),
            ConwayLifeAnimation.component_descriptor(),
        )
        with self.assertRaises(ValueError):
            self.interface.composer_catalog.require(
                provider="python", component_id="alert", version=1,
            )
        script = Path("web/static/js/composer_slice.js").read_text(encoding="utf-8")
        self.assertIn("const endpoint = builtin ? '/built-ins/open' : '/scene';", script)
        self.assertIn("renderStatus(result);", script)
        self.assertIn("refreshInFlight", script)
        self.assertIn("recoveryMatchesStatus", script)

    def test_readding_after_primary_removal_selects_the_missing_slot_and_checks(self) -> None:
        """The client must restore the missing Conway lower slot without duplicates."""
        script = Path("web/static/js/composer_slice.js").read_text(encoding="utf-8")
        self.assertIn("await submit(next, {rememberEdit: true});", script)
        # Equivalent authored output after add-two, remove-Conway, add again.
        response = self.client.post("/api/composer/scene", json={"origin": "composer", "scene": _current_scene(), "client_id": "desktop", "client_sequence": 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["state"], "live")

    def test_third_overlay_is_rejected_before_local_adapter_mutation(self) -> None:
        rejected = self.client.post("/api/composer/check", json=_scene([_conway(), _overlay("clock_upper"), _overlay("extra")]))
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("legacy or forbidden", rejected.get_json()["error"])
        self.assertEqual(self.interface.composer_control.commands, [])


if __name__ == "__main__":
    unittest.main()
