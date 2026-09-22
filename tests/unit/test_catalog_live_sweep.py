"""Focused safety and restoration checks for catalog playback qualification."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.qualification.catalog_live_sweep import (
    SweepError, _activate_after_revision_conflict, _counter_deltas, _digest,
    _is_receiver_three_status_error, _representative_cases,
    _wait_for_component, run,
)


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
    def test_active_receipt_waits_for_telemetry_to_observe_new_component(self):
        old = {"controller": {"current_animation": "christmas_tree"}}
        new = {"controller": {"current_animation": "sparkle"}}
        with (
            patch("tools.qualification.catalog_live_sweep._request_json",
                  side_effect=[old, new]) as request_json,
            patch("tools.qualification.catalog_live_sweep.time.sleep"),
        ):
            self.assertEqual(
                _wait_for_component("http://wall.invalid", "sparkle", timeout=5.0),
                new,
            )
            self.assertEqual(request_json.call_count, 2)

    def test_revision_conflict_rechecks_but_other_409_does_not(self):
        settings = {"revision": 1}
        conflict = SweepError(
            'PUT /api/v1/scene returned 409: {"error":"controller state changed '
            'after Check","code":"activation_conflict"}'
        )
        receipt = {"phase": "active"}
        with (
            patch("tools.qualification.catalog_live_sweep._settings_at_current_revision",
                  side_effect=[{"revision": 2}, {"revision": 3}]),
            patch("tools.qualification.catalog_live_sweep._activate",
                  side_effect=[conflict, receipt]) as activate,
        ):
            self.assertEqual(
                _activate_after_revision_conflict(
                    "http://wall.invalid", {"scene": "saved"}, settings,
                    timeout=5.0,
                ),
                receipt,
            )
            self.assertEqual(activate.call_args_list[0].args[2]["revision"], 2)
            self.assertEqual(activate.call_args_list[1].args[2]["revision"], 3)
        with (
            patch("tools.qualification.catalog_live_sweep._settings_at_current_revision",
                  return_value={"revision": 2}),
            patch("tools.qualification.catalog_live_sweep._activate",
                  side_effect=SweepError("PUT /api/v1/scene returned 409: wrong identity"))
                  as activate,
        ):
            with self.assertRaisesRegex(SweepError, "wrong identity"):
                _activate_after_revision_conflict(
                    "http://wall.invalid", {"scene": "saved"}, settings,
                    timeout=5.0,
                )
            self.assertEqual(activate.call_count, 1)

    def test_representative_cases_keep_opaque_composed_and_saved_look(self):
        cases = [
            {"case_id": "default:conway_life", "kind": "default"},
            {"case_id": "preset:sparkle:quiet", "kind": "preset"},
            {"case_id": "default:sparkle", "kind": "default"},
            {"case_id": "look:kept", "kind": "look"},
        ]
        self.assertEqual(
            [case["case_id"] for case in _representative_cases(cases)],
            ["default:sparkle", "default:conway_life", "look:kept"],
        )
        del cases[-1]
        with self.assertRaisesRegex(SweepError, "saved Look"):
            _representative_cases(cases)

    def test_accepted_receiver_three_status_noise_remains_visible(self):
        deltas, failures = _counter_deltas(
            {3: {"receiver_status_misses": 2}, 2: {"receiver_status_misses": 0}},
            {3: {"receiver_status_misses": 5}, 2: {"receiver_status_misses": 0}},
        )
        self.assertEqual(deltas, {"receiver_3.receiver_status_misses": 3})
        self.assertEqual(failures, [])
        self.assertTrue(_is_receiver_three_status_error(
            "receiver 3 lacks intact protected-v8 display status"
        ))
        self.assertFalse(_is_receiver_three_status_error(
            "receiver 2 lacks intact protected-v8 display status"
        ))

    def test_interrupt_checkpoint_resumes_only_exact_release_and_starting_scene(self):
        original = _scene()
        observation = {
            "is_running": True,
            "installation_profile_digest": PROFILE,
            "active_identity": {
                "scene_identity": {"revision": 2, "digest": _digest(original)},
                "global_settings_identity": {"revision": 7},
            },
            "vibe": {"state": {
                "vibe_id": "neutral", "profile_version": 1,
                "resolved_profile_digest": "e" * 64,
            }},
            "plant_modifiers": {"version": 1, "active": [], "strengths": {}},
            "brightness": 128, "animation_speed_scale": .3, "target_fps": 30,
        }
        cases = [
            {"case_id": "default:gradient", "kind": "default",
             "component_id": "gradient", "scene": original},
            {"case_id": "preset:gradient:quiet", "kind": "preset",
             "component_id": "gradient", "scene": original},
        ]
        envelopes = [
            {"case": "first", "components": [{}, {"component_digest": "b" * 64}]},
            {"case": "second"}, {"case": "original"},
        ]
        release = ["release-one"]
        activated_cases = []

        def request_json(_base, path, *, method="GET", payload=None, headers=None):
            if path == "/api/v1/composer/bootstrap":
                return {"components": []}
            if path == "/api/v1/composer/settings/observed":
                return deepcopy(observation)
            if path == "/api/v1/scene":
                return {"scene": deepcopy(original)}
            if path.endswith("/gradient/presets"):
                return {"presets": []}
            if path == "/api/composer/starters":
                return {"starters": []}
            if path == "/api/composer/looks":
                return {"looks": []}
            if path == "/api/v1/scene/checks":
                raise SweepError(
                    "POST /api/v1/scene/checks returned 400: canonical browser "
                    "scene animation managed identity is stale"
                )
            if path == "/api/v1/composer/operations/telemetry":
                return {
                    "controller": {"release_id": release[0],
                                   "release_consistent": True,
                                   "current_animation": "gradient"},
                    "diagnostics": {"driver_stats": {"devices": []}},
                }
            raise AssertionError((method, path, payload, headers))

        def activate(_base, candidate, _settings, *, timeout):
            if candidate["case"] != "original":
                activated_cases.append(candidate["case"])
            identity = {"scene_identity": {"digest": _digest(original)}}
            return {
                "activation_id": candidate["case"], "phase": "active",
                "requested_identity": identity, "observed_identity": identity,
            }

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("tools.qualification.catalog_live_sweep._request_json",
                  side_effect=request_json),
            patch("tools.qualification.catalog_live_sweep.animation_components",
                  return_value=[{"plugin_id": "gradient",
                                 "parameter_schema": {"seed": {}}}]),
            patch("tools.qualification.catalog_live_sweep.catalog_cases",
                  return_value=cases),
            patch("tools.qualification.catalog_live_sweep.browser_scene_requests",
                  return_value=envelopes),
            patch("tools.qualification.catalog_live_sweep._activate",
                  side_effect=activate),
            patch("tools.qualification.catalog_live_sweep.time.sleep"),
        ):
            checkpoint = Path(directory) / "sweep.json"
            first = run(
                "http://wall.invalid", hold=.25, timeout=5.0,
                physical_wall_authorized=True, checkpoint_path=checkpoint,
                should_stop=lambda: len(activated_cases) == 1,
            )
            self.assertFalse(first["passed"])
            self.assertEqual(first["passed_count"], 1)
            self.assertGreaterEqual(first["results"][0]["check_to_display_seconds"], 0)
            self.assertTrue(json.loads(checkpoint.read_text())["restored"])
            second = run(
                "http://wall.invalid", hold=.25, timeout=5.0,
                physical_wall_authorized=True, checkpoint_path=checkpoint,
                resume=True,
            )
            self.assertTrue(second["passed"])
            self.assertEqual(second["resumed_count"], 1)
            self.assertEqual(activated_cases, ["first", "second"])
            self.assertEqual(len(json.loads(checkpoint.read_text())["results"]), 2)
            accepted_checkpoint = checkpoint.read_text()
            saved = json.loads(accepted_checkpoint)
            del saved["results"][0]["requested_identity"]
            del saved["results"][0]["observed_identity"]
            checkpoint.write_text(json.dumps(saved))
            with self.assertRaisesRegex(SweepError, "invalid completed-case prefix"):
                run(
                    "http://wall.invalid", hold=.25, timeout=5.0,
                    physical_wall_authorized=True, checkpoint_path=checkpoint,
                    resume=True,
                )
            checkpoint.write_text(accepted_checkpoint)
            observation["installation_profile_digest"] = "f" * 64
            with self.assertRaisesRegex(SweepError, "does not match this release"):
                run(
                    "http://wall.invalid", hold=.25, timeout=5.0,
                    physical_wall_authorized=True, checkpoint_path=checkpoint,
                    resume=True,
                )
            observation["installation_profile_digest"] = PROFILE
            with self.assertRaisesRegex(SweepError, "does not match this release"):
                run(
                    "http://wall.invalid", hold=2.25, timeout=5.0,
                    physical_wall_authorized=True, checkpoint_path=checkpoint,
                    resume=True,
                )
            release[0] = "release-two"
            with self.assertRaisesRegex(SweepError, "does not match this release"):
                run(
                    "http://wall.invalid", hold=.25, timeout=5.0,
                    physical_wall_authorized=True, checkpoint_path=checkpoint,
                    resume=True,
                )
            release[0] = "release-one"
            observation["active_identity"]["scene_identity"]["digest"] = "0" * 64
            with self.assertRaisesRegex(SweepError, "differs from the starting Scene"):
                run(
                    "http://wall.invalid", hold=.25, timeout=5.0,
                    physical_wall_authorized=True, checkpoint_path=checkpoint,
                    resume=True,
                )
            observation["active_identity"]["scene_identity"]["digest"] = _digest(original)
            cases[1]["case_id"] = "preset:gradient:changed"
            with self.assertRaisesRegex(SweepError, "does not match this release"):
                run(
                    "http://wall.invalid", hold=.25, timeout=5.0,
                    physical_wall_authorized=True, checkpoint_path=checkpoint,
                    resume=True,
                )
            self.assertEqual(activated_cases, ["first", "second"])

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
            patch(
                "tools.qualification.catalog_live_sweep._wait_for_component",
                side_effect=[
                    SweepError("telemetry did not observe active gradient"),
                    {"controller": {"current_animation": "gradient"}},
                ],
            ),
            patch("tools.qualification.catalog_live_sweep.time.sleep"),
        ):
            summary = run(
                "http://wall.invalid", hold=.25, timeout=5.0,
                physical_wall_authorized=True,
            )

        self.assertFalse(summary["passed"])
        self.assertIn("telemetry did not observe active", summary["failure"])
        self.assertIsNone(summary["restore_error"])
        self.assertEqual(summary["restored_component"], "gradient")
        self.assertEqual(build_requests.call_args.args[2][-1], original)
        self.assertEqual(activate.call_args_list[-1].args[1], original_envelope)
        self.assertEqual(
            activate.call_args_list[-1].args[2],
            activate.call_args_list[0].args[2],
        )

    def test_interrupt_during_activation_restores_then_propagates(self):
        original = _scene()
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
        animation = {
            "plugin_id": "gradient", "parameter_schema": {"seed": {}}
        }
        candidate_envelope = {
            "case": "candidate",
            "components": [{}, {"component_digest": "b" * 64}],
        }
        original_envelope = {"case": "exact-original"}

        def request_json(_base_url, path, *, method="GET", payload=None, headers=None):
            if path == "/api/v1/composer/bootstrap":
                return {"components": []}
            if path == "/api/v1/composer/settings/observed":
                return deepcopy(observation)
            if path == "/api/v1/scene":
                return {"scene": deepcopy(original)}
            if path.endswith("/gradient/presets"):
                return {"presets": []}
            if path == "/api/composer/starters":
                return {"starters": []}
            if path == "/api/composer/looks":
                return {"looks": []}
            if path == "/api/v1/scene/checks":
                raise SweepError(
                    "POST /api/v1/scene/checks returned 400: canonical browser "
                    "scene animation managed identity is stale"
                )
            if path == "/api/v1/composer/operations/telemetry":
                return {
                    "controller": {"current_animation": "gradient"},
                    "diagnostics": {"driver_stats": {"devices": []}},
                }
            raise AssertionError((method, path, payload, headers))

        restored_receipt = {
            "requested_identity": {"scene_identity": {"digest": "f" * 64}},
            "observed_identity": {"scene_identity": {"digest": "f" * 64}},
        }
        with (
            patch(
                "tools.qualification.catalog_live_sweep._request_json",
                side_effect=request_json,
            ),
            patch(
                "tools.qualification.catalog_live_sweep.animation_components",
                return_value=[animation],
            ),
            patch(
                "tools.qualification.catalog_live_sweep.catalog_cases",
                return_value=[{
                    "case_id": "default:gradient", "kind": "default",
                    "component_id": "gradient", "scene": deepcopy(original),
                }],
            ),
            patch(
                "tools.qualification.catalog_live_sweep.browser_scene_requests",
                return_value=[candidate_envelope, original_envelope],
            ),
            patch(
                "tools.qualification.catalog_live_sweep._activate",
                side_effect=[KeyboardInterrupt(), restored_receipt],
            ) as activate,
        ):
            with self.assertRaises(KeyboardInterrupt):
                run(
                    "http://wall.invalid", hold=.25, timeout=5.0,
                    physical_wall_authorized=True,
                )

        self.assertEqual(activate.call_count, 2)
        self.assertEqual(activate.call_args_list[-1].args[1], original_envelope)
        self.assertEqual(activate.call_args_list[-1].args[2]["revision"], 7)


if __name__ == "__main__":
    unittest.main()
