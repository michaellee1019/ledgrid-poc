from __future__ import annotations

import hashlib
import unittest

from animation.core.installation_profile_transaction import (
    FakeInstallationProfileFault,
    FakeInstallationProfileFaultEffect,
    FakeInstallationProfileWall,
    InstallationProfileCandidate,
    InstallationProfileTransaction,
    InstallationProfileTransactionError,
    InstallationProfileTransactionPhase,
    InstallationProfileWallHealth,
)


def _profile_id(label: str) -> str:
    return hashlib.sha256(f"global:{label}".encode()).hexdigest()


def _candidate(label: str, *, payload_bytes: int = 24) -> InstallationProfileCandidate:
    payloads = {
        receiver_id: (
            f"{label}:receiver:{receiver_id}:".encode()
            + bytes((65 + receiver_id,)) * payload_bytes
        )
        for receiver_id in range(4)
    }
    return InstallationProfileCandidate(_profile_id(label), payloads)


def _binding_state(wall: FakeInstallationProfileWall):
    return tuple(
        (
            receiver.status().active_binding,
            receiver.status().rollback_binding,
            receiver.status().staged_binding,
        )
        for receiver in wall.receivers
    )


class InstallationProfileCandidateTests(unittest.TestCase):
    def test_one_global_content_id_binds_four_receiver_specific_content_ids(self):
        candidate = _candidate("geometry-v1")

        self.assertEqual(candidate.profile_id, _profile_id("geometry-v1"))
        self.assertEqual(len(set(candidate.receiver_payloads)), 4)
        self.assertEqual(len(set(candidate.receiver_payload_digests)), 4)
        for receiver_id in range(4):
            payload = candidate.payload_for(receiver_id)
            binding = candidate.binding_for(receiver_id)
            self.assertIs(type(payload), bytes)
            self.assertEqual(binding.profile_id, candidate.profile_id)
            self.assertEqual(
                binding.payload_digest, hashlib.sha256(payload).hexdigest()
            )

    def test_candidate_requires_exact_ids_and_immutable_nonempty_bytes(self):
        valid = {receiver_id: b"payload" for receiver_id in range(4)}
        invalid_cases = (
            ("x" * 64, valid, ValueError),
            (_profile_id("x"), {0: b"x"}, ValueError),
            (
                _profile_id("x"),
                {0: b"x", 1: b"x", 2: b"x", True: b"x"},
                ValueError,
            ),
            (
                _profile_id("x"),
                {0: b"x", 1: bytearray(b"x"), 2: b"x", 3: b"x"},
                TypeError,
            ),
            (
                _profile_id("x"),
                {0: b"x", 1: b"x", 2: b"", 3: b"x"},
                ValueError,
            ),
        )
        for profile_id, payloads, error in invalid_cases:
            with self.subTest(profile_id=profile_id, error=error):
                with self.assertRaises(error):
                    InstallationProfileCandidate(profile_id, payloads)  # type: ignore[arg-type]


