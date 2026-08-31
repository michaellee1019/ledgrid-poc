"""Focused local UI/API proof for the bounded Composer slice."""

from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from animation.plugins.clock_overlay import ClockOverlayAnimation
from animation.plugins.conway_life import ConwayLifeAnimation
from web.app import AnimationWebInterface


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


class ComposerSliceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.wall = _WallChannel()
        self.interface = AnimationWebInterface(
            self.wall, _PreviewManager(), local_mode=True,
        )
        self.client = self.interface.app.test_client()

    def test_root_is_the_simple_local_composer_not_a_preview_or_dashboard(self) -> None:
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("Local Composer", html)
        self.assertIn("Aurora Curtains", html)
        self.assertIn("Conway Life", Path("web/static/js/composer_slice.js").read_text(encoding="utf-8"))
        self.assertIn("Go Live", html)
        self.assertNotIn("previewCanvas", html)
        self.assertIn('id="stopScene"', html)
        self.assertIn("/static/css/composer_slice.css", html)
        self.assertIn("/static/js/composer_slice.js", html)
        self.assertNotIn("/static/css/composer.css", html)

    def test_check_then_activate_keeps_wall_channel_inert_and_reconciles_exact_identity(self) -> None:
        checked = self.client.post("/api/composer/check", json=_scene([
            _conway("conway_lower", {"seed": 1971, "rule": "B3/S23"}),
            _overlay("clock_upper", {"show_seconds": True}),
        ]))
        self.assertEqual(checked.status_code, 200)
        check_body = checked.get_json()
        self.assertEqual(check_body["status"]["state"], "pending")
        self.assertEqual(
            [item["slot_id"] for item in check_body["canonical_scene"]["overlays"]],
            ["conway_lower", "clock_upper"],
        )
        first, second = check_body["canonical_scene"]["overlays"]
        self.assertEqual(first["component"]["component_id"], "conway_life")
        self.assertEqual(second["component"]["component_id"], "clock_overlay")
        self.assertEqual(first["component"]["parameters"], {
            "seed": 1971, "rule": "B3/S23", "initial_density": 0.14,
            "generations_per_second": 5.0, "seed_cells": [],
        })
        self.assertEqual(second["component"]["parameters"], {
            "format_24h": False, "show_seconds": True,
            "clock_offset_minutes": 0, "color": [255, 224, 128],
        })
        live = self.client.post("/api/composer/activate", json={
            "token": check_body["token"], "basis": check_body["basis"],
            "idempotency_key": "browser-intent-1",
        })
        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.get_json()["status"]["state"], "converged")
        self.assertEqual(live.get_json()["status"]["wall_mutations"], 0)
        self.assertEqual(self.wall.commands, [])
        self.assertEqual(len(self.interface.composer_control.commands), 1)

    def test_new_checked_basis_is_diverged_until_its_own_activation_and_bad_input_is_rejected(self) -> None:
        first = self.client.post("/api/composer/check", json=_scene()).get_json()
        self.client.post("/api/composer/activate", json={
            "token": first["token"], "basis": first["basis"], "idempotency_key": "one",
        })
        changed = _scene([_overlay("clock_upper")])
        checked = self.client.post("/api/composer/check", json=changed).get_json()
        self.assertEqual(checked["status"]["state"], "diverged")
        rejected = self.client.post("/api/composer/check", json={"origin": "dashboard"})
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(rejected.get_json()["status"]["state"], "rejected")

    def test_exact_retry_and_expired_check_have_distinct_reconciliation_states(self) -> None:
        checked = self.client.post("/api/composer/check", json=_scene()).get_json()
        request = {
            "token": checked["token"], "basis": checked["basis"],
            "idempotency_key": "same-intent",
        }
        self.assertFalse(self.client.post("/api/composer/activate", json=request).get_json()["exact_retry"])
        retry = self.client.post("/api/composer/activate", json=request).get_json()
        self.assertTrue(retry["exact_retry"])
        self.assertEqual(retry["status"]["state"], "retry")

        fresh = self.client.post("/api/composer/check", json=_scene()).get_json()
        expired = replace(self.interface._composer_checked, expires_at=0)
        self.interface._composer_checked = expired
        self.interface.composer_tokens._records[fresh["token"]].checked = expired
        stale = self.client.post("/api/composer/activate", json={
            "token": fresh["token"], "basis": fresh["basis"], "idempotency_key": "expired",
        })
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.get_json()["status"]["state"], "stale")

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
        self.assertIn("if (!activated.ok) { status(activationData); throw", script)
        self.assertNotIn("$('#reconcileState').textContent = 'Rejected'", script)

    def test_readding_after_primary_removal_selects_the_missing_slot_and_checks(self) -> None:
        """The client must restore the missing Conway lower slot without duplicates."""
        script = Path("web/static/js/composer_slice.js").read_text(encoding="utf-8")
        self.assertIn(
            "Object.keys(defaults).find((candidate) => !state.overlays.some((item) => item.slot_id === candidate))",
            script,
        )
        # Equivalent authored output after add-two, remove-Conway, add again.
        response = self.client.post("/api/composer/check", json=_scene([
            _overlay("clock_upper", {"show_seconds": True}),
            _conway("conway_lower", {"seed": 1971}),
        ]))
        self.assertEqual(response.status_code, 200)
        slots = [
            item["slot_id"]
            for item in response.get_json()["canonical_scene"]["overlays"]
        ]
        self.assertEqual(slots, ["clock_upper", "conway_lower"])
        self.assertEqual(set(slots), {"conway_lower", "clock_upper"})

    def test_third_overlay_is_rejected_before_local_adapter_mutation(self) -> None:
        rejected = self.client.post("/api/composer/check", json=_scene([
            _conway(), _overlay("clock_upper"), _overlay("extra"),
        ]))
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("zero to two", rejected.get_json()["error"])
        self.assertEqual(self.interface.composer_control.commands, [])


if __name__ == "__main__":
    unittest.main()
