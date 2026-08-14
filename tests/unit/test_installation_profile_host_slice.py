"""Cross-lane acceptance for the portable Phase 3C host profile slice."""

from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from animation.core.installation_profile import (
    compile_installation_profile,
    encode_installation_profile,
)
from animation.core.installation_profile_library import InstallationProfileLibrary
from animation.core.installation_profile_topology import (
    INSTALLED_INSTALLATION_PROFILE_TOPOLOGY,
)
from animation.core.installation_profile_transaction import (
    FakeInstallationProfileFault,
    FakeInstallationProfileWall,
    InstallationProfileCandidate,
    InstallationProfileTransaction,
    InstallationProfileTransactionPhase,
)


ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = ROOT / "tests/fixtures/installation_profile_v1.bin"


def _candidate_from_library(
    library: InstallationProfileLibrary,
    profile_id: str,
) -> InstallationProfileCandidate:
    resolved = library.resolve(profile_id, INSTALLED_INSTALLATION_PROFILE_TOPOLOGY)
    payloads = {
        logical_id: encode_installation_profile(profile)
        for logical_id, profile in resolved.receiver_profiles.items()
    }
    return InstallationProfileCandidate(resolved.id, payloads)


class InstallationProfileHostSliceAcceptanceTests(unittest.TestCase):
    def test_managed_global_profile_drives_four_bound_receiver_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            library = InstallationProfileLibrary(Path(directory))
            receipt = library.publish(GOLDEN_PATH.read_bytes())
            candidate = _candidate_from_library(library, receipt.id)
            wall = FakeInstallationProfileWall(
                capacity_bytes=24_000,
                reserve_bytes=2_000,
            )

            result = InstallationProfileTransaction(wall).install(candidate)

            self.assertTrue(result.success)
            self.assertTrue(result.wall_status.healthy)
            self.assertEqual(result.wall_status.active_profile_id, receipt.id)
            resolved = library.resolve(
                receipt.id, INSTALLED_INSTALLATION_PROFILE_TOPOLOGY
            )
            for logical_id, status in enumerate(result.wall_status.receiver_statuses):
                expected_payload = encode_installation_profile(
                    resolved.receiver_profiles[logical_id]
                )
                self.assertEqual(
                    status.active_binding,
                    candidate.binding_for(logical_id),
                )
                self.assertEqual(
                    status.active_binding.payload_digest,
                    hashlib.sha256(expected_payload).hexdigest(),
                )

    def test_failed_candidate_compensates_then_retry_promotes_prior_to_rollback(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            library = InstallationProfileLibrary(Path(directory))
            first_receipt = library.publish(GOLDEN_PATH.read_bytes())
            second_receipt = library.publish(
                encode_installation_profile(
                    compile_installation_profile(clearance_radius=2)
                )
            )
            first = _candidate_from_library(library, first_receipt.id)
            second = _candidate_from_library(library, second_receipt.id)
            wall = FakeInstallationProfileWall(
                capacity_bytes=40_000,
                reserve_bytes=2_000,
            )
            transaction = InstallationProfileTransaction(wall)
            self.assertTrue(transaction.install(first).success)

            failed = transaction.install(
                second,
                faults=(
                    FakeInstallationProfileFault(
                        2,
                        InstallationProfileTransactionPhase.COMMIT,
                    ),
                ),
            )

            self.assertFalse(failed.success)
            self.assertTrue(failed.compensated)
            self.assertTrue(failed.wall_status.healthy)
            self.assertEqual(failed.wall_status.active_profile_id, first.profile_id)

            retried = transaction.install(second)
            self.assertTrue(retried.success)
            self.assertEqual(retried.wall_status.active_profile_id, second.profile_id)
            for logical_id, status in enumerate(retried.wall_status.receiver_statuses):
                self.assertEqual(
                    status.rollback_binding,
                    first.binding_for(logical_id),
                )


if __name__ == "__main__":
    unittest.main()
