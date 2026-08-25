from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import tempfile
import unittest

from animation.core.installation_profile import (
    compile_installation_profile,
    encode_installation_profile,
)
from animation.core.installation_profile_library import InstallationProfileLibrary
from animation.core.installation_profile_topology import (
    IDENTITY_INSTALLATION_PROFILE_TOPOLOGY,
    INSTALLED_INSTALLATION_PROFILE_TOPOLOGY,
    RECEIVER_COUNT,
)
from animation.core.installation_profile_transaction import (
    FakeInstallationProfileFault,
    FakeInstallationProfileFaultEffect,
    FakeInstallationProfileReceiver,
    FakeInstallationProfileWall,
    InstallationProfileCandidate,
    InstallationProfileReceiverSnapshot,
    InstallationProfileTransaction,
    InstallationProfileTransactionError,
    InstallationProfileTransactionPhase,
    InstallationProfileWallHealth,
    candidate_from_resolved,
)


def _profile_id(label: str) -> str:
    return hashlib.sha256(f"global:{label}".encode()).hexdigest()


def _candidate(label: str, *, payload_bytes: int = 24) -> InstallationProfileCandidate:
    payloads = {
        receiver_id: (
            f"{label}:receiver:{receiver_id}:".encode()
            + bytes((65 + receiver_id,)) * payload_bytes
        )
        for receiver_id in range(RECEIVER_COUNT)
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


class _StructuralReceiverAdapter:
    """Non-subclassing adapter exercising the documented receiver boundary."""

    def __init__(self, receiver: FakeInstallationProfileReceiver) -> None:
        self._receiver = receiver
        self.receiver_id = receiver.receiver_id

    @property
    def active_binding(self):
        return self._receiver.active_binding

    @property
    def staged_binding(self):
        return self._receiver.staged_binding

    def binding_is_valid(self, binding):
        return self._receiver.binding_is_valid(binding)

    def transaction_snapshot(self):
        return self._receiver.transaction_snapshot()

    def preflight_profile(self, binding, payload):
        return self._receiver.preflight_profile(binding, payload)

    def stage_profile(self, plan, payload, *, corrupt_payload):
        self._receiver.stage_profile(
            plan, payload, corrupt_payload=corrupt_payload
        )

    def verify_profile(self, binding, payload):
        self._receiver.verify_profile(binding, payload)

    def commit_profile(self, binding, prior_active):
        self._receiver.commit_profile(binding, prior_active)

    def compensate_profile(self, snapshot):
        self._receiver.compensate_profile(snapshot)


class _StructuralWallAdapter:
    """Non-fake wall object composed only through the structural interface."""

    def __init__(self, wall: FakeInstallationProfileWall) -> None:
        self._wall = wall
        self.receivers = tuple(
            _StructuralReceiverAdapter(receiver) for receiver in wall.receivers
        )

    def status(self):
        return self._wall.status()


class _AdversarialReceiverAdapter(_StructuralReceiverAdapter):
    def __init__(
        self,
        receiver: FakeInstallationProfileReceiver,
        *,
        commit_noop: bool = False,
        drop_rollback: bool = False,
        fail_commit: bool = False,
        fail_after_stage: bool = False,
        compensation: str = "normal",
        invalid_after_compensation: bool = False,
    ) -> None:
        super().__init__(receiver)
        self.commit_noop = commit_noop
        self.drop_rollback = drop_rollback
        self.fail_commit = fail_commit
        self.fail_after_stage = fail_after_stage
        self.compensation = compensation
        self.invalid_after_compensation = invalid_after_compensation
        self.compensation_calls = 0

    def binding_is_valid(self, binding):
        if (
            self.invalid_after_compensation
            and self.compensation_calls
            and binding is not None
        ):
            return False
        return super().binding_is_valid(binding)

    def stage_profile(self, plan, payload, *, corrupt_payload):
        super().stage_profile(
            plan, payload, corrupt_payload=corrupt_payload
        )
        if self.fail_after_stage:
            raise TimeoutError(f"receiver {self.receiver_id} stage timed out")

    def commit_profile(self, binding, prior_active):
        if self.fail_commit:
            raise TimeoutError(f"receiver {self.receiver_id} commit timed out")
        if not self.commit_noop:
            super().commit_profile(
                binding, None if self.drop_rollback else prior_active
            )

    def compensate_profile(self, snapshot):
        self.compensation_calls += 1
        if self.compensation == "fail":
            raise TimeoutError(
                f"receiver {self.receiver_id} compensation timed out"
            )
        if self.compensation != "noop":
            super().compensate_profile(snapshot)


class _AdversarialWallAdapter(_StructuralWallAdapter):
    def __init__(
        self,
        wall: FakeInstallationProfileWall,
        receiver_options=None,
        *,
        dishonest_profile_id=None,
    ) -> None:
        self._wall = wall
        receiver_options = receiver_options or {}
        self.receivers = tuple(
            _AdversarialReceiverAdapter(
                receiver,
                **receiver_options.get(receiver.receiver_id, {}),
            )
            for receiver in wall.receivers
        )
        self.dishonest_profile_id = dishonest_profile_id

    def status(self):
        status = self._wall.status()
        if self.dishonest_profile_id is None:
            return status
        return replace(
            status,
            health=InstallationProfileWallHealth.HEALTHY,
            active_profile_id=self.dishonest_profile_id,
            mixed_generation=False,
        )


class InstallationProfileCandidateTests(unittest.TestCase):
    def test_one_global_content_id_binds_five_receiver_specific_content_ids(self):
        candidate = _candidate("geometry-v1")

        self.assertEqual(candidate.profile_id, _profile_id("geometry-v1"))
        self.assertEqual(len(set(candidate.receiver_payloads)), RECEIVER_COUNT)
        self.assertEqual(len(set(candidate.receiver_payload_digests)), RECEIVER_COUNT)
        for receiver_id in range(RECEIVER_COUNT):
            payload = candidate.payload_for(receiver_id)
            binding = candidate.binding_for(receiver_id)
            self.assertIs(type(payload), bytes)
            self.assertEqual(binding.profile_id, candidate.profile_id)
            self.assertEqual(
                binding.payload_digest, hashlib.sha256(payload).hexdigest()
            )

    def test_candidate_requires_exact_ids_and_immutable_nonempty_bytes(self):
        valid = {receiver_id: b"payload" for receiver_id in range(RECEIVER_COUNT)}
        invalid_cases = (
            ("x" * 64, valid, ValueError),
            (_profile_id("x"), {0: b"x"}, ValueError),
            (
                _profile_id("x"),
                {True: b"x", 0: b"x", 2: b"x", 3: b"x", 4: b"x"},
                ValueError,
            ),
            (
                _profile_id("x"),
                {0: b"x", 1: bytearray(b"x"), 2: b"x", 3: b"x", 4: b"x"},
                TypeError,
            ),
            (
                _profile_id("x"),
                {0: b"x", 1: b"x", 2: b"", 3: b"x", 4: b"x"},
                ValueError,
            ),
        )
        for profile_id, payloads, error in invalid_cases:
            with self.subTest(profile_id=profile_id, error=error):
                with self.assertRaises(error):
                    InstallationProfileCandidate(profile_id, payloads)  # type: ignore[arg-type]


class CandidateFromResolvedTests(unittest.TestCase):
    def resolve(self, topology=INSTALLED_INSTALLATION_PROFILE_TOPOLOGY):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        library = InstallationProfileLibrary(Path(directory.name))
        encoded = encode_installation_profile(compile_installation_profile())
        receipt = library.publish(encoded)
        return library, library.resolve(receipt.id, topology)

    def test_encodes_exact_immutable_payload_for_each_topology_binding(self):
        _, resolved = self.resolve()

        candidate = candidate_from_resolved(resolved)

        self.assertEqual(candidate.profile_id, resolved.id)
        self.assertEqual(
            candidate.receiver_payloads,
            tuple(
                encode_installation_profile(resolved.receiver_profiles[receiver_id])
                for receiver_id in range(RECEIVER_COUNT)
            ),
        )
        self.assertTrue(
            all(type(payload) is bytes for payload in candidate.receiver_payloads)
        )
        self.assertEqual(
            tuple(
                resolved.receiver_profiles[receiver_id].strip_origin
                for receiver_id in range(RECEIVER_COUNT)
            ),
            (0, 8, 24, 16, 32),
        )
        self.assertEqual(
            tuple(
                resolved.receiver_profiles[receiver_id].reversed_strip_order
                for receiver_id in range(RECEIVER_COUNT)
            ),
            (False, False, True, True, False),
        )

    def test_topology_changes_receiver_payload_identity_not_global_identity(self):
        library, installed = self.resolve()
        identity = library.resolve(
            installed.id, IDENTITY_INSTALLATION_PROFILE_TOPOLOGY
        )

        installed_candidate = candidate_from_resolved(installed)
        identity_candidate = candidate_from_resolved(identity)

        self.assertEqual(installed_candidate.profile_id, identity_candidate.profile_id)
        self.assertEqual(
            installed_candidate.payload_for(0), identity_candidate.payload_for(0)
        )
        self.assertEqual(
            installed_candidate.payload_for(1), identity_candidate.payload_for(1)
        )
        self.assertNotEqual(
            installed_candidate.payload_for(2), identity_candidate.payload_for(2)
        )
        self.assertNotEqual(
            installed_candidate.payload_for(3), identity_candidate.payload_for(3)
        )

    def test_rejects_non_resolved_noncanonical_and_wrong_topology_views(self):
        library, installed = self.resolve()
        identity = library.resolve(
            installed.id, IDENTITY_INSTALLATION_PROFILE_TOPOLOGY
        )
        cases = (
            (object(), TypeError, "ResolvedInstallationProfile"),
            (
                replace(installed, encoded=installed.encoded + b"x"),
                ValueError,
                "not canonical",
            ),
            (
                replace(
                    installed,
                    receipt=replace(
                        installed.receipt,
                        id="f" * 64,
                        content_digest="f" * 64,
                    ),
                ),
                ValueError,
                "content ID",
            ),
            (
                replace(installed, receiver_profiles=object()),
                TypeError,
                "must be a mapping",
            ),
            (
                replace(
                    installed,
                    receiver_profiles={
                        receiver_id: identity.receiver_profiles[receiver_id]
                        for receiver_id in range(RECEIVER_COUNT)
                    },
                ),
                ValueError,
                "topology slice",
            ),
            (
                replace(
                    installed,
                    receiver_profiles={
                        receiver_id: installed.receiver_profiles[receiver_id]
                        for receiver_id in range(RECEIVER_COUNT - 1)
                    },
                ),
                ValueError,
                "exactly once",
            ),
        )
        for value, error, message in cases:
            with self.subTest(error=error, message=message):
                with self.assertRaisesRegex(error, message):
                    candidate_from_resolved(value)  # type: ignore[arg-type]


class InstallationProfileTransactionTests(unittest.TestCase):
    @staticmethod
    def wall(
        *, capacity_bytes: int | tuple[int, ...] = 512, reserve_bytes: int = 32
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
                for receiver_id in range(RECEIVER_COUNT)
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

    def test_non_fake_adapter_uses_structural_receiver_and_wall_interfaces(self):
        fake = self.wall()
        wall = _StructuralWallAdapter(fake)
        self.assertNotIsInstance(wall, FakeInstallationProfileWall)
        self.assertTrue(
            all(
                not isinstance(receiver, FakeInstallationProfileReceiver)
                for receiver in wall.receivers
            )
        )

        candidate = _candidate("adapter")
        result = InstallationProfileTransaction(wall).install(candidate)

        self.assertTrue(result.success)
        self.assertEqual(result.wall_status.active_profile_id, candidate.profile_id)
        for receiver_id, status in enumerate(result.wall_status.receiver_statuses):
            self.assertEqual(status.active_binding, candidate.binding_for(receiver_id))

    def test_success_rejects_noop_commits_even_when_wall_status_claims_candidate(self):
        fake = self.wall()
        prior = _candidate("prior")
        candidate = _candidate("candidate")
        fake.seed_active(prior)
        before = _binding_state(fake)
        wall = _AdversarialWallAdapter(
            fake,
            {
                receiver_id: {"commit_noop": True}
                for receiver_id in range(RECEIVER_COUNT)
            },
            dishonest_profile_id=candidate.profile_id,
        )

        result = InstallationProfileTransaction(wall).install(candidate)

        self.assertFalse(result.success)
        self.assertTrue(result.compensated)
        self.assertFalse(result.changed)
        self.assertIn("exact healthy unanimous candidate", result.error)
        self.assertEqual(_binding_state(fake), before)

    def test_success_rejects_adapter_that_drops_required_rollback_binding(self):
        fake = self.wall()
        prior = _candidate("prior")
        older = _candidate("older")
        candidate = _candidate("candidate")
        fake.seed_active(prior)
        fake.seed_rollback(older)
        before = _binding_state(fake)
        wall = _AdversarialWallAdapter(fake, {0: {"drop_rollback": True}})

        result = InstallationProfileTransaction(wall).install(candidate)

        self.assertFalse(result.success)
        self.assertTrue(result.compensated)
        self.assertFalse(result.changed)
        self.assertIn("exact healthy unanimous candidate", result.error)
        self.assertEqual(_binding_state(fake), before)

    def test_timeout_after_structural_stage_enters_full_compensation(self):
        fake = self.wall()
        prior = _candidate("prior")
        fake.seed_active(prior)
        before = _binding_state(fake)
        wall = _AdversarialWallAdapter(
            fake, {1: {"fail_after_stage": True}}
        )

        result = InstallationProfileTransaction(wall).install(
            _candidate("candidate")
        )

        self.assertFalse(result.success)
        self.assertTrue(result.compensated)
        self.assertFalse(result.changed)
        self.assertEqual(
            result.failed_phase, InstallationProfileTransactionPhase.STAGE
        )
        self.assertEqual(result.failed_receiver_id, 1)
        self.assertIn("stage timed out", result.error)
        self.assertEqual(_binding_state(fake), before)
        self.assertEqual(
            [receiver.compensation_calls for receiver in wall.receivers],
            [1] * RECEIVER_COUNT,
        )

    def test_noop_compensation_is_detected_and_fail_closed_despite_healthy_claim(self):
        fake = self.wall()
        prior = _candidate("prior")
        fake.seed_active(prior)
        before = _binding_state(fake)
        wall = _AdversarialWallAdapter(
            fake,
            {
                **{
                    receiver_id: {"compensation": "noop"}
                    for receiver_id in range(RECEIVER_COUNT)
                },
                2: {"fail_commit": True, "compensation": "noop"},
            },
            dishonest_profile_id=prior.profile_id,
        )

        result = InstallationProfileTransaction(wall).install(
            _candidate("candidate")
        )

        self.assertFalse(result.success)
        self.assertFalse(result.compensated)
        self.assertTrue(result.changed)
        self.assertEqual(
            [receiver.compensation_calls for receiver in wall.receivers],
            [1] * RECEIVER_COUNT,
        )
        self.assertNotEqual(_binding_state(fake), before)
        self.assertIn("receiver 0 binding snapshot differs", result.error)
        self.assertIn("receiver 1 binding snapshot differs", result.error)
        self.assertEqual(
            result.wall_status.health, InstallationProfileWallHealth.DEGRADED
        )
        self.assertIsNone(result.wall_status.active_profile_id)

    def test_compensation_failure_does_not_stop_later_receivers_or_claim_success(self):
        fake = self.wall()
        prior = _candidate("prior")
        fake.seed_active(prior)
        before = _binding_state(fake)
        wall = _AdversarialWallAdapter(
            fake,
            {
                0: {"compensation": "fail"},
                2: {"fail_commit": True},
            },
        )

        result = InstallationProfileTransaction(wall).install(
            _candidate("candidate")
        )

        self.assertFalse(result.success)
        self.assertFalse(result.compensated)
        self.assertTrue(result.changed)
        self.assertEqual(
            [receiver.compensation_calls for receiver in wall.receivers],
            [1] * RECEIVER_COUNT,
        )
        self.assertNotEqual(_binding_state(fake)[0], before[0])
        self.assertEqual(_binding_state(fake)[1:], before[1:])
        self.assertIn("receiver 0 compensation failed", result.error)
        self.assertIn("receiver 0 binding snapshot differs", result.error)
        self.assertFalse(result.wall_status.healthy)

    def test_compensation_requires_restored_bindings_to_remain_valid(self):
        fake = self.wall()
        prior = _candidate("prior")
        fake.seed_active(prior)
        wall = _AdversarialWallAdapter(
            fake,
            {
                0: {"invalid_after_compensation": True},
                1: {"fail_after_stage": True},
            },
        )

        result = InstallationProfileTransaction(wall).install(
            _candidate("candidate")
        )

        self.assertFalse(result.success)
        self.assertFalse(result.compensated)
        self.assertTrue(result.changed)
        self.assertIn("receiver 0 restored active binding is not valid", result.error)
        self.assertEqual(
            result.wall_status.health, InstallationProfileWallHealth.DEGRADED
        )

    def test_public_snapshot_and_status_values_are_immutable(self):
        wall = self.wall()
        snapshot = InstallationProfileReceiverSnapshot(None, None, None)

        with self.assertRaises(FrozenInstanceError):
            snapshot.active_binding = _candidate("x").binding_for(0)  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            wall.receiver(0).status().used_bytes = 0  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            wall.status().health = InstallationProfileWallHealth.DEGRADED  # type: ignore[misc]

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
            capacity_bytes=(180, 180, 120, 180, 180), reserve_bytes=20
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
            for receiver_id in range(RECEIVER_COUNT):
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
            {"capacity_bytes": (10, 10, 10, 10)},
            {"capacity_bytes": (10, 10, 10, 10, True)},
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

    def test_transaction_requires_exactly_five_structural_receivers_in_id_order(self):
        with self.assertRaisesRegex(TypeError, "wall interface"):
            InstallationProfileTransaction(object())  # type: ignore[arg-type]

        wall = _StructuralWallAdapter(
            FakeInstallationProfileWall(capacity_bytes=128)
        )
        wall.receivers = tuple(reversed(wall.receivers))
        with self.assertRaisesRegex(TypeError, "logical ID order"):
            InstallationProfileTransaction(wall)


if __name__ == "__main__":
    unittest.main()
