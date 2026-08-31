"""Guarded browser scene Check, activation, and receipt API contracts."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from animation.core.activation_qualification import canonical_json_sha256
from animation.core.receiver_static_component import (
    COMPILED_RAINBOW_EXPECTED_PAYLOAD_DIGEST,
    receiver_static_component_descriptor,
)
from animation.core.presentation_contracts import resolve_vibe
from ipc.control_channel import FileControlChannel
from ipc.runtime_control import manager_controller_runtime_digests
from ipc.scene_contract import component_contract_digest
from tests.unit.test_browser_scene_contract import _Manager, _document
from web.activation_token_store import ActivationTokenStore
from web.app import AnimationWebInterface


SESSION_ID = "a" * 32
RELEASE_ID = "c" * 64
ACTIVE_IDENTITY = {"current_identity": "b" * 64}
CURRENT_IDENTITY = canonical_json_sha256(ACTIVE_IDENTITY)


class _Clock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _global_settings(revision: int = 3) -> dict:
    vibe = resolve_vibe("neutral").state.to_dict()
    return {
        "schema": "ledgrid.global-settings-state",
        "schema_version": 1,
        "revision": revision,
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


def _target_transport() -> dict:
    devices = []
    for logical_device in range(5):
        fec_enabled = logical_device == 3
        full_frames = 300
        devices.append({
            "logical_device": logical_device,
            "expected_wire_bytes": (
                4088 if fec_enabled else 3320 if logical_device < 4 else 424
            ),
            "deltas": {
                "full_frame_transfers": 300,
                "full_frame_status_transfers": 2,
                "full_frame_status_samples": 2,
                "full_frame_status_sample_misses": 0,
                "full_frame_write_only_transfers": 298,
            },
            "final": {
                "receiver_status_version": 7,
                "receiver_status_max_version_seen": 7,
                "full_frame_frames_since_status_sample": logical_device,
                "full_frame_max_status_sample_gap": 255,
                "spidev_buffer_size": 4096,
                "full_frame_write_only_supported": True,
            },
            "fec": {
                "requested_count": int(fec_enabled),
                "enabled_count": int(fec_enabled),
                "deltas": {
                    "fec_frames_sent": full_frames if fec_enabled else 0,
                    "fec_codewords_sent": 68 * full_frames if fec_enabled else 0,
                    "fec_parity_bytes_sent": 730 * full_frames if fec_enabled else 0,
                    "fec_data_padding_bytes_sent": 26 * full_frames if fec_enabled else 0,
                    "receiver_fec_packets_received": full_frames if fec_enabled else 0,
                    "receiver_fec_packets_accepted": full_frames if fec_enabled else 0,
                    "receiver_fec_corrected_packets": 1 if fec_enabled else 0,
                    "receiver_fec_corrected_codewords": 1 if fec_enabled else 0,
                    "receiver_fec_uncorrectable_packets": 0,
                    "receiver_fec_semantic_crc_errors": 0,
                    "receiver_fec_framing_errors": 0,
                },
                "final": {
                    "receiver_fec_last_decode_us": 75 if fec_enabled else 0,
                    "receiver_fec_max_decode_us": 90 if fec_enabled else 0,
                },
            },
        })
    return {
        "aggregate": {
            "expected_wire_bytes": 4088,
            "deltas": {
                field: sum(device["deltas"][field] for device in devices)
                for field in devices[0]["deltas"]
            },
            "final": {
                "receiver_status_version": 7,
                "receiver_status_max_version_seen": 7,
                "full_frame_frames_since_status_sample": 4,
                "full_frame_max_status_sample_gap": 255,
                "spidev_buffer_size": 4096,
                "full_frame_write_only_supported": True,
            },
            "fec": {
                "requested_count": 1,
                "enabled_count": 1,
                "deltas": {
                    field: sum(device["fec"]["deltas"][field] for device in devices)
                    for field in devices[0]["fec"]["deltas"]
                },
                "final": {
                    "receiver_fec_last_decode_us": 75,
                    "receiver_fec_max_decode_us": 90,
                },
            },
        },
        "devices": devices,
    }


class SceneActivationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.channel = FileControlChannel(
            str(root / "control.json"),
            str(root / "status.json"),
            str(root / "activations"),
        )
        self.channel.write_status({
            "release_id": RELEASE_ID,
            "controller_session_id": SESSION_ID,
            "controller_state_revision": 7,
            "active_identity": deepcopy(ACTIVE_IDENTITY),
            "current_identity_digest": CURRENT_IDENTITY,
            "installation_profile_digest": "0" * 64,
            "brightness": 128,
            "target_fps": 30,
            "animation_speed_scale": 0.3,
            "plant_modifiers": {"version": 1, "active": [], "strengths": {}},
            "vibe": {"state": resolve_vibe("neutral").state.to_dict()},
        })
        self.interface = AnimationWebInterface(
            self.channel,
            _Manager(),
            local_mode=True,
            release_id=RELEASE_ID,
            activation_token_store_path=root / "tokens.sqlite3",
            activation_enabled=True,
        )
        self.client = self.interface.app.test_client()
        bootstrap = self.client.get("/api/v1/composer/bootstrap").get_json()
        self.scene = _document(bootstrap["components"])
        self.globals = _global_settings()

    def _check(self):
        return self.client.post("/api/v1/scene/checks", json={
            "scene": self.scene,
            "global_settings": self.globals,
            "browser_evidence": {"status": "pass", "checkerVersion": "browser-v2"},
        })

    def _numeric_browser_evidence(self) -> dict:
        return {
            "status": "pass",
            "source": "browser",
            "capturedAt": int(time.time() * 1000),
            "environment": {"userAgent": "qualification-test-browser"},
            "sampleCount": 48,
            "frameTimeMs": {"mean": 2.0, "p95": 3.0, "p99": 4.0, "max": 5.0},
            "cadence": {
                "observedFps": 30.0,
                "targetFps": 30,
                "missedFrameRatio": 0.0,
                "changedFrameRatio": 0.5,
            },
            "electrical": {
                "kind": "uncalibrated_estimate",
                "brightness": 128,
                "meanCurrentAmps": 3.0,
                "peakCurrentAmps": 4.0,
                "nominalVoltageVolts": 5.0,
            },
        }

    def test_operations_telemetry_retains_only_non_browser_contract_sections(self):
        status = self.channel.read_status()
        status.update({
            "updated_at": 1_000.0,
            "mode": "idle",
            "is_running": False,
            "performance": {"avg_frame_ms": 2.5},
            "driver_stats": {
                "aggregate": {"num_devices": 5},
                "devices": [{"receiver_logical_device": 0}],
            },
            "receiver_hybrid": {"operational": True},
            "scene": {"provider_mode": "receiver_native"},
            "scene_state": {"revision": 7},
            "latest_activation": {"phase": "active"},
        })
        self.channel.write_status(status)

        response = self.client.get("/api/v1/composer/operations/telemetry")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        payload = response.get_json()
        self.assertEqual(payload["schema"], "ledgrid.composer-operations-telemetry")
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(set(payload), {
            "schema", "schema_version", "controller", "deployment",
            "diagnostics", "calibration", "qualification", "receiver_native",
        })
        self.assertEqual(payload["controller"]["release_id"], RELEASE_ID)
        self.assertTrue(payload["controller"]["release_consistent"])
        self.assertEqual(payload["diagnostics"]["driver_stats"]["aggregate"], {
            "num_devices": 5,
        })
        self.assertEqual(payload["calibration"]["installation_profile_digest"], "0" * 64)
        self.assertEqual(payload["qualification"]["scene_state"], {"revision": 7})
        self.assertEqual(payload["receiver_native"], {"operational": True})
        self.assertNotIn("frame_data", payload["controller"])

    def test_composer_settings_observation_is_bounded_and_revision_qualified(self):
        status = self.channel.read_status()
        status.update({
            "updated_at": 1_000.0,
            "is_running": True,
            "global_settings": {"revision": 7, "output": {"power": True}},
            "frame_data": [1, 2, 3],
            "driver_stats": {"aggregate": {"num_devices": 5}},
        })
        self.channel.write_status(status)

        response = self.client.get("/api/v1/composer/settings/observed")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        payload = response.get_json()
        self.assertEqual(payload["schema"], "ledgrid.composer-settings-observation")
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(set(payload), {
            "schema", "schema_version", "observed_at",
            "controller_session_id", "controller_state_revision",
            "active_identity", "installation_profile_digest", "global_settings",
            "is_running", "brightness", "target_fps", "animation_speed_scale",
            "vibe", "plant_modifiers",
        })
        self.assertEqual(payload["controller_session_id"], SESSION_ID)
        self.assertEqual(payload["controller_state_revision"], 7)
        self.assertEqual(payload["global_settings"], {
            "revision": 7, "output": {"power": True},
        })
        self.assertNotIn("frame_data", payload)
        self.assertNotIn("driver_stats", payload)

    def test_check_uses_controller_catalog_runtime_identity_not_web_projection(self):
        checked = self._check().get_json()
        authoritative = manager_controller_runtime_digests(
            self.interface.preview_manager
        )
        for component in checked["basis"]["components"]:
            qualified_id = (
                f"{component['provider']}:{component['component_id']}"
            )
            self.assertEqual(
                component["controller_runtime_digest"],
                authoritative[qualified_id],
            )
        background = checked["basis"]["components"][0]
        self.assertNotEqual(
            background["controller_runtime_digest"],
            background["component_digest"],
        )

    def test_check_requires_exact_non_null_web_and_controller_release(self):
        original_status = self.channel.read_status()
        cases = (
            ("missing_web", None, RELEASE_ID),
            ("missing_controller", RELEASE_ID, None),
            ("mismatched", RELEASE_ID, "d" * 64),
        )
        for label, web_release, controller_release in cases:
            with self.subTest(label=label):
                status = deepcopy(original_status)
                if controller_release is None:
                    status.pop("release_id", None)
                else:
                    status["release_id"] = controller_release
                self.channel.write_status(status)
                self.interface.release_id = web_release

                response = self._check()

                self.assertEqual(response.status_code, 503)
                self.assertEqual(
                    response.get_json()["code"], "controller_state_unavailable"
                )
                with sqlite3.connect(
                    Path(self.temporary.name) / "tokens.sqlite3"
                ) as database:
                    self.assertEqual(
                        database.execute(
                            "SELECT COUNT(*) FROM activation_tokens"
                        ).fetchone()[0],
                        0,
                    )
                self.assertEqual(self.channel.list_activation_commands(), [])

    def test_runtime_authority_covers_host_and_receiver_component_roles(self):
        manager = _Manager()
        python_background = deepcopy(manager.components[0])
        python_overlay = deepcopy(manager.components[1])
        managed_native = {
            "plugin_id": "managed_native",
            "provider": "receiver_native",
            "role": "background",
            "entrypoint": "receiver_module:managed_native",
            "parameter_schema_version": 1,
            "parameter_schema": {},
            "defaults": {},
            "build": {
                "expected_payload_digest": "3" * 64,
                "bundle_digest": "4" * 64,
            },
        }
        compiled_native = receiver_static_component_descriptor({
            "receiver_local_background": True,
            "receiver_sparse_overlay": True,
        })
        self.assertIsNotNone(compiled_native)
        manager.components = [
            python_background,
            python_overlay,
            managed_native,
            compiled_native,
        ]
        interface = AnimationWebInterface(
            self.channel, manager, local_mode=True, release_id=RELEASE_ID
        )
        projected = [
            {"provider": item["provider"], "plugin_id": item["plugin_id"]}
            for item in manager.components
        ]

        identities = interface._activation_runtime_digests(projected)

        self.assertEqual(
            identities["python:gradient"],
            component_contract_digest(python_background),
        )
        self.assertEqual(
            identities["python:clock_overlay"],
            component_contract_digest(python_overlay),
        )
        self.assertEqual(identities["receiver_native:managed_native"], "3" * 64)
        self.assertEqual(
            identities["receiver_native:compiled_rainbow"],
            COMPILED_RAINBOW_EXPECTED_PAYLOAD_DIGEST,
        )

    def test_missing_or_rekeyed_runtime_authority_fails_closed(self):
        catalog = [
            {"provider": "python", "plugin_id": "gradient"},
            {"provider": "python", "plugin_id": "clock_overlay"},
        ]
        cases = (
            {},
            {
                "python:gradient": "1" * 64,
                "python:renamed_clock": "2" * 64,
            },
        )
        for authority in cases:
            with self.subTest(authority=authority), patch(
                "web.app.manager_controller_runtime_digests",
                return_value=authority,
            ):
                with self.assertRaisesRegex(
                    ValueError, "controller runtime identity is unavailable"
                ):
                    self.interface._activation_runtime_digests(catalog)

    def test_runtime_authority_only_requires_components_used_by_the_scene(self):
        catalog = [
            {"provider": "python", "plugin_id": "gradient"},
            {"provider": "receiver_native", "plugin_id": "compiled_rainbow"},
        ]
        authority = {"python:gradient": "1" * 64}
        with patch(
            "web.app.manager_controller_runtime_digests",
            return_value=authority,
        ):
            result = self.interface._activation_runtime_digests(
                catalog, required={"python:gradient"},
            )

        self.assertEqual(result, authority)

    def _activation_body(self, checked: dict, *, scene=None, globals_=None) -> dict:
        return {
            "check_token": checked["check_token"],
            "expected_controller_session_id": checked["basis"]["controller"]["session_id"],
            "expected_controller_state_revision": checked["basis"]["controller"]["state_revision"],
            "scene": self.scene if scene is None else scene,
            "global_settings": self.globals if globals_ is None else globals_,
        }

    def test_activation_is_default_off_and_advertised_honestly(self) -> None:
        root = Path(self.temporary.name) / "disabled"
        disabled = AnimationWebInterface(
            FileControlChannel(
                str(root / "control.json"),
                str(root / "status.json"),
                str(root / "activations"),
            ),
            _Manager(),
            local_mode=True,
            activation_token_store_path=root / "tokens.sqlite3",
        )
        client = disabled.app.test_client()
        connectivity = client.get("/api/v1/composer/connectivity").get_json()
        bootstrap = client.get("/api/v1/composer/bootstrap").get_json()
        self.assertFalse(connectivity["actions"]["check_scene"])
        self.assertFalse(connectivity["actions"]["activate_scene"])
        self.assertFalse(
            bootstrap["capabilities"]["server_actions"]["activation_available"]
        )
        self.assertEqual(connectivity["activation_mode"], "disabled")
        self.assertEqual(
            bootstrap["capabilities"]["server_actions"]["activation_mode"],
            "disabled",
        )
        response = client.post("/api/v1/scene/checks", json={})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["code"], "activation_unavailable")
        self.assertFalse((root / "tokens.sqlite3").exists())

    def test_explicit_activation_override_is_labeled_non_production_canary(self) -> None:
        connectivity = self.client.get(
            "/api/v1/composer/connectivity"
        ).get_json()
        self.assertRegex(connectivity["catalog_digest"], r"^[0-9a-f]{64}$")
        bootstrap = self.client.get("/api/v1/composer/bootstrap").get_json()
        self.assertEqual(connectivity["activation_mode"], "development_canary")
        self.assertEqual(
            bootstrap["capabilities"]["server_actions"]["activation_mode"],
            "development_canary",
        )

    def test_activation_catalog_can_reuse_matching_deployed_bundle(self) -> None:
        bundled = self.interface._matching_bundled_browser_catalog()

        # Test doubles intentionally differ from the deployed product catalog,
        # so their validation remains on the dynamic catalog path.
        self.assertIsNone(bundled)

    def test_check_is_read_only_and_returns_short_lived_opaque_authorization(self) -> None:
        response = self._check()

        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        payload = response.get_json()
        self.assertGreaterEqual(len(payload["check_token"]), 43)
        self.assertRegex(payload["basis_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(payload["basis"]["controller"]["state_revision"], 7)
        self.assertEqual(
            payload["basis"]["qualification"]["record_digest"],
            payload["qualification"]["record_digest"],
        )
        self.assertEqual(payload["qualification"]["status"], "development_canary")
        self.assertFalse(payload["qualification"]["production_qualified"])
        self.assertIn(
            "installation_budget_uncalibrated",
            payload["qualification"]["blockers"],
        )
        self.assertIn(
            "missing_receiver_evidence",
            payload["qualification"]["blockers"],
        )
        self.assertEqual(self.channel.list_activation_commands(), [])

    def test_numeric_browser_evidence_is_advisory_and_bound_into_check(self) -> None:
        evidence = self._numeric_browser_evidence()
        first = self.client.post("/api/v1/scene/checks", json={
            "scene": self.scene,
            "global_settings": self.globals,
            "browser_evidence": evidence,
        }).get_json()
        self.assertNotIn("missing_browser_evidence", first["qualification"]["blockers"])
        self.assertIn("missing_receiver_evidence", first["qualification"]["blockers"])

        slower = deepcopy(evidence)
        slower["frameTimeMs"] = {
            "mean": 20.0, "p95": 34.0, "p99": 35.0, "max": 36.0,
        }
        second = self.client.post("/api/v1/scene/checks", json={
            "scene": self.scene,
            "global_settings": self.globals,
            "browser_evidence": slower,
        }).get_json()
        self.assertNotEqual(
            first["qualification"]["record_digest"],
            second["qualification"]["record_digest"],
        )
        self.assertIn(
            "browser_p95_exceeds_frame_budget",
            second["qualification"]["blockers"],
        )

    def test_check_loads_only_exact_target_owned_qualification_envelope(self) -> None:
        evidence = self._numeric_browser_evidence()
        first = self.client.post("/api/v1/scene/checks", json={
            "scene": self.scene,
            "global_settings": self.globals,
            "browser_evidence": evidence,
        }).get_json()
        binding_digest = first["qualification"]["binding_digest"]
        captured_at = int(time.time() * 1000)
        target_evidence = {
            "schema": "ledgrid.target-qualification-evidence",
            "schema_version": 3,
            "revision": 1,
            "binding_digest": binding_digest,
            "captured_at": captured_at,
            "environment": "exact Raspberry Pi and five-receiver test capture",
            "runtime_identity": {
                "release_id": RELEASE_ID,
                "controller_session_id": SESSION_ID,
                "controller_state_revision": 7,
                "current_identity_digest": CURRENT_IDENTITY,
            },
            "transport": _target_transport(),
            "evidence": [],
        }
        for source in ("controller_pi", "receiver"):
            target_evidence["evidence"].append({
                "source": source,
                "binding_digest": binding_digest,
                "captured_at": captured_at,
                "environment": f"{source} exact 33x138 target",
                "sample_count": 300,
                "frame_time_ms": {
                    "mean": 2.0, "p95": 3.0, "p99": 4.0, "max": 5.0,
                },
                "cadence": {
                    "observed_fps": 30.0,
                    "missed_frame_ratio": 0.0,
                    "changed_frame_ratio": None if source == "receiver" else 1.0,
                },
                "electrical": None,
            })
        receiver = next(
            item for item in target_evidence["evidence"]
            if item["source"] == "receiver"
        )
        receiver["transport_digest"] = canonical_json_sha256(
            target_evidence["transport"]
        )
        legacy_status = self.channel.read_status()
        legacy_status["activation_qualification_evidence"] = deepcopy(
            target_evidence["evidence"]
        )
        self.channel.write_status(legacy_status)
        ignored_legacy = self.client.post("/api/v1/scene/checks", json={
            "scene": self.scene,
            "global_settings": self.globals,
            "browser_evidence": evidence,
        }).get_json()
        self.assertIn(
            "missing_controller_pi_evidence",
            ignored_legacy["qualification"]["blockers"],
        )
        self.assertIn(
            "missing_receiver_evidence",
            ignored_legacy["qualification"]["blockers"],
        )
        target_path = Path(self.temporary.name) / "target-evidence.json"
        target_path.write_text(json.dumps(target_evidence), encoding="utf-8")
        self.interface.target_qualification_evidence_path = target_path

        checked = self.client.post("/api/v1/scene/checks", json={
            "scene": self.scene,
            "global_settings": self.globals,
            "browser_evidence": evidence,
        }).get_json()

        blockers = checked["qualification"]["blockers"]
        self.assertNotIn("missing_controller_pi_evidence", blockers)
        self.assertNotIn("missing_receiver_evidence", blockers)
        self.assertNotIn("stale_controller_pi_evidence", blockers)
        self.assertTrue(checked["qualification"]["gates"]["performance"]["passed"])
        self.assertFalse(checked["qualification"]["gates"]["power"]["passed"])

        # The activation authority must commit to the exact normalized
        # transport proof, not just the accompanying cadence summaries.
        changed_transport = deepcopy(target_evidence)
        changed_transport["transport"]["devices"][0]["deltas"][
            "full_frame_transfers"
        ] += 1
        changed_transport["transport"]["devices"][0]["deltas"][
            "full_frame_write_only_transfers"
        ] += 1
        changed_transport["transport"]["aggregate"]["deltas"][
            "full_frame_transfers"
        ] += 1
        changed_transport["transport"]["aggregate"]["deltas"][
            "full_frame_write_only_transfers"
        ] += 1
        next(
            item for item in changed_transport["evidence"]
            if item["source"] == "receiver"
        )["transport_digest"] = canonical_json_sha256(
            changed_transport["transport"]
        )
        target_path.write_text(json.dumps(changed_transport), encoding="utf-8")
        changed_check = self.client.post("/api/v1/scene/checks", json={
            "scene": self.scene,
            "global_settings": self.globals,
            "browser_evidence": evidence,
        }).get_json()
        self.assertNotEqual(
            checked["qualification"]["record_digest"],
            changed_check["qualification"]["record_digest"],
        )

        for field, replacement in (
            ("release_id", "d" * 64),
            ("controller_session_id", "e" * 32),
            ("controller_state_revision", 8),
            ("current_identity_digest", "f" * 64),
        ):
            with self.subTest(replayed_identity=field):
                replayed = deepcopy(target_evidence)
                replayed["runtime_identity"][field] = replacement
                target_path.write_text(json.dumps(replayed), encoding="utf-8")
                rejected = self.client.post("/api/v1/scene/checks", json={
                    "scene": self.scene,
                    "global_settings": self.globals,
                    "browser_evidence": evidence,
                }).get_json()
                self.assertIn(
                    "missing_controller_pi_evidence",
                    rejected["qualification"]["blockers"],
                )
                self.assertIn(
                    "missing_receiver_evidence",
                    rejected["qualification"]["blockers"],
                )

        missing_revision = deepcopy(target_evidence)
        missing_revision["runtime_identity"].pop("controller_state_revision")
        target_path.write_text(json.dumps(missing_revision), encoding="utf-8")
        rejected = self.client.post("/api/v1/scene/checks", json={
            "scene": self.scene,
            "global_settings": self.globals,
            "browser_evidence": evidence,
        }).get_json()
        self.assertIn(
            "missing_controller_pi_evidence",
            rejected["qualification"]["blockers"],
        )
        self.assertIn(
            "missing_receiver_evidence",
            rejected["qualification"]["blockers"],
        )

    def test_check_rejects_unrestorable_live_legacy_before_token(self) -> None:
        token_path = Path(self.temporary.name) / "tokens.sqlite3"
        for label, live_fields in (
            ("legacy animation", {
                "is_running": True,
                "current_animation": "gradient",
            }),
        ):
            with self.subTest(label=label):
                status = self.channel.read_status()
                status.pop("scene_state", None)
                status.update(live_fields)
                self.channel.write_status(status)

                response = self._check()

                self.assertEqual(response.status_code, 409, response.get_json())
                self.assertEqual(
                    response.get_json()["code"],
                    "activation_snapshot_unavailable",
                )
                self.assertIn("cannot be restored exactly", response.get_json()["error"])
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                with sqlite3.connect(token_path) as connection:
                    token_count = connection.execute(
                        "SELECT COUNT(*) FROM activation_tokens"
                    ).fetchone()[0]
                self.assertEqual(token_count, 0)
                self.assertEqual(self.channel.list_activation_commands(), [])

    def test_removed_library_preset_route_does_not_invalidate_check(self) -> None:
        checked = self._check().get_json()
        response = self.client.post(
            "/api/animations/gradient/presets",
            json={
                "name": "Checked draft",
                "params": {
                    "speed": 0.7,
                    "map_path": "config/managed-map.json",
                },
            },
        )
        self.assertEqual(response.status_code, 404, response.get_json())
        self.assertIsNone(self.channel.read_control())
        self.assertEqual(self.channel.list_activation_commands(), [])

        activated = self.client.put(
            "/api/v1/scene",
            json=self._activation_body(checked),
            headers={"Idempotency-Key": "check-survives-preset-save"},
        )
        self.assertEqual(activated.status_code, 202, activated.get_json())

    def test_activation_requires_every_precondition_without_queue_mutation(self) -> None:
        response = self.client.put("/api/v1/scene", json={"scene": self.scene})

        self.assertEqual(response.status_code, 428)
        self.assertEqual(response.get_json()["code"], "activation_precondition_required")
        self.assertEqual(self.channel.list_activation_commands(), [])

    def test_guarded_activation_is_pending_durable_and_exactly_idempotent(self) -> None:
        checked = self._check().get_json()
        body = self._activation_body(checked)
        headers = {"Idempotency-Key": "composer-attempt-1"}

        first = self.client.put("/api/v1/scene", json=body, headers=headers)
        second = self.client.put("/api/v1/scene", json=body, headers=headers)

        self.assertEqual(first.status_code, 202, first.get_json())
        self.assertEqual(second.status_code, 202, second.get_json())
        self.assertEqual(first.get_json()["activation_id"], second.get_json()["activation_id"])
        self.assertFalse(first.get_json()["exact_retry"])
        self.assertTrue(second.get_json()["exact_retry"])
        self.assertEqual(len(self.channel.list_activation_commands()), 1)
        command = self.channel.list_activation_commands()[0]
        self.assertNotIn("check_token", command)
        self.assertRegex(command["check_token_digest"], r"^[0-9a-f]{64}$")
        self.assertNotIn(
            checked["check_token"],
            json.dumps(command, sort_keys=True),
        )
        self.assertEqual(first.headers["Location"], first.get_json()["status_url"])
        status = self.client.get(first.headers["Location"])
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.get_json()["phase"], "queued")
        self.assertIsNone(status.get_json()["observed_identity"])

    def test_exact_retry_survives_expiry_and_changed_controller_state(self) -> None:
        clock = _Clock()
        self.interface._activation_token_store = ActivationTokenStore(
            Path(self.temporary.name) / "retry.sqlite3", clock=clock
        )
        checked = self._check().get_json()
        body = self._activation_body(checked)
        headers = {"Idempotency-Key": "lost-response"}
        first = self.client.put("/api/v1/scene", json=body, headers=headers)
        activation_id = first.get_json()["activation_id"]

        active = self.channel.read_activation_status(activation_id)
        active["phase"] = "active"
        active["observed_identity"] = deepcopy(active["requested_identity"])
        active["controller"]["state_revision_after"] = 8
        active["telemetry"] = {
            "complete": True, "fresh": True, "observed_at": 1_000_001,
        }
        self.channel.activation_status_file(activation_id).write_text(
            json.dumps(active), encoding="utf-8"
        )
        controller = self.channel.read_status()
        controller["controller_state_revision"] = 8
        self.channel.write_status(controller)
        clock.value += 120

        retry = self.client.put("/api/v1/scene", json=body, headers=headers)
        self.assertEqual(retry.status_code, 202, retry.get_json())
        self.assertEqual(retry.get_json()["activation_id"], activation_id)
        self.assertTrue(retry.get_json()["exact_retry"])
        self.assertEqual(retry.get_json()["phase"], "active")
        self.assertFalse(retry.get_json()["pending"])
        self.assertEqual(len(self.channel.list_activation_commands()), 1)

    def test_exact_retry_ignores_catalog_drift_but_changed_request_conflicts(self) -> None:
        checked = self._check().get_json()
        body = self._activation_body(checked)
        headers = {"Idempotency-Key": "catalog-drift-retry"}
        first = self.client.put("/api/v1/scene", json=body, headers=headers)
        activation_id = first.get_json()["activation_id"]

        self.interface.preview_manager.components.clear()
        retry = self.client.put("/api/v1/scene", json=body, headers=headers)
        self.assertEqual(retry.status_code, 202, retry.get_json())
        self.assertEqual(retry.get_json()["activation_id"], activation_id)
        self.assertTrue(retry.get_json()["exact_retry"])

        changed = deepcopy(body)
        changed["scene"]["background"]["parameters"]["speed"] = 0.8
        conflict = self.client.put(
            "/api/v1/scene", json=changed, headers=headers
        )
        self.assertEqual(conflict.status_code, 409, conflict.get_json())
        self.assertEqual(conflict.get_json()["code"], "activation_conflict")
        self.assertEqual(len(self.channel.list_activation_commands()), 1)

    def test_durable_outbox_repairs_each_post_bind_failure(self) -> None:
        failure_points = ("status", "queue", "delivered")
        for index, failure_point in enumerate(failure_points):
            with self.subTest(failure_point=failure_point):
                checked = self._check().get_json()
                body = self._activation_body(checked)
                headers = {"Idempotency-Key": f"outbox-{failure_point}"}
                store = self.interface._activation_tokens()
                if failure_point == "status":
                    original = self.channel.write_activation_status
                    self.channel.write_activation_status = lambda _status: (_ for _ in ()).throw(OSError("injected status failure"))
                elif failure_point == "queue":
                    original = self.channel.enqueue_activation
                    self.channel.enqueue_activation = lambda _command: (_ for _ in ()).throw(OSError("injected queue failure"))
                else:
                    original = store.mark_outbox_delivered
                    store.mark_outbox_delivered = lambda _activation_id: (_ for _ in ()).throw(OSError("injected delivery failure"))
                try:
                    failed = self.client.put("/api/v1/scene", json=body, headers=headers)
                finally:
                    if failure_point == "status":
                        self.channel.write_activation_status = original
                    elif failure_point == "queue":
                        self.channel.enqueue_activation = original
                    else:
                        store.mark_outbox_delivered = original
                self.assertEqual(failed.status_code, 500, failed.get_json())
                pending = store.pending_outbox()
                self.assertTrue(pending)
                activation_id = next(
                    item.activation_id for item in pending
                    if item.idempotency_key == f"outbox-{failure_point}"
                )

                repaired = self.client.put("/api/v1/scene", json=body, headers=headers)
                self.assertEqual(repaired.status_code, 202, repaired.get_json())
                self.assertEqual(repaired.get_json()["activation_id"], activation_id)
                self.assertTrue(repaired.get_json()["exact_retry"])
                self.assertIsNotNone(self.channel.read_activation_status(activation_id))
                self.assertIsNotNone(self.channel.read_activation_command(activation_id))
                self.assertFalse(any(
                    item.activation_id == activation_id
                    for item in store.pending_outbox()
                ))

    def test_server_restart_recovers_a_bound_but_unprojected_outbox(self) -> None:
        checked = self._check().get_json()
        body = self._activation_body(checked)
        original = self.channel.write_activation_status
        self.channel.write_activation_status = lambda _status: (_ for _ in ()).throw(
            OSError("injected crash window")
        )
        try:
            failed = self.client.put(
                "/api/v1/scene", json=body,
                headers={"Idempotency-Key": "restart-recovery"},
            )
        finally:
            self.channel.write_activation_status = original
        self.assertEqual(failed.status_code, 500)
        pending = self.interface._activation_tokens().pending_outbox()
        activation_id = next(
            item.activation_id for item in pending
            if item.idempotency_key == "restart-recovery"
        )

        AnimationWebInterface(
            self.channel,
            _Manager(),
            local_mode=True,
            activation_token_store_path=Path(self.temporary.name) / "tokens.sqlite3",
            activation_enabled=True,
        )
        self.assertIsNotNone(self.channel.read_activation_status(activation_id))
        self.assertIsNotNone(self.channel.read_activation_command(activation_id))

    def test_scene_global_and_controller_changes_conflict_before_queue(self) -> None:
        mutations = []
        changed_scene = deepcopy(self.scene)
        changed_scene["background"]["parameters"]["speed"] = 0.8
        changed_scene["fallback"]["parameters"]["speed"] = 0.8
        mutations.append(("scene", changed_scene, self.globals))
        changed_globals = deepcopy(self.globals)
        changed_globals["output"]["brightness"] = 64
        mutations.append(("globals", self.scene, changed_globals))

        for label, scene, settings in mutations:
            with self.subTest(label=label):
                checked = self._check().get_json()
                response = self.client.put(
                    "/api/v1/scene",
                    json=self._activation_body(
                        checked, scene=scene, globals_=settings
                    ),
                    headers={"Idempotency-Key": f"changed-{label}"},
                )
                self.assertEqual(response.status_code, 409, response.get_json())
                self.assertEqual(self.channel.list_activation_commands(), [])

        checked = self._check().get_json()
        status = self.channel.read_status()
        status["controller_state_revision"] = 8
        self.channel.write_status(status)
        response = self.client.put(
            "/api/v1/scene",
            json=self._activation_body(checked),
            headers={"Idempotency-Key": "changed-controller"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.channel.list_activation_commands(), [])

    def test_expired_token_is_410_and_never_queues(self) -> None:
        clock = _Clock()
        self.interface._activation_token_store = ActivationTokenStore(
            Path(self.temporary.name) / "expiring.sqlite3", clock=clock
        )
        checked = self._check().get_json()
        clock.value += 120

        response = self.client.put(
            "/api/v1/scene",
            json=self._activation_body(checked),
            headers={"Idempotency-Key": "expired"},
        )
        self.assertEqual(response.status_code, 410)
        self.assertEqual(self.channel.list_activation_commands(), [])

    def test_status_cancel_rollback_and_direct_alias_contracts(self) -> None:
        checked = self._check().get_json()
        accepted = self.client.put(
            "/api/v1/scene",
            json=self._activation_body(checked),
            headers={"Idempotency-Key": "resource-contract"},
        ).get_json()
        activation_url = accepted["status_url"]

        canceled = self.client.delete(activation_url)
        self.assertEqual(canceled.status_code, 202)
        canceled_payload = canceled.get_json()
        self.assertTrue(canceled_payload["cancel_requested"])
        self.assertEqual(
            canceled.headers["Location"], canceled_payload["request_status_url"]
        )
        pending_cancel = self.client.get(
            canceled_payload["request_status_url"]
        ).get_json()
        self.assertEqual(pending_cancel["outcome"], "pending")
        self.channel.write_activation_cancel_result(
            accepted["activation_id"],
            request_id=canceled_payload["request_id"],
            outcome="succeeded",
            status_phase="failed",
        )
        self.assertEqual(
            self.client.get(canceled_payload["request_status_url"]).get_json()[
                "outcome"
            ],
            "succeeded",
        )
        rollback = self.client.post(f"{activation_url}/rollback", json={})
        self.assertEqual(rollback.status_code, 428)
        self.assertEqual(
            rollback.get_json()["code"], "activation_precondition_required"
        )

        active = self.channel.read_activation_status(accepted["activation_id"])
        active["phase"] = "active"
        active["observed_identity"] = deepcopy(active["requested_identity"])
        active["controller"]["state_revision_after"] = 8
        active["telemetry"] = {
            "complete": True, "fresh": True, "observed_at": 1_000_001,
        }
        active["rollback"] = {
            "available": True,
            "snapshot_id": "snapshot-1",
            "result": None,
            "error": None,
        }
        self.channel.activation_status_file(
            accepted["activation_id"]
        ).write_text(json.dumps(active), encoding="utf-8")
        controller = self.channel.read_status()
        controller["controller_state_revision"] = 9
        self.channel.write_status(controller)
        rollback = self.client.post(f"{activation_url}/rollback", json={
            "expected_controller_session_id": SESSION_ID,
            "expected_controller_state_revision": 8,
        })
        self.assertEqual(rollback.status_code, 409)
        self.assertIsNone(
            self.channel.read_activation_rollback(accepted["activation_id"])
        )
        controller["controller_state_revision"] = 8
        self.channel.write_status(controller)
        rollback = self.client.post(f"{activation_url}/rollback", json={
            "expected_controller_session_id": SESSION_ID,
            "expected_controller_state_revision": 8,
        })
        self.assertEqual(rollback.status_code, 202)
        rollback_payload = rollback.get_json()
        self.assertEqual(
            rollback.headers["Location"], rollback_payload["request_status_url"]
        )
        self.assertEqual(rollback_payload["snapshot_id"], "snapshot-1")
        self.assertEqual(
            self.client.get(rollback_payload["request_status_url"]).get_json()[
                "outcome"
            ],
            "pending",
        )
        retried = self.client.post(f"{activation_url}/rollback", json={
            "expected_controller_session_id": SESSION_ID,
            "expected_controller_state_revision": 8,
        })
        self.assertEqual(retried.status_code, 202)
        self.assertTrue(retried.get_json()["exact_retry"])
        self.assertEqual(
            retried.get_json()["request_id"], rollback_payload["request_id"]
        )

        aliases = (
            ("post", "/api/v1/studio-next/take-scene"),
            ("post", "/api/v1/scene-presets/example/apply"),
            ("patch", "/api/v1/scene/components/clock_overlay"),
            ("delete", "/api/v1/scene"),
        )
        for method, path in aliases:
            with self.subTest(path=path):
                response = getattr(self.client, method)(path, json={})
                if path.startswith("/api/v1/studio-next/"):
                    self.assertEqual(response.status_code, 404)
                else:
                    self.assertEqual(response.status_code, 428)
                    self.assertEqual(
                        response.get_json()["code"], "guarded_activation_required"
                    )

    def test_every_execution_alias_is_fail_closed_without_any_command(self) -> None:
        aliases = (
            ("post", "/api/v1/studio-next/take-look", {
                "provider": "python", "plugin_id": "gradient", "preset_id": "default",
            }),
            ("post", "/api/v1/studio-next/take-scene", {"scene": self.scene}),
            ("put", "/api/v1/scene", {"scene": self.scene}),
            ("post", "/api/v1/scene", {"scene": self.scene}),
            ("patch", "/api/v1/scene/components/background", {"params": {}}),
            ("post", "/api/v1/scene-presets/default/apply", {}),
            ("delete", "/api/v1/scene", {}),
        )
        for method, path, body in aliases:
            with self.subTest(method=method, path=path):
                response = getattr(self.client, method)(path, json=body)
                if path.startswith("/api/v1/studio-next/"):
                    self.assertEqual(response.status_code, 404)
                else:
                    self.assertEqual(response.status_code, 428, response.get_json())
                    self.assertIn(
                        response.get_json()["code"],
                        {"guarded_activation_required", "activation_precondition_required"},
                    )
                self.assertEqual(self.channel.list_activation_commands(), [])
                self.assertIsNone(self.channel.read_control())

    def test_removed_legacy_route_families_are_not_registered(self) -> None:
        removed = (
            ("get", "/api/animations"),
            ("get", "/api/animations/gradient"),
            ("post", "/api/animations/gradient/presets"),
            ("post", "/api/start/gradient"),
            ("post", "/api/device/state"),
            ("get", "/api/status"),
            ("get", "/api/stats"),
            ("get", "/api/metrics"),
            ("get", "/api/hardware/stats"),
            ("get", "/api/config/vibe"),
            ("post", "/api/config/plant-aware"),
            ("post", "/api/hole"),
            ("post", "/api/interaction"),
            ("get", "/api/frame"),
            ("get", "/api/preview/gradient"),
            ("post", "/api/preview/gradient/with_params"),
            ("post", "/api/parameters"),
            ("post", "/api/dpad/left"),
            ("post", "/dpad/left"),
            ("post", "/api/reload/gradient"),
            ("post", "/api/refresh"),
            ("post", "/api/v1/scene/preview"),
            ("get", "/api/v1/presets/legacy/gradient/export"),
        )
        for method, path in removed:
            with self.subTest(method=method, path=path):
                response = getattr(self.client, method)(path, json={})
                self.assertIn(response.status_code, {404, 410})
                self.assertEqual(self.channel.list_activation_commands(), [])
                self.assertIsNone(self.channel.read_control())

    def test_route_caller_and_entrypoint_inventory_has_no_retired_surface(self) -> None:
        """Keep the post-cutover route/caller scan reproducible and fail closed."""
        retained_prefixes = (
            "/api/v1/composer/",
            "/api/v1/components",
            "/api/v1/scene",
            "/api/v1/receiver-native/recover",
            "/api/v1/native-backgrounds",
            "/api/v1/receivers/status/refresh",
            "/api/v1/installation-profiles",
            "/api/v1/vibe",
            "/api/config/target-fps",
            "/api/config/animation-speed",
            "/api/config/brightness",
            "/api/config/plant-modifiers",
            "/api/stop",
        )
        registered = sorted(
            rule.rule for rule in self.interface.app.url_map.iter_rules()
            if rule.rule.startswith("/api/")
        )
        self.assertTrue(registered)
        for route in registered:
            with self.subTest(route=route):
                self.assertTrue(route.startswith(retained_prefixes))

        retired = (
            "/api/animations",
            "/api/start",
            "/api/device/state",
            "/api/status",
            "/api/stats",
            "/api/metrics",
            "/api/hardware/stats",
            "/api/config/vibe",
            "/api/config/plant-aware",
            "/api/hole",
            "/api/interaction",
            "/api/frame",
            "/api/preview",
            "/api/parameters",
            "/api/dpad",
            "/dpad/",
            "/api/reload",
            "/api/refresh",
            "/api/v1/scene/preview",
            "/api/v1/presets/legacy",
        )
        root = Path(__file__).resolve().parents[2]
        entrypoints = [
            root / "web" / "app.py",
            root / "web" / "templates" / "composer.html",
            root / "web" / "static" / "js" / "composer.js",
            root / "web" / "static" / "generated" / "composer" / "bootstrap.v1.json",
            root / "ipc" / "scene_contract.py",
        ]
        entrypoints.extend([
            root / "tools" / "deployment" / "deploy.sh",
            root / "tools" / "deployment" / "deploy_python.sh",
            root / "tools" / "deployment" / "deploy_target.py",
            root / "tools" / "deployment" / "native_background_entrypoint.py",
            root / "tools" / "diagnostics" / "receiver_dispatch_order.py",
            root / "tools" / "diagnostics" / "receiver_phase_lane_isolation.py",
            root / "tools" / "diagnostics" / "remote_diagnostics.sh",
            root / "tools" / "qualification" / "target_evidence.py",
            root / "tools" / "benchmarks" / "guarded_wall_soak.py",
            root / "tools" / "benchmarks" / "live_display_state.py",
            root / "tools" / "benchmarks" / "output_rate_sweep.py",
            root / "tools" / "benchmarks" / "receiver_acceptance.py",
            root / "tools" / "benchmarks" / "receiver_native_physical_acceptance.py",
        ])
        rollback_compatibility = root / "tools" / "deployment" / "deploy_target.py"
        for path in entrypoints:
            source = path.read_text(encoding="utf-8")
            for route in retired:
                with self.subTest(entrypoint=path.relative_to(root), route=route):
                    if path == rollback_compatibility and route == "/api/status":
                        self.assertEqual(source.count(route), 1)
                        self.assertIn("allow_legacy_status_fallback", source)
                        continue
                    self.assertNotIn(route, source)


if __name__ == "__main__":
    unittest.main()
