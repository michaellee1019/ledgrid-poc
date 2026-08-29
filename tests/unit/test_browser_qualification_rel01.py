"""Fail-closed contracts for retained REL-01 portable browser evidence."""

from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from animation.component_parameters import SCENE_EXTERNAL_COMPONENT_PARAMETERS
from animation.core.activation_qualification import canonical_json_sha256
from animation.core.presentation_contracts import resolve_vibe
from tools.browser_qualification.evidence import (
    EVIDENCE_SCHEMA,
    aggregate_evidence,
    load_manifest,
    run_qualification,
    validate_engine_result,
)
from tools.browser_qualification.fixture_server import (
    OFFLINE_GATE_COOKIE,
    WallMutationAttempt,
    create_fixture_server,
)
from tools.browser_qualification.source_identity import fixture_release_id


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "tools" / "browser_qualification" / "rel01_manifest.json"
NOW = "2026-08-28T01:00:00.000Z"
LATER = "2026-08-28T01:01:00.000Z"


def _source(*, dirty: bool = False) -> dict:
    return {"commit": "a" * 40, "working_tree_dirty": dirty}


def _engine_result(engine: str, manifest: dict) -> dict:
    journeys = []
    for journey_id, contract in manifest["journeys"].items():
        journey = {
            "journey_id": journey_id,
            "outcome": "PASS",
            "assertions": [
                {
                    "assertion_id": assertion_id,
                    "passed": True,
                    "detail": "deterministic test observation",
                }
                for assertion_id in contract["required_assertions"]
            ],
        }
        if "viewport" in contract:
            journey["viewport"] = deepcopy(contract["viewport"])
        else:
            journey["viewports"] = deepcopy(contract["viewports"])
            journey["viewport_observations"] = [
                {
                    **deepcopy(viewport),
                    "outcome": "PASS",
                    "assertions": [
                        {
                            "assertion_id": assertion_id,
                            "passed": True,
                            "detail": "deterministic viewport observation",
                        }
                        for assertion_id in contract["required_viewport_assertions"]
                    ],
                }
                for viewport in contract["viewports"]
            ]
        journeys.append(journey)
    return {
        "requested_engine": engine,
        "reported_engine": engine,
        "browser_version": f"test-{engine}-1",
        "playwright_version": manifest["playwright_version"],
        "offline_strategy": manifest["offline_strategies"][engine],
        "executed": True,
        "started_at": NOW,
        "completed_at": LATER,
        "outcome": "PASS",
        "journeys": journeys,
        "fixture_status": {
            "schema": "ledgrid.browser-qualification-fixture-status",
            "schema_version": 2,
            "profile_digest": "b" * 64,
            "native_plugin_id": "aurora_curtains_native",
            "native_bundle_digest": "c" * 64,
            "native_payload_digest": "d" * 64,
            "source_commit": "a" * 40,
            "release_id": fixture_release_id("a" * 40),
            "controller_release_id": fixture_release_id("a" * 40),
            "release_consistent": True,
            "network_outage_blocks": 1 if engine == "webkit" else 0,
            "network_outage_paths": ["/composer"] if engine == "webkit" else [],
            "wall_mutation_attempts": 0,
            "wall_consumer_attached": False,
        },
    }


