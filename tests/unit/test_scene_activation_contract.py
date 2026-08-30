"""Canonical Check, activation-command, and correlated-status contracts."""

from __future__ import annotations

import unittest
from copy import deepcopy

from ipc.scene_contract import (
    BROWSER_SCENE_SCHEMA,
    COMPOSER_OPERATIONS_STATUS_SCHEMA,
    GLOBAL_SETTINGS_SCHEMA,
    SCENE_ACTIVATION_COMMAND_SCHEMA,
    SCENE_ACTIVATION_STATUS_SCHEMA,
    SceneValidationError,
    activation_identity_from_basis,
    browser_scene_to_host_scene,
    build_scene_activation_basis,
    build_composer_operations_status,
    canonical_json_bytes,
    canonical_json_sha256,
    decorate_browser_component,
    decorate_catalog,
    global_settings_digest,
    normalize_activation_controller_identity,
    normalize_activation_qualification,
    normalize_browser_scene_document,
    normalize_global_settings_payload,
    normalize_scene_activation_basis,
    normalize_scene_activation_command,
    normalize_scene_activation_status,
    scene_activation_basis_digest,
    validate_scene_activation_status_transition,
)


PROFILE_A = "a" * 64
PROFILE_B = "b" * 64
SESSION_A = "1" * 32


def _component(component_id: str, *, role: str = "background") -> dict:
    return {
        "plugin_id": component_id,
        "provider": "python",
        "role": role,
        "entrypoint": f"animation.plugins.{component_id}:Fixture",
        "parameter_schema_version": 1,
        "parameter_schema": {
            "speed": {"type": "float", "min": 0.1, "max": 5.0, "default": 1.0},
        },
        "defaults": {"speed": 1.0},
        "availability": {"state": "ready"},
        "compatibility": {"composable": True, "implementation_loaded": True},
        "build": {},
    }


def _catalog() -> list[dict]:
    result = []
    for index, component in enumerate(decorate_catalog([
        _component("gradient"),
        _component("clock_overlay", role="overlay"),
    ]), start=1):
        result.append(decorate_browser_component(
            component,
            browser_runtime={
                "kind": "python",
                "supported": True,
                "digest": f"{index:064x}",
            },
        ))
    return result


def _binding(component: dict, speed: float) -> dict:
    managed = component["browser_capabilities"]["managed_identity"]
    return {
        "provider": managed["provider"],
        "component_id": managed["component_id"],
        "component_digest": managed["component_digest"],
        "runtime_digest": managed["runtime_digest"],
        "parameter_schema_version": managed["parameter_schema_version"],
        "parameters": {"speed": speed},
    }


def _browser_scene(catalog: list[dict], profile: str = PROFILE_A) -> dict:
    by_id = {item["plugin_id"]: item for item in catalog}
    background = _binding(by_id["gradient"], 0.7)
    return {
        "schema": BROWSER_SCENE_SCHEMA,
        "schema_version": 1,
        "revision": 17,
        "background": background,
        "layers": [{
            "role": "clock",
            "component": _binding(by_id["clock_overlay"], 1.1),
            "enabled": True,
            "opacity": 220,
            "blend_mode": "source_over",
        }],
        "installation_profile": {"digest": profile},
        "fallback": deepcopy(background),
    }


def _global_settings(*, revision: int = 8) -> dict:
    return {
        "schema": GLOBAL_SETTINGS_SCHEMA,
        "schema_version": 1,
        "revision": revision,
        "vibe": {
            "vibe_id": "cozy",
            "profile_version": 1,
            "resolved_profile_digest": "c" * 64,
        },
        "plant_modifiers": {
            "version": 1,
            "active": ["obstacle", "illuminate"],
            "strengths": {"illuminate": 0.4, "obstacle": 1},
        },
        "output": {
            "power": True,
            "brightness": 96,
            "animation_speed_scale": 0.45,
            "target_fps": 120,
        },
    }


def _set_path(value: dict, path: tuple, replacement) -> None:
    target = value
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement


class SceneActivationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = _catalog()
        self.browser_scene = _browser_scene(self.catalog)
        self.settings = _global_settings()
        self.controller_runtimes = {
            "python:gradient": "d" * 64,
            "python:clock_overlay": "e" * 64,
        }
        self.basis = build_scene_activation_basis(
            browser_scene=self.browser_scene,
            catalog=self.catalog,
            global_settings=self.settings,
            controller_runtime_digests=self.controller_runtimes,
            controller_session_id=SESSION_A,
            controller_state_revision=40,
            current_identity_digest="f" * 64,
            qualification_version="server-check-v1",
            qualification_record_digest="8" * 64,
            expires_at=2_000_000,
        )
        document = normalize_browser_scene_document(
            self.browser_scene, catalog=self.catalog, purpose="activation"
        )
        self.host_scene = browser_scene_to_host_scene(
            document, catalog=self.catalog
        )
        self.command = {
            "schema": SCENE_ACTIVATION_COMMAND_SCHEMA,
            "schema_version": 1,
            "activation_id": "activation-001",
            "check_token_digest": "9" * 64,
            "basis": deepcopy(self.basis),
            "basis_digest": scene_activation_basis_digest(self.basis),
            "desired": {
                "scene": deepcopy(self.host_scene),
                "global_settings": deepcopy(self.settings),
                "installation_profile_digest": PROFILE_A,
            },
        }

    def test_canonical_json_is_order_independent_and_strict(self) -> None:
        left = {"z": [3, {"b": True, "a": None}], "a": "value"}
        right = {"a": "value", "z": [3, {"a": None, "b": True}]}
        self.assertEqual(canonical_json_bytes(left), canonical_json_bytes(right))
        self.assertEqual(canonical_json_sha256(left), canonical_json_sha256(right))
        self.assertRegex(canonical_json_sha256(left), r"^[0-9a-f]{64}$")
        for invalid in ({1: "integer key"}, {"x": float("nan")}, {"x": object()}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(SceneValidationError):
                    canonical_json_bytes(invalid)

    def test_global_settings_use_controller_units_and_canonical_plant_order(self) -> None:
        normalized = normalize_global_settings_payload(self.settings)
        self.assertEqual(normalized["output"], {
            "power": True,
            "brightness": 96,
            "animation_speed_scale": 0.45,
            "target_fps": 120,
        })
        self.assertEqual(
            normalized["plant_modifiers"]["active"],
            ["illuminate", "obstacle"],
        )
        self.assertEqual(global_settings_digest(self.settings), canonical_json_sha256(normalized))

        powered_off = deepcopy(self.settings)
        powered_off["output"]["power"] = False
        self.assertFalse(normalize_global_settings_payload(powered_off)["output"]["power"])
        self.assertNotEqual(
            global_settings_digest(powered_off), global_settings_digest(self.settings)
        )

        for path, replacement in (
            (("revision",), 9),
            (("vibe", "resolved_profile_digest"), "d" * 64),
            (("output", "brightness"), 97),
            (("output", "animation_speed_scale"), 0.5),
            (("output", "target_fps"), 121),
        ):
            candidate = deepcopy(self.settings)
            _set_path(candidate, path, replacement)
            self.assertNotEqual(
                global_settings_digest(candidate), global_settings_digest(self.settings),
                path,
            )

    def test_basis_derives_both_scene_digests_and_complete_runtime_identities(self) -> None:
        repeated = build_scene_activation_basis(
            browser_scene=deepcopy(self.browser_scene),
            catalog=deepcopy(self.catalog),
            global_settings=deepcopy(self.settings),
            controller_runtime_digests=dict(reversed(tuple(self.controller_runtimes.items()))),
            controller_session_id=SESSION_A,
            controller_state_revision=40,
            current_identity_digest="f" * 64,
            qualification_version="server-check-v1",
            qualification_record_digest="8" * 64,
            expires_at=2_000_000,
            host_scene=deepcopy(self.host_scene),
        )
        self.assertEqual(repeated, self.basis)
        self.assertEqual(
            normalize_activation_controller_identity(self.basis["controller"]),
            self.basis["controller"],
        )
        self.assertEqual(
            normalize_activation_qualification(self.basis["qualification"]),
            self.basis["qualification"],
        )
        self.assertEqual(
            [item["slot_id"] for item in self.basis["components"]],
            ["background", "clock_overlay", "known_python_fallback"],
        )
        self.assertEqual(
            self.basis["components"][0]["browser_runtime_digest"], "1".zfill(64)
        )
        self.assertEqual(
            self.basis["components"][0]["controller_runtime_digest"], "d" * 64
        )
        self.assertEqual(self.basis["installation_profile_digest"], PROFILE_A)
        self.assertEqual(
            self.basis["global_settings"]["digest"],
            global_settings_digest(self.settings),
        )

    def test_act02_every_checked_basis_mutation_invalidates_the_command(self) -> None:
        mutations = (
            (("browser_scene", "digest"), "0" * 64),
            (("host_scene", "digest"), "0" * 64),
            (("components", 0, "component_digest"), "0" * 64),
            (("components", 0, "browser_runtime_digest"), "0" * 64),
            (("components", 0, "controller_runtime_digest"), "0" * 64),
            (("installation_profile_digest",), PROFILE_B),
            (("global_settings", "digest"), "0" * 64),
            (("global_settings", "revision"), 9),
            (("controller", "session_id"), "2" * 32),
            (("controller", "state_revision"), 41),
            (("controller", "current_identity_digest"), "0" * 64),
            (("qualification", "version"), "server-check-v2"),
            (("qualification", "record_digest"), "7" * 64),
            (("qualification", "expires_at"), 2_000_001),
        )
        for path, replacement in mutations:
            with self.subTest(path=path):
                candidate = deepcopy(self.command)
                _set_path(candidate["basis"], path, replacement)
                with self.assertRaisesRegex(SceneValidationError, "basis_digest"):
                    normalize_scene_activation_command(candidate)

    def test_command_rejects_desired_state_drift_and_expired_check(self) -> None:
        mutations = (
            (("scene", "background", "parameter_overrides", "speed"), 0.8),
            (("scene", "revision"), 18),
            (("global_settings", "revision"), 9),
            (("global_settings", "output", "brightness"), 97),
            (("installation_profile_digest",), PROFILE_B),
        )
        for path, replacement in mutations:
            with self.subTest(path=path):
                candidate = deepcopy(self.command)
                _set_path(candidate["desired"], path, replacement)
                with self.assertRaisesRegex(SceneValidationError, "checked basis"):
                    normalize_scene_activation_command(candidate)

        normalized = normalize_scene_activation_command(
            self.command, now=1_999_999
        )
        self.assertEqual(normalized["check_token_digest"], "9" * 64)
        with self.assertRaisesRegex(SceneValidationError, "expired"):
            normalize_scene_activation_command(self.command, now=2_000_000)

    def test_profile01_is_identical_across_browser_basis_and_command(self) -> None:
        normalized = normalize_scene_activation_command(self.command)
        identity = activation_identity_from_basis(self.basis)
        self.assertEqual(
            self.browser_scene["installation_profile"]["digest"], PROFILE_A
        )
        self.assertEqual(self.basis["installation_profile_digest"], PROFILE_A)
        self.assertEqual(
            normalized["desired"]["installation_profile_digest"], PROFILE_A
        )
        self.assertEqual(identity["installation_profile_digest"], PROFILE_A)

    def test_every_contract_level_is_closed(self) -> None:
        identity = activation_identity_from_basis(self.basis)
        queued = self._status("queued", identity=identity)
        cases = (
            (self.settings, (), normalize_global_settings_payload),
            (self.settings, ("vibe",), normalize_global_settings_payload),
            (self.settings, ("output",), normalize_global_settings_payload),
            (self.basis, (), normalize_scene_activation_basis),
            (self.basis, ("controller",), scene_activation_basis_digest),
            (self.basis, ("qualification",), scene_activation_basis_digest),
            (self.basis, ("components", 0), scene_activation_basis_digest),
            (self.command, (), normalize_scene_activation_command),
            (self.command, ("desired",), normalize_scene_activation_command),
            (queued, (), normalize_scene_activation_status),
            (queued, ("telemetry",), normalize_scene_activation_status),
            (queued, ("rollback",), normalize_scene_activation_status),
            (queued, ("requested_identity",), normalize_scene_activation_status),
        )
        for original, path, normalizer in cases:
            with self.subTest(path=path, normalizer=normalizer.__name__):
                candidate = deepcopy(original)
                target = candidate
                for key in path:
                    target = target[key]
                target["unexpected"] = True
                with self.assertRaisesRegex(SceneValidationError, "unsupported"):
                    normalizer(candidate)

    def test_active_requires_exact_fresh_observation_and_legal_transition(self) -> None:
        identity = activation_identity_from_basis(self.basis)
        queued = normalize_scene_activation_status(
            self._status("queued", identity=identity)
        )
        preflight = self._status("preflighting", identity=identity)
        applying = self._status("applying", identity=identity)
        observing = self._status(
            "observing", identity=identity, observed=identity, after_revision=41,
            complete=True, fresh=True,
        )
        active = self._status(
            "active", identity=identity, observed=identity, after_revision=41,
            complete=True, fresh=True,
        )
        current = queued
        for next_status in (preflight, applying, observing, active):
            current = validate_scene_activation_status_transition(
                current, next_status
            )
        self.assertEqual(current["phase"], "active")

        mismatched = deepcopy(identity)
        mismatched["installation_profile_digest"] = PROFILE_B
        with self.assertRaisesRegex(SceneValidationError, "exact observed"):
            normalize_scene_activation_status(self._status(
                "active", identity=identity, observed=mismatched,
                after_revision=41, complete=True, fresh=True,
            ))
        with self.assertRaisesRegex(SceneValidationError, "complete fresh"):
            normalize_scene_activation_status(self._status(
                "active", identity=identity, observed=identity,
                after_revision=41, complete=False, fresh=False,
            ))
        with self.assertRaisesRegex(SceneValidationError, "illegal"):
            validate_scene_activation_status_transition(active, applying)

    def test_completed_rollback_may_publish_only_its_new_exact_observation(self) -> None:
        desired = activation_identity_from_basis(self.basis)
        prior = deepcopy(desired)
        prior["scene_identity"] = {"revision": 3, "digest": "7" * 64}
        active = self._status(
            "active", identity=desired, observed=desired, after_revision=41,
            complete=True, fresh=True,
        )
        rolling_back = deepcopy(active)
        rolling_back["phase"] = "rolling_back"
        rolling_back = validate_scene_activation_status_transition(
            active, rolling_back
        )
        rolled_back = deepcopy(rolling_back)
        rolled_back["phase"] = "rolled_back"
        rolled_back["observed_identity"] = prior
        rolled_back["controller"]["state_revision_after"] = 42
        rolled_back["telemetry"]["observed_at"] = 1_500_001
        rolled_back["rollback"]["result"] = "succeeded"
        receipt = validate_scene_activation_status_transition(
            rolling_back, rolled_back
        )
        self.assertEqual(receipt["observed_identity"], prior)
        self.assertEqual(receipt["controller"]["state_revision_after"], 42)

        forged = deepcopy(rolling_back)
        forged["observed_identity"] = prior
        with self.assertRaisesRegex(SceneValidationError, "outside"):
            validate_scene_activation_status_transition(rolling_back, forged)

    def test_restart_reconciliation_may_replace_only_active_without_rollback(self) -> None:
        identity = activation_identity_from_basis(self.basis)
        active = self._status(
            "active", identity=identity, observed=identity, after_revision=41,
            complete=True, fresh=True,
        )
        renewed = deepcopy(active)
        renewed["controller"]["session_id"] = "2" * 32
        renewed["controller"]["state_revision_before"] = 0
        renewed["controller"]["state_revision_after"] = 1
        renewed["telemetry"]["observed_at"] += 1
        renewed["rollback"].update(
            available=False, snapshot_id=None, result=None, error=None
        )

        receipt = validate_scene_activation_status_transition(active, renewed)
        self.assertEqual(receipt["controller"]["session_id"], "2" * 32)
        self.assertFalse(receipt["rollback"]["available"])

        stale = deepcopy(renewed)
        stale["phase"] = "failed"
        stale["error"] = "restored state does not match"
        stale["observed_identity"] = None
        stale["controller"]["state_revision_after"] = None
        stale["telemetry"] = {
            "complete": False, "fresh": False, "observed_at": None,
        }
        failed = validate_scene_activation_status_transition(active, stale)
        self.assertEqual(failed["phase"], "failed")

        same_session_failure = deepcopy(stale)
        same_session_failure["controller"]["session_id"] = SESSION_A
        with self.assertRaisesRegex(SceneValidationError, "illegal"):
            validate_scene_activation_status_transition(active, same_session_failure)

        retained_authority = deepcopy(renewed)
        retained_authority["rollback"].update(
            available=True, snapshot_id="snapshot-001"
        )
        with self.assertRaisesRegex(SceneValidationError, "controller session"):
            validate_scene_activation_status_transition(active, retained_authority)

    def test_successful_rollback_requires_exact_fresh_inactive_observation(self) -> None:
        desired = activation_identity_from_basis(self.basis)
        inactive = {
            "scene_identity": None,
            "component_identities": [],
            "global_settings_identity": {
                "revision": 4,
                "digest": "6" * 64,
            },
            "installation_profile_digest": PROFILE_B,
        }
        rolled_back = self._status(
            "rolled_back", identity=desired, observed=inactive,
            after_revision=41, complete=True, fresh=True,
        )
        rolled_back["rollback"]["result"] = "succeeded"
        receipt = normalize_scene_activation_status(rolled_back)
        self.assertIsNone(receipt["observed_identity"]["scene_identity"])
        self.assertEqual(receipt["observed_identity"]["component_identities"], [])

        for path, replacement, message in (
            (("observed_identity",), None, "exact fresh"),
            (("telemetry", "fresh"), False, "exact fresh"),
            (("controller", "state_revision_after"), 40, "advanced"),
        ):
            invalid = deepcopy(rolled_back)
            _set_path(invalid, path, replacement)
            with self.assertRaisesRegex(SceneValidationError, message):
                normalize_scene_activation_status(invalid)

        invalid_requested = deepcopy(rolled_back)
        invalid_requested["requested_identity"] = inactive
        with self.assertRaisesRegex(SceneValidationError, "must be active"):
            normalize_scene_activation_status(invalid_requested)

    def test_terminal_failures_require_and_retain_their_error(self) -> None:
        identity = activation_identity_from_basis(self.basis)
        failed = self._status("failed", identity=identity)
        with self.assertRaisesRegex(SceneValidationError, "requires an error"):
            normalize_scene_activation_status(failed)
        failed["error"] = "controller state revision changed after Check"
        normalized = normalize_scene_activation_status(failed)
        self.assertEqual(normalized["error"], failed["error"])

    def _status(
        self,
        phase: str,
        *,
        identity: dict,
        observed: dict | None = None,
        after_revision: int | None = None,
        complete: bool = False,
        fresh: bool = False,
    ) -> dict:
        return {
            "schema": SCENE_ACTIVATION_STATUS_SCHEMA,
            "schema_version": 1,
            "activation_id": "activation-001",
            "basis_digest": scene_activation_basis_digest(self.basis),
            "command_id": "command-001",
            "phase": phase,
            "error": None,
            "requested_identity": deepcopy(identity),
            "normalized_identity": deepcopy(identity),
            "observed_identity": deepcopy(observed),
            "controller": {
                "session_id": SESSION_A,
                "state_revision_before": 40,
                "state_revision_after": after_revision,
            },
            "telemetry": {
                "complete": complete,
                "fresh": fresh,
                "observed_at": 1_500_000 if fresh else None,
            },
            "rollback": {
                "available": True,
                "snapshot_id": "snapshot-001",
                "result": None,
                "error": None,
            },
            "camera_observation": None,
        }


class ComposerOperationsStatusContractTests(unittest.TestCase):
    def _status(self, **changes: object) -> dict:
        value = {
            "updated_at": 1_000.0,
            "is_running": True,
            "controller_session_id": SESSION_A,
            "controller_state_revision": 41,
            "current_identity_digest": "d" * 64,
            "active_identity": {"scene_identity": {"revision": 17, "digest": "e" * 64}},
            "target_fps": 60,
            "actual_fps": 58,
            "receiver_count": 3,
            "receiver_hybrid": {
                "healthy": True,
                "operational": True,
                "telemetry_complete": True,
                "readable_devices": [0, 1, 2],
                "unverified_devices": [],
            },
            "latest_activation": {
                "phase": "active",
                "normalized_identity": {"scene_identity": {"revision": 17, "digest": "e" * 64}},
                "controller": {"session_id": SESSION_A},
            },
        }
        value.update(changes)
        return value

    def test_stale_observation_is_unavailable_and_keeps_raw_evidence_owned(self) -> None:
        result = build_composer_operations_status(self._status(), now_ms=1_020_001)

        self.assertEqual(result["schema"], COMPOSER_OPERATIONS_STATUS_SCHEMA)
        self.assertEqual(result["observation"]["freshness"], "stale")
        self.assertEqual(result["reconciliation"]["state"], "stale")
        self.assertEqual(result["health"]["state"], "unavailable")
        self.assertEqual(result["raw_evidence"], {
            "owner": "controller_status", "url": "/api/status", "observed_at": 1_000_000,
        })

    def test_partial_receiver_evidence_is_degraded_without_exposing_driver_payload(self) -> None:
        result = build_composer_operations_status(self._status(
            receiver_hybrid={
                "operational": False,
                "degraded": True,
                "telemetry_complete": False,
                "readable_devices": [0, 2],
                "unverified_devices": [1],
                "error": "receiver 1 missed status drain",
            },
        ), now_ms=1_001_000)

        receivers = result["health"]["receivers"]
        self.assertEqual(result["health"]["state"], "degraded")
        self.assertEqual(receivers["state"], "degraded")
        self.assertEqual(receivers["connected"], [0, 2])
        self.assertEqual(receivers["missing"], [1])
        self.assertEqual(receivers["unverified"], [1])
        self.assertNotIn("driver", result)

    def test_controller_reconnect_cannot_acknowledge_an_old_activation_receipt(self) -> None:
        result = build_composer_operations_status(self._status(
            controller_session_id="2" * 32,
            controller_state_revision=1,
        ), now_ms=1_001_000)

        self.assertEqual(result["observation"]["revision"]["session_id"], "2" * 32)
        self.assertEqual(result["reconciliation"]["state"], "reconnected")
        self.assertIn("session changed", result["reconciliation"]["reason"])

    def test_output_power_is_revision_qualified_and_distinct_from_idle(self) -> None:
        powered_off = build_composer_operations_status(self._status(
            is_running=False,
            global_settings={"output": {"power": False}},
        ), now_ms=1_001_000)

        self.assertEqual(powered_off["observation"]["state"], "idle")
        self.assertEqual(powered_off["output_power"], {
            "state": "off",
            "observed": False,
            "revision": {"session_id": SESSION_A, "state_revision": 41},
            "reason": "Output is off in the fresh controller observation.",
        })

    def test_output_power_reports_pending_and_failed_activation_states(self) -> None:
        pending = build_composer_operations_status(self._status(
            latest_activation={"phase": "observing", "controller": {"session_id": SESSION_A}},
        ), now_ms=1_001_000)
        failed = build_composer_operations_status(self._status(
            latest_activation={"phase": "failed", "controller": {"session_id": SESSION_A}},
        ), now_ms=1_001_000)

        self.assertEqual(pending["output_power"]["state"], "pending")
        self.assertEqual(failed["output_power"]["state"], "failed")

    def test_fresh_mismatched_identity_is_explicitly_diverged(self) -> None:
        result = build_composer_operations_status(self._status(
            active_identity={"scene_identity": {"revision": 18, "digest": "f" * 64}},
        ), now_ms=1_001_000)

        self.assertEqual(result["reconciliation"]["state"], "diverged")
        self.assertIn("does not match", result["reconciliation"]["reason"])

if __name__ == "__main__":
    unittest.main()
