"""Focused PERF-01/POWER-01 qualification contract coverage."""

from __future__ import annotations

import unittest
from copy import deepcopy

from animation.core.activation_qualification import (
    QUALIFICATION_RECORD_SCHEMA,
    QualificationValidationError,
    activation_qualification_binding_digest,
    activation_qualification_record_digest,
    evaluate_activation_qualification,
    installation_qualification_budget_digest,
    load_installation_qualification_budget,
    normalize_activation_qualification_record,
    normalize_installation_qualification_budget,
    normalize_target_qualification_evidence,
)


NOW_MS = 2_000_000


def _calibrated_budget() -> dict:
    return {
        "schema": "ledgrid.installation-qualification-budget",
        "schema_version": 1,
        "revision": 7,
        "installation_id": "test-wall",
        "geometry": {"strip_count": 33, "leds_per_strip": 138},
        "calibration": {
            "status": "calibrated",
            "captured_at": 1_500_000,
            "environment": "test fixture, isolated calibrated supply",
        },
        "maximum_evidence_age_ms": 60_000,
        "budgets": {
            "voltage": {"minimum_mean_v": 4.5, "maximum_p99_v": 5.5},
            "current": {"maximum_a": 10.0},
            "brightness": {"maximum_controller_value": 200},
            "safety": {
                "required_current_headroom_ratio": 0.1,
                "maximum_p99_power_w": 50.0,
            },
        },
    }


def _binding() -> dict:
    return {
        "browser_scene": {"revision": 17, "digest": "a" * 64},
        "installation_profile_digest": "b" * 64,
        "global_settings": {"revision": 9, "digest": "c" * 64},
        "geometry": {"strip_count": 33, "leds_per_strip": 138},
        "brightness": 128,
        "vibe": {
            "vibe_id": "cozy",
            "profile_version": 2,
            "resolved_profile_digest": "d" * 64,
        },
        "plant_modifiers": {
            "version": 1,
            "active": ["illuminate", "obstacle"],
            "strengths": {"obstacle": 1.0, "illuminate": 0.4},
        },
        "target_fps": 100,
    }


def _stats(mean: float = 2.0, p95: float = 5.0, p99: float = 7.0, maximum: float = 8.0) -> dict:
    return {"mean": mean, "p95": p95, "p99": p99, "max": maximum}


def _electrical(kind: str, budget_digest: str | None, brightness: int = 128) -> dict:
    return {
        "kind": kind,
        "budget_digest": budget_digest,
        "brightness": brightness,
        "voltage_v": _stats(5.0, 5.1, 5.2, 5.3),
        "current_a": _stats(6.0, 7.0, 8.0, 8.5),
    }


def _evidence(
    source: str,
    binding_digest: str,
    *,
    captured_at: int = NOW_MS - 10_000,
    electrical: dict | None = None,
) -> dict:
    return {
        "source": source,
        "binding_digest": binding_digest,
        "captured_at": captured_at,
        "environment": f"{source} deterministic test environment",
        "sample_count": 1000,
        "frame_time_ms": _stats(),
        "cadence": {
            "observed_fps": 100.0,
            "missed_frame_ratio": 0.0,
            "changed_frame_ratio": None if source == "receiver" else 0.4,
        },
        "electrical": electrical,
    }


def _record(*, browser_estimate: bool = True) -> dict:
    budget = _calibrated_budget()
    binding = _binding()
    binding_digest = activation_qualification_binding_digest(binding)
    budget_digest = installation_qualification_budget_digest(budget)
    browser_electrical = (
        _electrical("uncalibrated_estimate", None) if browser_estimate else None
    )
    return {
        "schema": QUALIFICATION_RECORD_SCHEMA,
        "schema_version": 1,
        "revision": 3,
        "qualification_version": "portable-v1",
        "binding": binding,
        "budget": {"revision": budget["revision"], "digest": budget_digest},
        "evidence": [
            _evidence(
                "browser", binding_digest, electrical=browser_electrical
            ),
            _evidence(
                "controller_pi",
                binding_digest,
                electrical=_electrical(
                    "calibrated_measurement", budget_digest
                ),
            ),
            _evidence("receiver", binding_digest),
        ],
    }