class InstallationProfileTransactionTests(unittest.TestCase):
    @staticmethod
    def wall(
        *, capacity_bytes: int | tuple[int, int, int, int] = 512, reserve_bytes: int = 32
    ) -> FakeInstallationProfileWall:
        return FakeInstallationProfileWall(
            capacity_bytes=capacity_bytes, reserve_bytes=reserve_bytes
        )

    def test_stages_verifies_and_commits_all_receivers_in_deterministic_order(self):
        wall = self.wall()
        candidate = _candidate("new")
        result = InstallationProfileTransaction(wall).install(candidate)

        self.assertTrue(result.success)
        self.assertTrue(result.changed)
        self.assertFalse(result.compensated)
        self.assertTrue(result.wall_status.healthy)
        self.assertEqual(result.wall_status.active_profile_id, candidate.profile_id)
        self.assertEqual(
            [(operation.phase, operation.receiver_id) for operation in result.operations],
            [
                (phase, receiver_id)
                for phase in (
                    InstallationProfileTransactionPhase.PREFLIGHT,
                    InstallationProfileTransactionPhase.STAGE,
                    InstallationProfileTransactionPhase.VERIFY,
                    InstallationProfileTransactionPhase.COMMIT,
                )
                for receiver_id in range(4)
            ],
        )
        for receiver_id, status in enumerate(result.wall_status.receiver_statuses):
            self.assertEqual(status.active_binding, candidate.binding_for(receiver_id))
            self.assertIsNone(status.staged_binding)
            self.assertIsNone(status.rollback_binding)
            self.assertIn(
                candidate.receiver_payload_digests[receiver_id], status.cached_digests
            )
            self.assertGreaterEqual(status.available_bytes, 0)

    def test_success_promotes_prior_active_to_rollback_and_pins_both(self):
        wall = self.wall()
        prior = _candidate("prior")
        superseded_rollback = _candidate("superseded-rollback")
        candidate = _candidate("candidate")
        wall.seed_active(prior)
        wall.seed_rollback(superseded_rollback)

        result = InstallationProfileTransaction(wall).install(candidate)

        self.assertTrue(result.success)
        for receiver_id, status in enumerate(result.wall_status.receiver_statuses):
            self.assertEqual(status.active_binding, candidate.binding_for(receiver_id))
            self.assertEqual(status.rollback_binding, prior.binding_for(receiver_id))
            self.assertEqual(
                set(status.pinned_digests),
                {
                    candidate.receiver_payload_digests[receiver_id],
                    prior.receiver_payload_digests[receiver_id],
                },
            )
            self.assertIn(
                superseded_rollback.receiver_payload_digests[receiver_id],
                status.cached_digests,
            )

    def test_retry_after_success_is_idempotent_and_preserves_rollback(self):
        wall = self.wall()
        prior = _candidate("prior")
        candidate = _candidate("candidate")
        wall.seed_active(prior)
        transaction = InstallationProfileTransaction(wall)
        first = transaction.install(candidate)
        counts_before = tuple(
            (
                status.write_count,
                status.stage_count,
                status.verify_count,
                status.commit_count,
            )
            for status in first.wall_status.receiver_statuses
        )

        retry = transaction.install(candidate)

        self.assertTrue(retry.success)
        self.assertFalse(retry.changed)
        self.assertEqual(retry.operations, ())
        self.assertEqual(
            counts_before,
            tuple(
                (
                    status.write_count,
                    status.stage_count,
                    status.verify_count,
                    status.commit_count,
                )
                for status in retry.wall_status.receiver_statuses
            ),
        )
        for receiver_id, status in enumerate(retry.wall_status.receiver_statuses):
            self.assertEqual(status.rollback_binding, prior.binding_for(receiver_id))

    def test_capacity_preflight_fails_before_any_receiver_mutation(self):
        wall = self.wall(
            capacity_bytes=(180, 180, 120, 180), reserve_bytes=20
        )
        prior = _candidate("prior", payload_bytes=20)
        rollback = _candidate("rollback", payload_bytes=20)
        candidate = _candidate("candidate", payload_bytes=45)
        wall.seed_active(prior)
        wall.seed_rollback(rollback)
        before_bindings = _binding_state(wall)
        before_cache = tuple(receiver.cached_digests for receiver in wall.receivers)
        before_counts = tuple(receiver.status().stage_count for receiver in wall.receivers)

        result = InstallationProfileTransaction(wall).install(candidate)

        self.assertFalse(result.success)
        self.assertEqual(
            result.failed_phase, InstallationProfileTransactionPhase.PREFLIGHT
        )
        self.assertEqual(result.failed_receiver_id, 2)
        self.assertFalse(result.compensated)
        self.assertEqual(_binding_state(wall), before_bindings)
        self.assertEqual(
            tuple(receiver.cached_digests for receiver in wall.receivers), before_cache
        )
        self.assertEqual(
            tuple(receiver.status().stage_count for receiver in wall.receivers),
            before_counts,
        )
        self.assertTrue(result.wall_status.healthy)
        self.assertFalse(result.wall_status.mixed_generation)

    def test_inactive_lru_entries_are_evicted_but_reserve_is_retained(self):
        candidate = _candidate("candidate", payload_bytes=8)
        payload_size = len(candidate.payload_for(0))
        wall = self.wall(capacity_bytes=payload_size + 12, reserve_bytes=6)
        seeded: list[tuple[str, str]] = []
        for receiver_id, receiver in enumerate(wall.receivers):
            oldest = receiver.cache_inactive(f"old:{receiver_id}".encode())
            newest = receiver.cache_inactive(f"new:{receiver_id}".encode())
            seeded.append((oldest, newest))

        result = InstallationProfileTransaction(wall).install(candidate)

        self.assertTrue(result.success)
        for receiver_id, status in enumerate(result.wall_status.receiver_statuses):
            oldest, newest = seeded[receiver_id]
            self.assertNotIn(oldest, status.cached_digests)
            self.assertIn(newest, status.cached_digests)
            self.assertIn(
                candidate.receiver_payload_digests[receiver_id], status.cached_digests
            )
            self.assertEqual(status.eviction_count, 1)
            self.assertGreaterEqual(status.available_bytes, 0)

    def test_active_rollback_and_staged_pins_refuse_deletion(self):
        wall = self.wall()
        active = _candidate("active")
        rollback = _candidate("rollback")
        candidate = _candidate("candidate")
        wall.seed_active(active)
        wall.seed_rollback(rollback)
        failed = InstallationProfileTransaction(wall).install(
            candidate,
            faults=(
                FakeInstallationProfileFault(
                    3, InstallationProfileTransactionPhase.VERIFY
                ),
            ),
        )
        self.assertFalse(failed.success)

        for receiver_id, receiver in enumerate(wall.receivers):
            with self.assertRaisesRegex(
                InstallationProfileTransactionError, "pinned"
            ):
                receiver.delete_cached_payload(
                    active.receiver_payload_digests[receiver_id]
                )
            with self.assertRaisesRegex(
                InstallationProfileTransactionError, "pinned"
            ):
                receiver.delete_cached_payload(
                    rollback.receiver_payload_digests[receiver_id]
                )
            self.assertIsNone(receiver.staged_binding)

    def test_every_receiver_and_phase_failure_restores_prior_unanimous_state(self):
        phases = (
            InstallationProfileTransactionPhase.PREFLIGHT,
            InstallationProfileTransactionPhase.STAGE,
            InstallationProfileTransactionPhase.VERIFY,
            InstallationProfileTransactionPhase.COMMIT,
        )
        for phase in phases:
            for receiver_id in range(4):
                with self.subTest(phase=phase, receiver_id=receiver_id):
                    wall = self.wall()
                    prior = _candidate("prior")
                    rollback = _candidate("rollback")
                    candidate = _candidate("candidate")
                    wall.seed_active(prior)
                    wall.seed_rollback(rollback)
                    before = _binding_state(wall)

                    result = InstallationProfileTransaction(wall).install(
                        candidate,
                        faults=(FakeInstallationProfileFault(receiver_id, phase),),
                    )

                    self.assertFalse(result.success)
                    self.assertEqual(result.failed_phase, phase)
                    self.assertEqual(result.failed_receiver_id, receiver_id)
                    self.assertEqual(_binding_state(wall), before)
                    self.assertTrue(result.wall_status.healthy)
                    self.assertFalse(result.wall_status.mixed_generation)
                    self.assertEqual(
                        result.wall_status.active_profile_id, prior.profile_id
                    )
                    for target_id, status in enumerate(
                        result.wall_status.receiver_statuses
                    ):
                        self.assertIn(
                            prior.receiver_payload_digests[target_id],
                            status.cached_digests,
                        )
                        self.assertIn(
                            rollback.receiver_payload_digests[target_id],
                            status.cached_digests,
                        )
                    attempted = [
                        operation.receiver_id
                        for operation in result.operations
                        if operation.phase is phase
                    ]
                    self.assertEqual(attempted, list(range(receiver_id + 1)))

    def test_partial_commit_failure_restores_explicit_no_active_state(self):
        wall = self.wall()
        candidate = _candidate("candidate")

        result = InstallationProfileTransaction(wall).install(
            candidate,
            faults=(
                FakeInstallationProfileFault(
                    2, InstallationProfileTransactionPhase.COMMIT
                ),
            ),
        )

        self.assertFalse(result.success)
        self.assertTrue(result.compensated)
        self.assertTrue(result.wall_status.no_active)
        self.assertFalse(result.wall_status.healthy)
        self.assertFalse(result.wall_status.mixed_generation)
        for status in result.wall_status.receiver_statuses:
            self.assertIsNone(status.active_binding)
            self.assertIsNone(status.rollback_binding)
            self.assertIsNone(status.staged_binding)

    def test_staged_corruption_is_detected_compensated_and_retry_repairs_it(self):
        wall = self.wall()
        prior = _candidate("prior")
        candidate = _candidate("candidate")
        wall.seed_active(prior)
        transaction = InstallationProfileTransaction(wall)

        corrupted = transaction.install(
            candidate,
            faults=(
                FakeInstallationProfileFault(
                    2,
                    InstallationProfileTransactionPhase.STAGE,
                    FakeInstallationProfileFaultEffect.CORRUPT_STAGED_PAYLOAD,
                ),
            ),
        )

        self.assertFalse(corrupted.success)
        self.assertEqual(
            corrupted.failed_phase, InstallationProfileTransactionPhase.VERIFY
        )
        self.assertEqual(corrupted.failed_receiver_id, 2)
        self.assertTrue(corrupted.compensated)
        self.assertTrue(corrupted.wall_status.healthy)
        self.assertFalse(corrupted.wall_status.mixed_generation)
        self.assertNotIn(
            candidate.receiver_payload_digests[2],
            corrupted.wall_status.receiver_statuses[2].cached_digests,
        )
        self.assertTrue(
            all(
                status.cache_integrity_ok
                for status in corrupted.wall_status.receiver_statuses
            )
        )

        retry = transaction.install(candidate)

        self.assertTrue(retry.success)
        self.assertEqual(retry.wall_status.active_profile_id, candidate.profile_id)

    def test_retry_after_verify_rejection_reuses_intact_cached_payloads(self):
        wall = self.wall()
        candidate = _candidate("candidate")
        transaction = InstallationProfileTransaction(wall)
        failed = transaction.install(
            candidate,
            faults=(
                FakeInstallationProfileFault(
                    2, InstallationProfileTransactionPhase.VERIFY
                ),
            ),
        )
        writes_after_failure = tuple(
            status.write_count for status in failed.wall_status.receiver_statuses
        )

        retry = transaction.install(candidate)

        self.assertTrue(retry.success)
        self.assertEqual(
            writes_after_failure,
            tuple(
                status.write_count for status in retry.wall_status.receiver_statuses
            ),
        )

    def test_mixed_generation_is_never_reported_healthy_or_mutated(self):
        wall = self.wall()
        first = _candidate("first")
        second = _candidate("second")
        candidate = _candidate("candidate")
        wall.seed_active(first)
        wall.receiver(3).seed_active(second.profile_id, second.payload_for(3))
        before = _binding_state(wall)

        status = wall.status()
        result = InstallationProfileTransaction(wall).install(candidate)

        self.assertEqual(
            status.health, InstallationProfileWallHealth.MIXED_GENERATION
        )
        self.assertTrue(status.mixed_generation)
        self.assertFalse(status.healthy)
        self.assertFalse(result.success)
        self.assertEqual(
            result.failed_phase, InstallationProfileTransactionPhase.PREFLIGHT
        )
        self.assertEqual(result.operations, ())
        self.assertEqual(_binding_state(wall), before)
        self.assertTrue(result.wall_status.mixed_generation)
        self.assertFalse(result.wall_status.healthy)

    def test_corrupt_pinned_active_payload_degrades_and_blocks_transaction(self):
        wall = self.wall()
        active = _candidate("active")
        candidate = _candidate("candidate")
        wall.seed_active(active)
        wall.receiver(1).corrupt_cached_payload(
            active.receiver_payload_digests[1]
        )
        before = _binding_state(wall)

        result = InstallationProfileTransaction(wall).install(candidate)

        self.assertEqual(wall.status().health, InstallationProfileWallHealth.DEGRADED)
        self.assertFalse(result.success)
        self.assertEqual(result.operations, ())
        self.assertEqual(_binding_state(wall), before)


