"""High-risk policy tests for optional deployment-gate caching."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace

from tools.deployment.gate_policy import (
    CacheEligibility,
    CacheProbeReason,
    GateCacheInputs,
    GateDescriptor,
    GateDisposition,
    GateTiming,
    build_cache_identity,
    evaluate_cache_candidate,
    probe_cache_record,
    skipped_gate,
)


class GateEligibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.descriptor = GateDescriptor(
            gate_id="tests.unit",
            gate_version="3",
            deterministic=True,
            local_only=True,
        )

    @staticmethod
    def timings(count: int, duration: float = 6.0) -> list[GateTiming]:
        return [GateTiming(f"attempt-{index}", duration) for index in range(count)]

    def test_requires_twenty_normal_successful_receipts(self):
        timings = self.timings(18)
        timings.extend(
            [
                GateTiming("failed", 20.0, succeeded=False),
                GateTiming("diagnostic", 20.0, normal_attempt=False),
            ]
        )

        decision = evaluate_cache_candidate(
            self.descriptor, timings, materially_improves_workflow=True
        )

        self.assertEqual(decision.eligibility, CacheEligibility.INSUFFICIENT_EVIDENCE)
        self.assertEqual(decision.normal_success_count, 18)
        self.assertFalse(decision.permits_cache_implementation)

    def test_rejects_duplicate_receipts_as_manufactured_evidence(self):
        timings = self.timings(20)
        timings[-1] = GateTiming("attempt-0", 30.0)

        with self.assertRaisesRegex(ValueError, "duplicate receipt"):
            evaluate_cache_candidate(
                self.descriptor, timings, materially_improves_workflow=True
            )

    def test_never_caches_external_or_nondeterministic_gate(self):
        timings = self.timings(20)
        external = evaluate_cache_candidate(
            replace(self.descriptor, local_only=False),
            timings,
            materially_improves_workflow=True,
        )
        nondeterministic = evaluate_cache_candidate(
            replace(self.descriptor, deterministic=False),
            timings,
            materially_improves_workflow=True,
        )

        self.assertEqual(external.eligibility, CacheEligibility.INELIGIBLE_EXTERNAL)
        self.assertEqual(
            nondeterministic.eligibility,
            CacheEligibility.INELIGIBLE_NONDETERMINISTIC,
        )

    def test_requires_gate_to_regularly_cost_at_least_five_seconds(self):
        timings = self.timings(14, 9.0) + [
            GateTiming(f"fast-{index}", 1.0) for index in range(6)
        ]

        decision = evaluate_cache_candidate(
            self.descriptor, timings, materially_improves_workflow=True
        )

        self.assertEqual(decision.eligibility, CacheEligibility.BELOW_COST_THRESHOLD)
        self.assertEqual(decision.threshold_sample_count, 14)

    def test_requires_observed_material_workflow_benefit(self):
        decision = evaluate_cache_candidate(
            self.descriptor,
            self.timings(20),
            materially_improves_workflow=False,
        )

        self.assertEqual(decision.eligibility, CacheEligibility.NO_MATERIAL_BENEFIT)

    def test_eligibility_only_permits_future_reviewed_implementation(self):
        decision = evaluate_cache_candidate(
            self.descriptor,
            self.timings(20),
            materially_improves_workflow=True,
        )

        self.assertEqual(decision.eligibility, CacheEligibility.ELIGIBLE)
        self.assertTrue(decision.permits_cache_implementation)
        self.assertIn("separately reviewed", decision.reason)


class GateIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inputs = GateCacheInputs(
            gate_id="tests.unit",
            gate_version="3",
            selected_source_contents={"b.py": b"b", "a.py": "a"},
            dirty_manifest=b"clean",
            lockfile_contents={"uv.lock": b"locked"},
            interpreter_identity="cpython-3.10.14",
            platform_identity="linux-aarch64",
            toolchain_identity="uv-1/platformio-55.03.39",
            command_arguments=("python", "-m", "unittest"),
        )

    def test_mapping_order_does_not_change_identity(self):
        reordered = replace(
            self.inputs,
            selected_source_contents={"a.py": "a", "b.py": b"b"},
        )
        self.assertEqual(
            build_cache_identity(self.inputs), build_cache_identity(reordered)
        )

    def test_every_declared_input_invalidates_identity(self):
        base = build_cache_identity(self.inputs)
        mutations = (
            replace(self.inputs, gate_id="tests.render"),
            replace(self.inputs, gate_version="4"),
            replace(self.inputs, selected_source_contents={"a.py": b"changed"}),
            replace(self.inputs, dirty_manifest=b"dirty diff"),
            replace(self.inputs, lockfile_contents={"uv.lock": b"new lock"}),
            replace(self.inputs, interpreter_identity="cpython-3.11.9"),
            replace(self.inputs, platform_identity="darwin-arm64"),
            replace(self.inputs, toolchain_identity="uv-2/platformio-55.03.39"),
            replace(self.inputs, command_arguments=("python", "-m", "pytest")),
        )

        self.assertEqual(
            len(mutations), len({build_cache_identity(item) for item in mutations})
        )
        for mutation in mutations:
            self.assertNotEqual(base, build_cache_identity(mutation))

    def test_rejects_unsafe_paths_and_incomplete_identity(self):
        with self.assertRaisesRegex(ValueError, "unsafe selected source"):
            replace(self.inputs, selected_source_contents={"../secret": b"x"})
        with self.assertRaisesRegex(ValueError, "lockfile_contents"):
            replace(self.inputs, lockfile_contents={})
        with self.assertRaisesRegex(ValueError, "command_arguments"):
            replace(self.inputs, command_arguments=())


class CacheProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        descriptor = GateDescriptor("tests.unit", "3", True, True)
        self.enabled = evaluate_cache_candidate(
            descriptor,
            [GateTiming(f"attempt-{index}", 6.0) for index in range(20)],
            materially_improves_workflow=True,
        )
        self.disabled = evaluate_cache_candidate(
            descriptor,
            [],
            materially_improves_workflow=True,
        )
        self.identity = "a" * 64
        self.record = json.dumps(
            {
                "schema_version": 1,
                "gate_id": "tests.unit",
                "gate_version": "3",
                "identity": self.identity,
                "outcome": "passed",
                "result_digest": "b" * 64,
            }
        ).encode()

    def test_policy_disabled_and_missing_records_execute(self):
        disabled = probe_cache_record(
            self.record,
            expected_identity=self.identity,
            policy_decision=self.disabled,
        )
        missing = probe_cache_record(
            None,
            expected_identity=self.identity,
            policy_decision=self.enabled,
        )

        self.assertEqual(
            (disabled.disposition, disabled.reason),
            (GateDisposition.EXECUTED, CacheProbeReason.POLICY_DISABLED),
        )
        self.assertEqual(missing.reason, CacheProbeReason.MISSING)

    def test_corrupt_failed_or_unknown_record_executes_safely(self):
        cases = [
            b"not-json",
            b"{}",
            self.record[:-1],
            self.record.replace(b'"passed"', b'"failed"'),
            self.record.replace(b'"schema_version": 1', b'"schema_version": 2'),
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                probe = probe_cache_record(
                    raw,
                    expected_identity=self.identity,
                    policy_decision=self.enabled,
                )
                self.assertEqual(probe.disposition, GateDisposition.EXECUTED)
                self.assertEqual(probe.reason, CacheProbeReason.CORRUPT)

    def test_identity_mismatch_executes_and_hit_is_receipt_visible(self):
        mismatch = probe_cache_record(
            self.record,
            expected_identity="c" * 64,
            policy_decision=self.enabled,
        )
        hit = probe_cache_record(
            self.record,
            expected_identity=self.identity,
            policy_decision=self.enabled,
        )

        self.assertEqual(mismatch.reason, CacheProbeReason.IDENTITY_MISMATCH)
        self.assertEqual(hit.disposition, GateDisposition.CACHED)
        self.assertEqual(hit.result_digest, "b" * 64)

    def test_record_descriptor_mismatch_cannot_reuse_aggregate_identity(self):
        wrong_gate = self.record.replace(b'"tests.unit"', b'"tests.other"')
        wrong_version = self.record.replace(
            b'"gate_version": "3"', b'"gate_version": "4"'
        )
        for record in (wrong_gate, wrong_version):
            with self.subTest(record=record):
                probe = probe_cache_record(
                    record,
                    expected_identity=self.identity,
                    policy_decision=self.enabled,
                )
                self.assertEqual(probe.reason, CacheProbeReason.IDENTITY_MISMATCH)

    def test_force_complete_gate_bypasses_even_a_valid_hit(self):
        probe = probe_cache_record(
            self.record,
            expected_identity=self.identity,
            policy_decision=self.enabled,
            force=True,
        )
        self.assertEqual(
            (probe.disposition, probe.reason),
            (GateDisposition.EXECUTED, CacheProbeReason.FORCED),
        )

    def test_skipped_is_distinct_from_executed_and_cached(self):
        self.assertEqual(skipped_gate(), GateDisposition.SKIPPED)


if __name__ == "__main__":
    unittest.main()
