"""Focused safety and restoration checks for catalog playback qualification."""

from __future__ import annotations

from copy import deepcopy
import unittest
from unittest.mock import patch

from tools.qualification.catalog_live_sweep import SweepError, run


PROFILE = "a" * 64


def _managed(provider: str, component_id: str) -> dict:
    return {
        "provider": provider,
        "component_id": component_id,
        "component_digest": "b" * 64,
        "runtime_digest": "c" * 64,
        "parameter_schema_version": 1,
    }


def _scene(component_id: str = "gradient") -> dict:
    return {
        "schema": "ledgrid.scene.v2",
        "background": {
            "component_id": "native_aurora", "version": 1,
            "provider": "receiver_native", "role": "background",
            "bundle_digest": "d" * 64,
            "parameters": {"gain": .5, "source_fps": 30.0, "seed": 1},
        },
        "animation": {
            "component_id": component_id, "version": 1,
            "provider": "python", "role": "animation",
            "parameters": {"seed": 2},
        },
        "widgets": [],
        "plants": {"effects": {"version": 1, "active": [], "strengths": {}}},
        "look": {"palette_id": "neutral", "pace": 1.0,
                 "presentation_brightness": 1.0},
    }


class CatalogLiveSweepTests(unittest.TestCase):
    def test_physical_run_requires_separate_authorization_before_network_access(self):
        with patch(
            "tools.qualification.catalog_live_sweep._request_json"
        ) as request_json:
            with self.assertRaisesRegex(SweepError, "physical-wall authorization"):
                run("http://wall.invalid", hold=0.25, timeout=5.0)
        request_json.assert_not_called()

    def test_physical_run_rejects_legacy_starting_scene_before_any_activation(self):
        original = _scene()
        bootstrap = {"components": [{
            "provider": "python", "plugin_id": "gradient",
            "role": "animation", "defaults": {"seed": 2},
            "parameter_schema": {"seed": {}}, "presets": [],
            "browser_capabilities": {
                "activation_ready": True,
                "managed_identity": _managed("python", "gradient"),
            },
        }]}
        observation = {
            "is_running": True,
            "active_identity": {"global_settings_identity": {"revision": 7}},
            "vibe": {"state": {
                "vibe_id": "neutral", "profile_version": 1,
                "resolved_profile_digest": "e" * 64,
            }},
            "brightness": 128, "animation_speed_scale": .3, "target_fps": 30,
        }
        responses = [
            bootstrap,
            observation,
            {"scene": {**original, "schema": "ledgrid.scene-state"}},
        ]
        with (
            patch(
                "tools.qualification.catalog_live_sweep._request_json",
                side_effect=responses,
            ) as request_json,
            patch("tools.qualification.catalog_live_sweep._activate") as activate,
        ):
            with self.assertRaisesRegex(SweepError, "complete Scene v2"):
                run(
                    "http://wall.invalid", hold=.25, timeout=5.0,
                    physical_wall_authorized=True,
                )
        self.assertEqual(request_json.call_count, 3)
        activate.assert_not_called()

    def test_first_case_failure_restores_exact_starting_scene_and_settings(self):
        original = _scene()
        bootstrap = {
            "components": [
                {
                    "provider": "receiver_native", "plugin_id": "native_aurora",
                    "role": "background", "defaults": original["background"]["parameters"],
                    "parameter_schema": {key: {} for key in original["background"]["parameters"]},
                    "browser_capabilities": {
                        "activation_ready": True,
                        "managed_identity": _managed("receiver_native", "native_aurora"),
                    },
                    "presets": [],
                },
                {
                    "provider": "python", "plugin_id": "gradient",
                    "role": "animation", "defaults": {"seed": 2},
                    "parameter_schema": {"seed": {}}, "presets": [],
                    "browser_capabilities": {
                        "activation_ready": True,
                        "managed_identity": _managed("python", "gradient"),
                    },
                },
            ]
        }
        observation = {
            "is_running": True,
            "installation_profile_digest": PROFILE,
            "active_identity": {"global_settings_identity": {"revision": 7}},
            "vibe": {"state": {
                "vibe_id": "neutral", "profile_version": 1,
                "resolved_profile_digest": "e" * 64,
            }},
            "plant_modifiers": {"version": 1, "active": [], "strengths": {}},
            "brightness": 128, "animation_speed_scale": .3, "target_fps": 30,
        }
        telemetry_reads = 0
        candidate_envelope = {
            "case": "candidate",
            "components": [{}, {"component_digest": "b" * 64}],
        }
        original_envelope = {"case": "exact-original"}

        def request_json(_base_url, path, *, method="GET", payload=None, headers=None):
            if path == "/api/v1/composer/bootstrap":
                return deepcopy(bootstrap)
            if path == "/api/v1/composer/settings/observed":
                return deepcopy(observation)
            if path == "/api/v1/scene":
                return {"scene": deepcopy(original)}
            if path.endswith("/gradient/presets"):
                return {"presets": []}
            if path == "/api/composer/starters":
                return {"starters": [{"id": "starter"}]}
            if path == "/api/composer/starters/starter":
                return {"starter": {"id": "starter", "scene": deepcopy(original)}}
            if path == "/api/composer/looks":
                return {"looks": []}
            if path == "/api/v1/scene/checks":
                raise SweepError(
                    "POST /api/v1/scene/checks returned 400: canonical browser "
                    "scene animation managed identity is stale"
                )
            if path == "/api/v1/composer/operations/telemetry":
                nonlocal telemetry_reads
                telemetry_reads += 1
                return {
                    "controller": {
                        "current_animation": (
                            "unexpected" if telemetry_reads == 2 else "gradient"
                        )
                    },
                    "diagnostics": {"driver_stats": {"devices": []}},
                }
            raise AssertionError((method, path, payload, headers))

        restored_receipt = {
            "requested_identity": {"scene_identity": {"digest": "f" * 64}},
            "observed_identity": {"scene_identity": {"digest": "f" * 64}},
        }
        candidate_receipt = {
            "activation_id": "candidate",
            "phase": "active",
            "requested_identity": {"scene_identity": {"digest": "candidate"}},
            "observed_identity": {"scene_identity": {"digest": "candidate"}},
        }
        with (
            patch(
                "tools.qualification.catalog_live_sweep._request_json",
                side_effect=request_json,
            ),
            patch(
                "tools.qualification.catalog_live_sweep.browser_scene_requests",
                return_value=[candidate_envelope, candidate_envelope, original_envelope],
            ) as build_requests,
            patch(
                "tools.qualification.catalog_live_sweep._activate",
                side_effect=[candidate_receipt, restored_receipt],
            ) as activate,
            patch("tools.qualification.catalog_live_sweep.time.sleep"),
        ):
            summary = run(
                "http://wall.invalid", hold=.25, timeout=5.0,
                physical_wall_authorized=True,
            )

        self.assertFalse(summary["passed"])
        self.assertIn("telemetry reports", summary["failure"])
        self.assertIsNone(summary["restore_error"])
        self.assertEqual(summary["restored_component"], "gradient")
        self.assertEqual(build_requests.call_args.args[2][-1], original)
        self.assertEqual(activate.call_args_list[-1].args[1], original_envelope)
        self.assertEqual(
            activate.call_args_list[-1].args[2],
            activate.call_args_list[0].args[2],
        )


if __name__ == "__main__":
    unittest.main()
