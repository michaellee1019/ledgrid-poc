"""Fail-closed contracts for retained physical-iPhone REL-01 evidence."""

from __future__ import annotations

import copy
import hashlib
import json
import struct
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from tools.browser_qualification.evidence import load_manifest
from tools.browser_qualification.external_iphone_evidence import (
    CAPTURE_SCHEMA,
    REQUIRED_VOICEOVER_OBSERVATIONS,
    _canonical_json,
    _runtime_bindings,
    capture_template,
    retain_capture,
    validate_capture,
)
from tools.browser_qualification.source_identity import fixture_release_id


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "tools/browser_qualification/rel01_manifest.json"
BOOTSTRAP_PATH = ROOT / "web/static/generated/composer/bootstrap.v1.json"
COMMIT = "a" * 40
NOW = datetime(2026, 8, 29, 14, 0, tzinfo=timezone.utc)


class ExternalIPhoneEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = load_manifest(MANIFEST_PATH)
        self.bindings = _runtime_bindings(BOOTSTRAP_PATH)
        self.source = {"commit": COMMIT, "working_tree_dirty": False}
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.capture = self._capture()

    def _artifact_record(self, path: Path) -> dict:
        return {
            "path": path.name,
            "byte_count": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    def _write_har(self, session_id: str, captured_at: str) -> Path:
        path = self.directory / f"{session_id}.har"
        path.write_text(
            json.dumps(
                {
                    "log": {
                        "version": "1.2",
                        "creator": {"name": "Safari Web Inspector", "version": "26"},
                        "entries": [
                            {
                                "startedDateTime": captured_at,
                                "request": {
                                    "method": "GET",
                                    "url": f"https://fixture.invalid/composer?session={session_id}",
                                },
                            }
                        ],
                    }
                }
            ),
            encoding="utf-8",
        )
        return path

    def _write_png(self, session_id: str, color: tuple[int, int, int]) -> Path:
        path = self.directory / f"{session_id}.png"
        Image.new("RGB", (2, 2), color).save(path, format="PNG")
        return path

    def _write_wav(self) -> Path:
        path = self.directory / "voiceover.wav"
        frames = b"\0\0" * 80
        fmt = struct.pack("<HHIIHH", 1, 1, 8_000, 16_000, 2, 16)
        path.write_bytes(
            b"RIFF"
            + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(frames))
            + b"WAVEfmt "
            + struct.pack("<I", len(fmt))
            + fmt
            + b"data"
            + struct.pack("<I", len(frames))
            + frames
        )
        return path

    def _fixture_status(self) -> dict:
        release_id = fixture_release_id(COMMIT)
        return {
            "schema": "ledgrid.browser-qualification-fixture-status",
            "schema_version": 2,
            "profile_digest": self.bindings["profile_digest"],
            "native_plugin_id": "aurora_curtains_native",
            "native_bundle_digest": "b" * 64,
            "native_payload_digest": "c" * 64,
            "source_commit": COMMIT,
            "release_id": release_id,
            "controller_release_id": release_id,
            "release_consistent": True,
            "network_outage_blocks": 1,
            "network_outage_paths": ["/composer"],
            "wall_mutation_attempts": 0,
            "wall_consumer_attached": False,
        }

    @staticmethod
    def _assertions(ids: list[str]) -> list[dict]:
        return [
            {"assertion_id": assertion_id, "passed": True, "detail": "observed"}
            for assertion_id in ids
        ]

    def _journeys(self) -> list[dict]:
        journeys: list[dict] = []
        for journey_id, contract in self.manifest["journeys"].items():
            journey = {
                "journey_id": journey_id,
                "outcome": "PASS",
                "assertions": self._assertions(contract["required_assertions"]),
            }
            if "viewport" in contract:
                journey["viewport"] = copy.deepcopy(contract["viewport"])
            else:
                journey["viewports"] = copy.deepcopy(contract["viewports"])
                journey["viewport_observations"] = [
                    {
                        **copy.deepcopy(viewport),
                        "outcome": "PASS",
                        "assertions": self._assertions(
                            contract["required_viewport_assertions"]
                        ),
                    }
                    for viewport in contract["viewports"]
                ]
            journeys.append(journey)
        return journeys

    def _session(self, session_id: str) -> dict:
        fixture = self._fixture_status()
        timing = {
            "safari": (
                "2026-08-29T13:00:00Z",
                "2026-08-29T13:10:00Z",
                "2026-08-29T13:01:00Z",
            ),
            "installed_standalone": (
                "2026-08-29T13:15:00Z",
                "2026-08-29T13:25:00Z",
                "2026-08-29T13:16:00Z",
            ),
            "voiceover": (
                "2026-08-29T13:30:00Z",
                "2026-08-29T13:40:00Z",
                "2026-08-29T13:31:00Z",
            ),
        }
        started_at, completed_at, captured_at = timing[session_id]
        trace = self._write_har(session_id, captured_at)
        screenshot = self._write_png(
            session_id,
            {
                "safari": (255, 0, 0),
                "installed_standalone": (0, 255, 0),
                "voiceover": (0, 0, 255),
            }[session_id],
        )
        session = {
            "session_id": session_id,
            "started_at": started_at,
            "completed_at": completed_at,
            "outcome": "PASS",
            "navigator_standalone": session_id == "installed_standalone",
            "safari_chrome_visible": session_id != "installed_standalone",
            "home_screen_install_present": session_id == "installed_standalone",
            "voiceover": {"enabled": False, "observations": []},
            "runtime_bindings": copy.deepcopy(self.bindings),
            "console_results": {"unexpected_errors": []},
            "network_results": {
                "session_id": session_id,
                "trace_format": "har",
                "trace_path": trace.name,
                "trace_byte_count": trace.stat().st_size,
                "trace_sha256": hashlib.sha256(trace.read_bytes()).hexdigest(),
            },
            "fixture_status_before": copy.deepcopy(fixture),
            "fixture_status_after": copy.deepcopy(fixture),
            "journeys": self._journeys(),
            "media": [
                {
                    "session_id": session_id,
                    "media_type": "screenshot",
                    "format": "png",
                    "captured_at": captured_at,
                    **self._artifact_record(screenshot),
                }
            ],
        }
        if session_id == "voiceover":
            session["surface"] = "safari"
            session["navigator_standalone"] = False
            session["safari_chrome_visible"] = True
            session["voiceover"] = {
                "enabled": True,
                "observations": self._assertions(list(REQUIRED_VOICEOVER_OBSERVATIONS)),
            }
            audio = self._write_wav()
            session["media"].append(
                {
                    "session_id": session_id,
                    "media_type": "audio_recording",
                    "format": "wav",
                    "captured_at": "2026-08-29T13:32:00Z",
                    **self._artifact_record(audio),
                }
            )
        return session

    def _capture(self) -> dict:
        return {
            "schema": CAPTURE_SCHEMA,
            "schema_version": 1,
            "gate": "REL-01",
            "disposition": "EXECUTED",
            "qualification_credit_requested": True,
            "source_binding": {
                "git_commit": COMMIT,
                "release_id": fixture_release_id(COMMIT),
                "manifest_sha256": hashlib.sha256(
                    _canonical_json(self.manifest)
                ).hexdigest(),
            },
            "runtime_bindings": copy.deepcopy(self.bindings),
            "device": {
                "device_class": "physical_iphone",
                "simulator": False,
                "continuity_camera_only": False,
                "available": True,
                "paired": True,
                "safari_web_inspector_target": True,
                "coredevice_state": "available",
                "connection_transport": "usb",
                "name": "Qualification iPhone",
                "model_name": "iPhone 16 Pro Max",
                "model_identifier": "iPhone17,2",
                "udid": "00008140-0005344C3433001C",
                "ios_version": "26.0.1",
                "ios_build": "23A355",
                "safari_version": "26.0",
                "webkit_version": "620.1.1",
            },
            "sessions": [
                self._session(item)
                for item in ("safari", "installed_standalone", "voiceover")
            ],
        }

    def _validate(
        self, capture: dict | None = None, *, source: dict | None = None
    ) -> dict:
        return validate_capture(
            capture or self.capture,
            manifest=self.manifest,
            source=source or self.source,
            artifact_base=self.directory,
            bootstrap_path=BOOTSTRAP_PATH,
            now=NOW,
        )

    def test_complete_physical_capture_passes_external_lane_only(self) -> None:
        retained = self._validate()

        self.assertEqual(retained["outcome"], "PASS")
        self.assertTrue(retained["external_evidence_satisfied"])
        self.assertFalse(retained["release_gate_satisfied"])
        self.assertEqual(retained["validation_errors"], [])
        self.assertEqual(
            retained["derived_session_artifacts"]["safari"]["network"]["request_count"],
            1,
        )
        self.assertEqual(
            retained["derived_session_artifacts"]["voiceover"]["media"][1][
                "stream_types"
            ],
            ["audio"],
        )

    def test_simulator_continuity_camera_and_waiver_never_pass(self) -> None:
        cases = (
            ("simulator", True),
            ("continuity_camera_only", True),
            ("device_class", "simulator"),
        )
        for field, value in cases:
            with self.subTest(field=field):
                capture = copy.deepcopy(self.capture)
                capture["device"][field] = value
                retained = self._validate(capture)
                self.assertEqual(retained["outcome"], "FAIL")
                self.assertFalse(retained["external_evidence_satisfied"])

        waived = copy.deepcopy(self.capture)
        waived["disposition"] = "OPERATOR_WAIVED"
        self.assertIn(
            "capture_not_executed", self._validate(waived)["validation_errors"]
        )
        self.assertIn(
            "operator_waiver_present", self._validate(waived)["validation_errors"]
        )

    def test_unavailable_or_uninspectable_device_fails(self) -> None:
        for field in ("available", "paired", "safari_web_inspector_target"):
            with self.subTest(field=field):
                capture = copy.deepcopy(self.capture)
                capture["device"][field] = False
                self.assertEqual(self._validate(capture)["outcome"], "FAIL")

    def test_standalone_and_voiceover_are_observed_not_inferred(self) -> None:
        standalone = copy.deepcopy(self.capture)
        session = next(
            item
            for item in standalone["sessions"]
            if item["session_id"] == "installed_standalone"
        )
        session["navigator_standalone"] = False
        self.assertIn(
            "session:installed_standalone:navigator_standalone_mismatch",
            self._validate(standalone)["validation_errors"],
        )

        voiceover = copy.deepcopy(self.capture)
        session = next(
            item for item in voiceover["sessions"] if item["session_id"] == "voiceover"
        )
        session["voiceover"]["enabled"] = False
        self.assertIn(
            "session:voiceover:voiceover_not_enabled",
            self._validate(voiceover)["validation_errors"],
        )

        malformed = copy.deepcopy(self.capture)
        session = next(
            item for item in malformed["sessions"] if item["session_id"] == "safari"
        )
        session["voiceover"] = []
        self.assertIn(
            "session:safari:voiceover_state_mismatch",
            self._validate(malformed)["validation_errors"],
        )

    def test_every_current_journey_and_assertion_is_required(self) -> None:
        capture = copy.deepcopy(self.capture)
        session = capture["sessions"][0]
        session["journeys"].pop()
        errors = self._validate(capture)["validation_errors"]
        self.assertIn("session:safari:journey_set_mismatch", errors)

        capture = copy.deepcopy(self.capture)
        session = capture["sessions"][0]
        session["journeys"][0]["assertions"].pop()
        errors = self._validate(capture)["validation_errors"]
        self.assertTrue(any("assertion_set_mismatch" in item for item in errors))

    def test_source_runtime_fixture_network_and_media_are_fail_closed(self) -> None:
        dirty = self._validate(source={"commit": COMMIT, "working_tree_dirty": True})
        self.assertIn("working_tree_not_clean", dirty["validation_errors"])

        runtime = copy.deepcopy(self.capture)
        runtime["runtime_bindings"]["python_runtime_digest"] = "0" * 64
        self.assertIn(
            "python_runtime_digest_mismatch",
            self._validate(runtime)["validation_errors"],
        )

        fixture = copy.deepcopy(self.capture)
        fixture["sessions"][0]["fixture_status_after"]["wall_mutation_attempts"] = 1
        self.assertIn(
            "session:safari:after:fixture_wall_mutation_attempts_mismatch",
            self._validate(fixture)["validation_errors"],
        )

        network = copy.deepcopy(self.capture)
        trace_path = (
            self.directory / network["sessions"][0]["network_results"]["trace_path"]
        )
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        trace["log"]["entries"][0]["request"] = {
            "method": "POST",
            "url": "https://fixture.invalid/api/v1/scene",
        }
        trace_path.write_text(json.dumps(trace), encoding="utf-8")
        network["sessions"][0]["network_results"].update(
            self._artifact_record(trace_path)
        )
        network["sessions"][0]["network_results"]["trace_path"] = network["sessions"][
            0
        ]["network_results"].pop("path")
        network["sessions"][0]["network_results"]["trace_byte_count"] = network[
            "sessions"
        ][0]["network_results"].pop("byte_count")
        network["sessions"][0]["network_results"]["trace_sha256"] = network["sessions"][
            0
        ]["network_results"].pop("sha256")
        self.assertIn(
            "session:safari:forbidden_network_request_observed",
            self._validate(network)["validation_errors"],
        )

        media = copy.deepcopy(self.capture)
        media["sessions"][0]["media"][0]["sha256"] = "0" * 64
        self.assertIn(
            "session:safari:media:0:sha256_mismatch",
            self._validate(media)["validation_errors"],
        )

    def test_unreadable_capture_is_retained_as_failure(self) -> None:
        input_path = self.directory / "bad.json"
        output_path = self.directory / "retained.json"
        input_path.write_text("not json", encoding="utf-8")

        retained = retain_capture(
            input_path=input_path,
            output_path=output_path,
            manifest_path=MANIFEST_PATH,
            bootstrap_path=BOOTSTRAP_PATH,
        )

        self.assertEqual(retained["outcome"], "FAIL")
        self.assertTrue(output_path.is_file())
        self.assertEqual(json.loads(output_path.read_text())["outcome"], "FAIL")

    def test_duplicate_json_keys_and_unknown_schema_fields_fail_closed(self) -> None:
        input_path = self.directory / "duplicate.json"
        output_path = self.directory / "retained.json"
        input_path.write_text('{"schema": 1, "schema": 2}', encoding="utf-8")

        retained = retain_capture(
            input_path=input_path,
            output_path=output_path,
            manifest_path=MANIFEST_PATH,
            bootstrap_path=BOOTSTRAP_PATH,
        )
        self.assertEqual(retained["outcome"], "FAIL")
        self.assertIn("duplicate JSON key", retained["validation_errors"][0])

        capture = copy.deepcopy(self.capture)
        capture["device"]["trust_me"] = True
        self.assertIn(
            "device:unexpected_fields:trust_me",
            self._validate(capture)["validation_errors"],
        )

        wrong_primitive_types = copy.deepcopy(self.capture)
        wrong_primitive_types["schema_version"] = True
        wrong_primitive_types["device"]["simulator"] = 0
        primitive_errors = self._validate(wrong_primitive_types)["validation_errors"]
        self.assertIn("capture_schema_version_invalid", primitive_errors)
        self.assertIn("device_simulator_invalid", primitive_errors)

        forged = copy.deepcopy(self.capture)
        forged["sessions"][0]["network_results"]["request_count"] = 0
        forged["sessions"][0]["network_results"]["forbidden_mutation_requests"] = []
        self.assertTrue(
            any(
                "network_results:unexpected_fields" in item
                for item in self._validate(forged)["validation_errors"]
            )
        )

    def test_timestamps_are_fresh_utc_ordered_and_bound_to_artifacts(self) -> None:
        non_utc = copy.deepcopy(self.capture)
        non_utc["sessions"][0]["started_at"] = "2026-08-29T09:00:00-04:00"
        self.assertIn(
            "session:safari:timestamps_invalid",
            self._validate(non_utc)["validation_errors"],
        )

        stale = copy.deepcopy(self.capture)
        self.assertIn(
            "session:safari:evidence_stale",
            validate_capture(
                stale,
                manifest=self.manifest,
                source=self.source,
                artifact_base=self.directory,
                bootstrap_path=BOOTSTRAP_PATH,
                now=datetime(2026, 9, 6, 14, 0, tzinfo=timezone.utc),
            )["validation_errors"],
        )

        future = copy.deepcopy(self.capture)
        self.assertIn(
            "session:safari:timestamp_in_future",
            validate_capture(
                future,
                manifest=self.manifest,
                source=self.source,
                artifact_base=self.directory,
                bootstrap_path=BOOTSTRAP_PATH,
                now=datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
            )["validation_errors"],
        )

        reordered = copy.deepcopy(self.capture)
        reordered["sessions"][0], reordered["sessions"][1] = (
            reordered["sessions"][1],
            reordered["sessions"][0],
        )
        self.assertIn(
            "session_order_invalid", self._validate(reordered)["validation_errors"]
        )

        outside = copy.deepcopy(self.capture)
        outside["sessions"][0]["media"][0]["captured_at"] = "2026-08-29T13:11:00Z"
        self.assertIn(
            "session:safari:media:0:timestamp_outside_session",
            self._validate(outside)["validation_errors"],
        )

    def test_media_is_locally_parsed_voiceover_audio_is_real_and_artifacts_are_distinct(
        self,
    ) -> None:
        malformed = copy.deepcopy(self.capture)
        screenshot_path = self.directory / malformed["sessions"][0]["media"][0]["path"]
        screenshot_path.write_bytes(b"not an image")
        malformed["sessions"][0]["media"][0].update(
            self._artifact_record(screenshot_path)
        )
        self.assertIn(
            "session:safari:media:0:media_parse_failed",
            self._validate(malformed)["validation_errors"],
        )

        reused = self._capture()
        reused_media = copy.deepcopy(reused["sessions"][0]["media"][0])
        reused_media["session_id"] = "installed_standalone"
        reused_media["captured_at"] = "2026-08-29T13:16:00Z"
        reused["sessions"][1]["media"] = [reused_media]
        self.assertTrue(
            any(
                "artifact_reused_from:safari" in item
                for item in self._validate(reused)["validation_errors"]
            )
        )

        no_audio = self._capture()
        no_audio["sessions"][2]["media"] = no_audio["sessions"][2]["media"][:1]
        self.assertIn(
            "session:voiceover:voiceover_audio_capture_missing",
            self._validate(no_audio)["validation_errors"],
        )

    def test_template_is_manifest_complete_and_deliberately_cannot_pass(self) -> None:
        template = capture_template(
            manifest=self.manifest,
            source=self.source,
            bootstrap_path=BOOTSTRAP_PATH,
        )

        self.assertEqual(template["disposition"], "NOT_EXECUTED")
        self.assertFalse(template["qualification_credit_requested"])
        self.assertEqual(
            {item["session_id"] for item in template["sessions"]},
            {"safari", "installed_standalone", "voiceover"},
        )
        for session in template["sessions"]:
            self.assertEqual(
                {item["journey_id"] for item in session["journeys"]},
                set(self.manifest["journeys"]),
            )
        retained = self._validate(template)
        self.assertEqual(retained["outcome"], "FAIL")
        self.assertIn("capture_not_executed", retained["validation_errors"])


if __name__ == "__main__":
    unittest.main()