class BrowserQualificationRel01Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = load_manifest(MANIFEST_PATH)
        self.results = {
            engine: _engine_result(engine, self.manifest)
            for engine in self.manifest["required_engines"]
        }

    def aggregate(self, *, source: dict | None = None, results: dict | None = None) -> dict:
        return aggregate_evidence(
            manifest=self.manifest,
            source=source or _source(),
            engine_results=results or self.results,
            started_at=NOW,
            completed_at=LATER,
        )

    def test_manifest_requires_all_engines_journeys_and_separate_claim_classes(self) -> None:
        package = json.loads(
            (ROOT / "tools/browser_qualification/package.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            package["dependencies"]["playwright"],
            self.manifest["playwright_version"],
        )
        self.assertEqual(
            self.manifest["required_engines"], ["chromium", "firefox", "webkit"]
        )
        self.assertEqual(
            self.manifest["offline_strategies"],
            {
                "chromium": "native_network_offline",
                "firefox": "fixture_origin_outage",
                "webkit": "fixture_origin_outage",
            },
        )
        self.assertEqual(
            self.manifest["service_worker_upgrade"],
            {
                "previous_cache": "ledgrid-composer-shell-v18",
                "current_cache": "ledgrid-composer-shell-v19",
            },
        )
        self.assertEqual(
            set(self.manifest["journeys"]),
            {
                "core_no_mutation",
                "offline_reconnect",
                "worker_recovery",
                "responsive_layouts",
                "keyboard_only_desktop",
                "global_controls",
                "profile_masks",
                "python_native_clock",
            },
        )
        self.assertEqual(
            self.manifest["journeys"]["offline_reconnect"]["viewport"],
            {"width": 390, "height": 844},
        )
        self.assertIn(
            "forbidden_wall_requests_zero",
            self.manifest["journeys"]["core_no_mutation"]["required_assertions"],
        )
        self.assertIn(
            "saved_identity_invalidated_prior_check",
            self.manifest["journeys"]["core_no_mutation"]["required_assertions"],
        )
        self.assertIn(
            "saved_identity_check_completed",
            self.manifest["journeys"]["core_no_mutation"]["required_assertions"],
        )
        self.assertIn(
            "stale_activation_rejected_before_queue",
            self.manifest["journeys"]["offline_reconnect"]["required_assertions"],
        )
        self.assertIn(
            "previous_cache_generation_upgraded",
            self.manifest["journeys"]["offline_reconnect"]["required_assertions"],
        )
        self.assertIn(
            "renderer_catalog_cached",
            self.manifest["journeys"]["offline_reconnect"]["required_assertions"],
        )
        self.assertIn(
            "offline_network_isolation_enforced",
            self.manifest["journeys"]["offline_reconnect"]["required_assertions"],
        )
        self.assertIn(
            "service_worker_controls_composer",
            self.manifest["journeys"]["offline_reconnect"]["required_assertions"],
        )
        self.assertIn(
            "opening_wall_state_reads_zero",
            self.manifest["journeys"]["offline_reconnect"]["required_assertions"],
        )
        self.assertIn(
            "reconnect_preserved_local_draft",
            self.manifest["journeys"]["offline_reconnect"]["required_assertions"],
        )
        self.assertIn(
            "bounded_worker_restart",
            self.manifest["journeys"]["worker_recovery"]["required_assertions"],
        )
        self.assertEqual(
            [
                (item["width"], item["height"])
                for item in self.manifest["journeys"]["responsive_layouts"]["viewports"]
            ],
            [(375, 667), (390, 844), (430, 932), (768, 1024), (1440, 1000)],
        )
        self.assertEqual(
            self.manifest["journeys"]["keyboard_only_desktop"]["viewport"],
            {"width": 1440, "height": 1000},
        )
        self.assertEqual(
            self.manifest["journeys"]["keyboard_only_desktop"]["background_name"],
            "Color Gradient",
        )
        self.assertTrue(
            {
                "skip_link_reached_composer",
                "parameter_keyboard_edit_undo_redo",
                "tablist_arrow_home_end_navigation",
                "activation_dialog_keyboard_focus_contained",
                "activation_review_cancelled_by_keyboard",
            }.issubset(
                self.manifest["journeys"]["keyboard_only_desktop"][
                    "required_assertions"
                ]
            )
        )
        self.assertEqual(
            self.manifest["journeys"]["profile_masks"]["engine_led_offsets"],
            {"chromium": 0, "firefox": 1, "webkit": 2},
        )
        self.assertEqual(
            self.manifest["journeys"]["python_native_clock"][
                "managed_native_ineligibility_reason"
            ],
            "The managed host implementation is not loaded.",
        )
        self.assertEqual(
            self.manifest["journeys"]["python_native_clock"][
                "python_background_name"
            ],
            "Color Gradient",
        )
        self.assertIn(
            "managed_native_host_ineligibility_declared",
            self.manifest["journeys"]["python_native_clock"][
                "required_assertions"
            ],
        )
        self.assertTrue(
            {
                "physical_iphone_safari",
                "ios_installed_standalone",
                "voiceover",
                "physical_wall_output",
                "controller_or_receiver_performance",
                "electrical_safety",
            }.issubset(self.manifest["excluded_claims"])
        )

    def test_complete_clean_matrix_passes_portable_lane_but_not_rel01_gate(self) -> None:
        evidence = self.aggregate()

        self.assertEqual(evidence["schema"], EVIDENCE_SCHEMA)
        self.assertEqual(evidence["git_commit"], "a" * 40)
        self.assertEqual(evidence["outcomes"]["portable_browser_matrix"], "PASS")
        self.assertEqual(
            evidence["outcomes"]["rel01_release_gate"],
            "PENDING_EXTERNAL_EVIDENCE",
        )
        self.assertFalse(evidence["release_gate_satisfied"])
        self.assertTrue(all(not item["validation_errors"] for item in evidence["results"]))

    def test_missing_engine_cannot_report_pass(self) -> None:
        results = dict(self.results)
        del results["webkit"]

        evidence = self.aggregate(results=results)

        self.assertEqual(evidence["outcomes"]["portable_browser_matrix"], "FAIL")
        webkit = next(item for item in evidence["results"] if item.get("requested_engine") != "chromium" and item.get("requested_engine") != "firefox")
        self.assertIn("engine_not_executed", webkit["validation_errors"])
        self.assertIn("journeys_missing", webkit["validation_errors"])

    def test_engine_identity_version_and_execution_are_required(self) -> None:
        result = deepcopy(self.results["firefox"])
        result["reported_engine"] = "chromium"
        result["browser_version"] = ""
        result["playwright_version"] = "0.0.0"
        result["executed"] = False

        errors = validate_engine_result(result, "firefox", self.manifest)

        self.assertIn("reported_engine_mismatch", errors)
        self.assertIn("browser_version_missing", errors)
        self.assertIn("playwright_version_mismatch", errors)
        self.assertIn("engine_not_executed", errors)

    def test_fixture_wall_attempt_or_consumer_cannot_report_pass(self) -> None:
        result = deepcopy(self.results["chromium"])
        result["fixture_status"]["wall_mutation_attempts"] = 1
        result["fixture_status"]["wall_consumer_attached"] = True
        result["fixture_status"]["native_payload_digest"] = "not-a-digest"

        errors = validate_engine_result(result, "chromium", self.manifest)

        self.assertIn("fixture_wall_mutation_attempted", errors)
        self.assertIn("fixture_wall_consumer_attached", errors)
        self.assertIn("fixture_native_payload_digest_invalid", errors)

    def test_fixture_release_identity_and_source_commit_are_required(self) -> None:
        result = deepcopy(self.results["chromium"])
        result["fixture_status"]["release_consistent"] = False
        result["fixture_status"]["controller_release_id"] = "f" * 64
        result["fixture_status"]["source_commit"] = "b" * 40

        evidence = self.aggregate(
            results={**self.results, "chromium": result}
        )
        errors = evidence["results"][0]["validation_errors"]

        self.assertIn("fixture_release_inconsistent", errors)
        self.assertIn("fixture_release_identity_mismatch", errors)
        self.assertIn("fixture_release_id_not_source_bound", errors)
        self.assertIn("fixture_source_commit_mismatch", errors)
        self.assertEqual(evidence["outcomes"]["portable_browser_matrix"], "FAIL")

    def test_skipped_assertion_wrong_viewport_and_declared_pass_fail_closed(self) -> None:
        result = deepcopy(self.results["webkit"])
        journey = next(item for item in result["journeys"] if item["journey_id"] == "offline_reconnect")
        journey["viewport"]["width"] = 391
        journey["assertions"] = [
            item
            for item in journey["assertions"]
            if item["assertion_id"] != "offline_export_completed"
        ]

        errors = validate_engine_result(result, "webkit", self.manifest)

        self.assertIn("viewport_mismatch:offline_reconnect", errors)
        self.assertIn("missing_assertion:offline_reconnect:offline_export_completed", errors)

    def test_failed_or_duplicate_assertion_cannot_be_hidden_by_engine_outcome(self) -> None:
        result = deepcopy(self.results["chromium"])
        assertion = result["journeys"][0]["assertions"][0]
        assertion["passed"] = False
        result["journeys"][0]["assertions"].append(deepcopy(assertion))

        errors = validate_engine_result(result, "chromium", self.manifest)

        self.assertTrue(any(item.startswith("duplicate_assertion:") for item in errors))
        self.assertTrue(any(item.startswith("failed_assertion:") for item in errors))

    def test_dirty_worktree_cannot_be_bound_to_a_commit_as_pass_evidence(self) -> None:
        evidence = self.aggregate(source=_source(dirty=True))

        self.assertEqual(evidence["outcomes"]["portable_browser_matrix"], "FAIL")
        self.assertIn("working_tree_not_clean", evidence["source_validation_errors"])

    def test_malformed_commit_cannot_report_pass(self) -> None:
        evidence = self.aggregate(
            source={"commit": "z" * 40, "working_tree_dirty": False}
        )

        self.assertEqual(evidence["outcomes"]["portable_browser_matrix"], "FAIL")
        self.assertIn("git_commit_invalid", evidence["source_validation_errors"])

    def test_missing_responsive_viewport_observation_fails_closed(self) -> None:
        result = deepcopy(self.results["firefox"])
        journey = next(
            item for item in result["journeys"] if item["journey_id"] == "responsive_layouts"
        )
        journey["viewport_observations"] = journey["viewport_observations"][:-1]

        errors = validate_engine_result(result, "firefox", self.manifest)

        self.assertIn(
            "viewport_observation_missing:responsive_layouts:desktop", errors
        )
        self.assertIn("viewport_observation_set_mismatch:responsive_layouts", errors)

    def test_unavailable_playwright_retains_failure_record_for_every_engine(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "rel01.json"
            evidence = run_qualification(
                base_url="http://127.0.0.1:1",
                output_path=output,
                playwright_module=Path(temporary) / "missing-playwright",
                source_provider=lambda: _source(),
            )

            retained = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(evidence, retained)
            self.assertEqual(retained["outcomes"]["portable_browser_matrix"], "FAIL")
            self.assertEqual(len(retained["results"]), 3)
            self.assertTrue(all(item["executed"] is False for item in retained["results"]))
            self.assertTrue(all(item["outcome"] == "FAIL" for item in retained["results"]))


class BrowserQualificationFixtureServerTests(unittest.TestCase):
    @staticmethod
    def _component_reference(component: dict) -> dict:
        identity = component["browser_capabilities"]["managed_identity"]
        parameters = {
            name: deepcopy(contract["default"])
            for name, contract in component["parameter_schema"].items()
            if name not in SCENE_EXTERNAL_COMPONENT_PARAMETERS
            and "default" in contract
        }
        return {
            "provider": identity["provider"],
            "component_id": identity["component_id"],
            "component_digest": identity["component_digest"],
            "runtime_digest": identity["runtime_digest"],
            "parameter_schema_version": identity["parameter_schema_version"],
            "parameters": parameters,
        }

    @staticmethod
    def _global_settings() -> dict:
        vibe = resolve_vibe("neutral").state.to_dict()
        return {
            "schema": "ledgrid.global-settings-state",
            "schema_version": 1,
            "revision": 1,
            "vibe": {
                "vibe_id": vibe["vibe_id"],
                "profile_version": vibe["profile_version"],
                "resolved_profile_digest": vibe["resolved_profile_digest"],
            },
            "plant_modifiers": {"version": 1, "active": [], "strengths": {}},
            "output": {
                "power": True,
                "brightness": 128,
                "animation_speed_scale": 0.3,
                "target_fps": 30,
            },
        }

    def test_fixture_uses_managed_profile_and_rejects_every_wall_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary)
            interface, channel, digest = create_fixture_server(state_dir)
            client = interface.app.test_client()

            bootstrap = client.get("/api/v1/composer/bootstrap")
            self.assertEqual(bootstrap.status_code, 200)
            payload = bootstrap.get_json()
            self.assertEqual(payload["installation_profile"]["digest"], digest)
            self.assertTrue(
                payload["capabilities"]["server_actions"]["activation_available"]
            )
            native = next(
                component
                for component in payload["components"]
                if component["key"] == "receiver_native:aurora_curtains_native"
            )
            self.assertFalse(native["browser_capabilities"]["activation_ready"])
            self.assertEqual(
                native["browser_capabilities"]["reason"],
                "The managed host implementation is not loaded.",
            )
            self.assertRegex(
                native["browser_capabilities"]["managed_identity"]["bundle_digest"],
                r"^[0-9a-f]{64}$",
            )
            self.assertRegex(
                native["browser_capabilities"]["managed_identity"]["expected_payload_digest"],
                r"^[0-9a-f]{64}$",
            )
            artifact = client.get(
                f"/api/v1/installation-profiles/{digest}/artifact"
            )
            self.assertEqual(artifact.status_code, 200)
            self.assertEqual(artifact.data, (ROOT / "tests/fixtures/installation_profile_v1.bin").read_bytes())
            status = client.get("/__qualification__/status").get_json()
            self.assertFalse(status["wall_consumer_attached"])
            self.assertEqual(status["wall_mutation_attempts"], 0)
            self.assertEqual(status["native_plugin_id"], "aurora_curtains_native")
            self.assertEqual(status["network_outage_blocks"], 0)
            self.assertRegex(status["source_commit"], r"^[0-9a-f]{40}$")
            self.assertRegex(status["release_id"], r"^[0-9a-f]{64}$")
            self.assertEqual(
                status["release_id"], fixture_release_id(status["source_commit"])
            )
            self.assertEqual(status["controller_release_id"], status["release_id"])
            self.assertTrue(status["release_consistent"])
            controller_status = channel.read_status()
            self.assertEqual(controller_status["release_id"], status["release_id"])
            self.assertEqual(interface.release_id, status["release_id"])
            self.assertEqual(
                controller_status["current_identity_digest"],
                canonical_json_sha256(controller_status["active_identity"]),
            )
            self.assertEqual(
                status["native_bundle_digest"],
                native["browser_capabilities"]["managed_identity"]["bundle_digest"],
            )

            client.set_cookie(OFFLINE_GATE_COOKIE, "1")
            blocked = client.get("/composer")
            self.assertEqual(blocked.status_code, 503)
            self.assertEqual(blocked.headers["X-LEDGrid-Qualification-Offline"], "1")
            client.delete_cookie(OFFLINE_GATE_COOKIE)
            status = client.get("/__qualification__/status").get_json()
            self.assertEqual(status["network_outage_blocks"], 1)
            self.assertEqual(status["network_outage_paths"], ["/composer"])

            with self.assertRaises(WallMutationAttempt):
                channel.send_command("set_brightness", brightness=1)
            retained = [
                json.loads(line)
                for line in channel.attempt_log.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(retained[0]["operation"], "send_command")
            self.assertFalse(channel.control_path.exists())

    def test_fixture_guarded_check_has_coherent_release_and_runtime_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            interface, channel, digest = create_fixture_server(Path(temporary))
            client = interface.app.test_client()
            bootstrap = client.get("/api/v1/composer/bootstrap").get_json()
            components = {
                item["plugin_id"]: item for item in bootstrap["components"]
            }
            background = self._component_reference(components["gradient"])
            scene = {
                "schema": "ledgrid.browser-scene",
                "schema_version": 1,
                "revision": 1,
                "background": background,
                "layers": [],
                "installation_profile": {"digest": digest},
                "fallback": deepcopy(background),
            }

            checked = client.post(
                "/api/v1/scene/checks",
                json={
                    "scene": scene,
                    "global_settings": self._global_settings(),
                    "browser_evidence": None,
                },
            )

            self.assertEqual(checked.status_code, 201, checked.get_json())
            result = checked.get_json()
            status = client.get("/__qualification__/status").get_json()
            self.assertEqual(
                result["basis"]["controller"]["current_identity_digest"],
                channel.read_status()["current_identity_digest"],
            )
            self.assertEqual(interface.release_id, status["release_id"])
            self.assertRegex(result["check_token"], r"^[-_A-Za-z0-9]+$")
            self.assertEqual(channel.attempts, [])
            self.assertEqual(channel.list_activation_commands(), [])

            interface.release_id = "f" * 64
            rejected = client.post(
                "/api/v1/scene/checks",
                json={
                    "scene": scene,
                    "global_settings": self._global_settings(),
                    "browser_evidence": None,
                },
            )
            self.assertEqual(rejected.status_code, 503)
            self.assertEqual(
                rejected.get_json()["code"], "controller_state_unavailable"
            )
            self.assertEqual(channel.attempts, [])


if __name__ == "__main__":
    unittest.main()