class ActivationQualificationTests(unittest.TestCase):
    def test_exact_pass_is_canonical_and_binds_every_activation_input(self) -> None:
        budget = _calibrated_budget()
        record = _record()
        result = evaluate_activation_qualification(record, budget, now_ms=NOW_MS)

        self.assertTrue(result["qualified"], result["reasons"])
        self.assertEqual(result["frame_budget_ms"], 10.0)
        self.assertTrue(result["advisory"]["browser_electrical_estimate_present"])
        self.assertTrue(all(gate["passed"] for gate in result["gates"].values()))
        self.assertEqual(
            result["record_digest"], activation_qualification_record_digest(record)
        )

        reordered = deepcopy(record)
        reordered["evidence"].reverse()
        reordered["binding"]["plant_modifiers"]["strengths"] = {
            "illuminate": 0.4,
            "obstacle": 1.0,
        }
        self.assertEqual(
            activation_qualification_record_digest(record),
            activation_qualification_record_digest(reordered),
        )

        for path, replacement in (
            (("browser_scene", "revision"), 18),
            (("browser_scene", "digest"), "e" * 64),
            (("installation_profile_digest",), "f" * 64),
            (("global_settings", "revision"), 10),
            (("global_settings", "digest"), "1" * 64),
            (("geometry", "strip_count"), 32),
            (("brightness",), 129),
            (("vibe", "vibe_id"), "bold"),
            (("plant_modifiers", "strengths", "obstacle"), 0.9),
            (("target_fps",), 99),
        ):
            with self.subTest(path=path):
                changed = deepcopy(record["binding"])
                target = changed
                for part in path[:-1]:
                    target = target[part]
                target[path[-1]] = replacement
                self.assertNotEqual(
                    activation_qualification_binding_digest(record["binding"]),
                    activation_qualification_binding_digest(changed),
                )

    def test_missing_and_stale_target_evidence_fail_closed(self) -> None:
        budget = _calibrated_budget()
        missing = _record()
        missing["evidence"] = [
            item for item in missing["evidence"] if item["source"] != "receiver"
        ]
        result = evaluate_activation_qualification(missing, budget, now_ms=NOW_MS)
        self.assertFalse(result["qualified"])
        self.assertIn("missing_receiver_evidence", result["reasons"])

        stale = _record()
        next(
            item for item in stale["evidence"] if item["source"] == "controller_pi"
        )["captured_at"] = NOW_MS - 60_001
        result = evaluate_activation_qualification(stale, budget, now_ms=NOW_MS)
        self.assertFalse(result["qualified"])
        self.assertIn("stale_controller_pi_evidence", result["reasons"])

    def test_identity_mismatch_cannot_be_qualified(self) -> None:
        budget = _calibrated_budget()
        record = _record()
        next(
            item for item in record["evidence"] if item["source"] == "receiver"
        )["binding_digest"] = "9" * 64
        result = evaluate_activation_qualification(record, budget, now_ms=NOW_MS)
        self.assertFalse(result["qualified"])
        self.assertIn("receiver_binding_mismatch", result["reasons"])

        record = _record()
        record["budget"]["revision"] += 1
        result = evaluate_activation_qualification(record, budget, now_ms=NOW_MS)
        self.assertIn("budget_identity_mismatch", result["reasons"])

    def test_declared_fps_budget_is_inclusive_and_enforced_per_source(self) -> None:
        budget = _calibrated_budget()
        exact = _record()
        browser = next(
            item for item in exact["evidence"] if item["source"] == "browser"
        )
        browser["frame_time_ms"] = _stats(4.0, 10.0, 10.0, 10.0)
        self.assertTrue(
            evaluate_activation_qualification(exact, budget, now_ms=NOW_MS)[
                "gates"
            ]["performance"]["passed"]
        )

        browser["frame_time_ms"] = _stats(4.0, 10.001, 10.1, 10.2)
        result = evaluate_activation_qualification(exact, budget, now_ms=NOW_MS)
        self.assertFalse(result["qualified"])
        self.assertIn("browser_p95_exceeds_frame_budget", result["reasons"])

        browser["frame_time_ms"] = _stats(4.0, 5.0, 6.0, 7.0)
        browser["cadence"]["observed_fps"] = 99.99
        result = evaluate_activation_qualification(exact, budget, now_ms=NOW_MS)
        self.assertIn("browser_cadence_below_target_fps", result["reasons"])

    def test_electrical_and_brightness_thresholds_use_exact_activation(self) -> None:
        budget = _calibrated_budget()

        brightness = _record()
        brightness["binding"]["brightness"] = 201
        changed_binding = activation_qualification_binding_digest(brightness["binding"])
        for item in brightness["evidence"]:
            item["binding_digest"] = changed_binding
            if item["electrical"] is not None:
                item["electrical"]["brightness"] = 201
        result = evaluate_activation_qualification(brightness, budget, now_ms=NOW_MS)
        self.assertIn("activation_brightness_exceeds_budget", result["reasons"])

        mismatched = _record()
        controller = next(
            item for item in mismatched["evidence"] if item["source"] == "controller_pi"
        )
        controller["electrical"]["brightness"] = 127
        result = evaluate_activation_qualification(mismatched, budget, now_ms=NOW_MS)
        self.assertIn("controller_pi_electrical_brightness_mismatch", result["reasons"])

        current = _record()
        controller = next(
            item for item in current["evidence"] if item["source"] == "controller_pi"
        )
        controller["electrical"]["current_a"] = _stats(8.0, 8.5, 9.001, 9.1)
        result = evaluate_activation_qualification(current, budget, now_ms=NOW_MS)
        self.assertIn("controller_pi_current_exceeds_safe_budget", result["reasons"])

    def test_malformed_nonfinite_and_unordered_statistics_are_rejected(self) -> None:
        for replacement in (float("nan"), float("inf"), "fast", True):
            with self.subTest(replacement=replacement):
                record = _record()
                record["evidence"][0]["frame_time_ms"]["mean"] = replacement
                with self.assertRaises(QualificationValidationError):
                    normalize_activation_qualification_record(record)

        record = _record()
        record["evidence"][0]["frame_time_ms"] = _stats(5.0, 4.0, 7.0, 8.0)
        with self.assertRaisesRegex(QualificationValidationError, "mean <= p95"):
            normalize_activation_qualification_record(record)

        budget = _calibrated_budget()
        budget["budgets"]["current"]["maximum_a"] = float("nan")
        with self.assertRaises(QualificationValidationError):
            normalize_installation_qualification_budget(budget)

    def test_source_labels_are_unique_and_control_electrical_authority(self) -> None:
        duplicate = _record()
        duplicate["evidence"][2]["source"] = "browser"
        with self.assertRaisesRegex(QualificationValidationError, "duplicate sources"):
            normalize_activation_qualification_record(duplicate)

        browser_claim = _record()
        browser = browser_claim["evidence"][0]
        browser["electrical"] = _electrical(
            "calibrated_measurement",
            installation_qualification_budget_digest(_calibrated_budget()),
        )
        with self.assertRaisesRegex(
            QualificationValidationError,
            "browser evidence cannot claim",
        ):
            normalize_activation_qualification_record(browser_claim)

        target_estimate = _record()
        controller = target_estimate["evidence"][1]
        controller["electrical"] = _electrical("uncalibrated_estimate", None)
        with self.assertRaisesRegex(
            QualificationValidationError,
            "must be labeled browser",
        ):
            normalize_activation_qualification_record(target_estimate)

    def test_browser_only_estimate_is_advisory_and_never_satisfies_power(self) -> None:
        budget = _calibrated_budget()
        record = _record()
        record["evidence"] = [record["evidence"][0]]
        result = evaluate_activation_qualification(record, budget, now_ms=NOW_MS)
        self.assertFalse(result["qualified"])
        self.assertTrue(result["advisory"]["browser_electrical_estimate_present"])
        self.assertIn(
            "missing_calibrated_target_electrical_evidence", result["reasons"]
        )
        self.assertIn("missing_controller_pi_evidence", result["reasons"])
        self.assertIn("missing_receiver_evidence", result["reasons"])

    def test_checked_in_unknown_budget_loads_but_power_fails_closed(self) -> None:
        budget = load_installation_qualification_budget()
        self.assertEqual(budget["calibration"]["status"], "unqualified")
        self.assertEqual(budget["maximum_evidence_age_ms"], 14_400_000)
        self.assertIsNone(budget["budgets"]["current"]["maximum_a"])

        record = _record()
        record["budget"] = {
            "revision": budget["revision"],
            "digest": installation_qualification_budget_digest(budget),
        }
        controller = next(
            item for item in record["evidence"] if item["source"] == "controller_pi"
        )
        controller["electrical"]["budget_digest"] = record["budget"]["digest"]
        result = evaluate_activation_qualification(record, budget, now_ms=NOW_MS)
        self.assertFalse(result["qualified"])
        self.assertTrue(result["gates"]["identity"]["passed"])
        self.assertFalse(result["gates"]["power"]["passed"])
        self.assertIn("installation_budget_uncalibrated", result["reasons"])

    def test_target_evidence_envelope_requires_one_exact_simultaneous_pair(self) -> None:
        binding_digest = activation_qualification_binding_digest(_binding())
        captured_at = NOW_MS - 1_000
        envelope = {
            "schema": "ledgrid.target-qualification-evidence",
            "schema_version": 1,
            "revision": 1,
            "binding_digest": binding_digest,
            "captured_at": captured_at,
            "environment": "Raspberry Pi and exact installed five-receiver wall",
            "evidence": [
                _evidence(
                    "controller_pi", binding_digest, captured_at=captured_at,
                ),
                _evidence("receiver", binding_digest, captured_at=captured_at),
            ],
        }
        normalized = normalize_target_qualification_evidence(envelope)
        self.assertEqual(
            [item["source"] for item in normalized["evidence"]],
            ["controller_pi", "receiver"],
        )

        for label, mutate in (
            ("missing receiver", lambda value: value["evidence"].pop()),
            (
                "wrong binding",
                lambda value: value["evidence"][1].__setitem__(
                    "binding_digest", "9" * 64
                ),
            ),
            (
                "different window",
                lambda value: value["evidence"][0].__setitem__(
                    "captured_at", captured_at - 1
                ),
            ),
        ):
            with self.subTest(label=label):
                changed = deepcopy(envelope)
                mutate(changed)
                with self.assertRaises(QualificationValidationError):
                    normalize_target_qualification_evidence(changed)


if __name__ == "__main__":
    unittest.main()