class InstallationProfileFakeValidationTests(unittest.TestCase):
    def test_fake_configuration_and_faults_fail_closed(self):
        for kwargs in (
            {"capacity_bytes": 0},
            {"capacity_bytes": 10, "reserve_bytes": 11},
            {"capacity_bytes": (10, 10, 10)},
            {"capacity_bytes": (10, 10, 10, True)},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises((TypeError, ValueError)):
                FakeInstallationProfileWall(**kwargs)  # type: ignore[arg-type]

        with self.assertRaisesRegex(ValueError, "compensation"):
            FakeInstallationProfileFault(
                0, InstallationProfileTransactionPhase.COMPENSATE
            )
        with self.assertRaisesRegex(ValueError, "only during stage"):
            FakeInstallationProfileFault(
                0,
                InstallationProfileTransactionPhase.VERIFY,
                FakeInstallationProfileFaultEffect.CORRUPT_STAGED_PAYLOAD,
            )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            InstallationProfileTransaction(
                FakeInstallationProfileWall(capacity_bytes=128)
            ).install(
                _candidate("candidate"),
                faults=(
                    FakeInstallationProfileFault(
                        0, InstallationProfileTransactionPhase.STAGE
                    ),
                    FakeInstallationProfileFault(
                        0, InstallationProfileTransactionPhase.STAGE
                    ),
                ),
            )


if __name__ == "__main__":
    unittest.main()
