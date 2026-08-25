"""Acceptance coverage for the Pi-authoritative installation-profile library."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import numpy as np

from animation.core.installation_profile import (
    compile_installation_profile,
    decode_installation_profile,
    encode_installation_profile,
)
from animation.core.installation_profile_library import (
    PROFILE_FILENAME,
    PROFILES_DIRECTORY,
    RECEIPT_FILENAME,
    InstallationProfileLibrary,
    InstallationProfileLibraryError,
    InstallationProfileNotFoundError,
)
from animation.core.installation_profile_topology import (
    IDENTITY_INSTALLATION_PROFILE_TOPOLOGY,
    INSTALLED_INSTALLATION_PROFILE_TOPOLOGY,
    InstallationProfileTopology,
    slice_installation_profile,
)


ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = ROOT / "tests" / "fixtures" / "installation_profile_v1.bin"
EXPECTED_PROFILE_ID = (
    "ce457a14efd131395507c449f35a7701"
    "ca78ddca059620dc3757806ef553ca6a"
)
EXPECTED_CALIBRATION_DIGEST = (
    "580aca497078fe64a6b182e6ff0de9c"
    "92c58ab14a039062e95ece1961415ffe3"
)
EXPECTED_FILE_SHA256 = (
    "3e9fd83f990f9d7fcd3a7e958212fad5"
    "fab82941d2e9973c3d0b0c19bdbcb918"
)
FIXED_PUBLISH_TIME = datetime(2026, 8, 14, 17, 0, tzinfo=timezone.utc)


def _rehash_content_digest(data: bytearray) -> None:
    data[68:100] = bytes(32)
    data[68:100] = hashlib.sha256(data).digest()


class InstallationProfileLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)
        self.library_root = (
            self.base / "installation_profile_library"
        ).resolve(strict=False)
        self.library = InstallationProfileLibrary(
            self.library_root, clock=lambda: FIXED_PUBLISH_TIME
        )
        self.golden = GOLDEN_PATH.read_bytes()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def entry_path(self, profile_id: str = EXPECTED_PROFILE_ID) -> Path:
        return self.library_root / PROFILES_DIRECTORY / profile_id

    @staticmethod
    def topology(**overrides: object) -> InstallationProfileTopology:
        values: dict[str, object] = {
            "logical_to_transport_routes": (
                (0, 0), (0, 1), (1, 1), (1, 0), (1, 2)
            ),
            "physical_lane_order": (0, 1, 3, 2, 4),
            "reverse_host_strips_by_logical_receiver": (
                False,
                False,
                True,
                True,
                False,
            ),
            "reverse_native_strips_by_logical_receiver": (
                False,
                False,
                True,
                True,
                False,
            ),
        }
        values.update(overrides)
        return InstallationProfileTopology(**values)

    def publish_golden(self):
        return self.library.publish(self.golden)

    def rewrite_immutable_file(self, path: Path, payload: bytes) -> None:
        path.chmod(0o644)
        path.write_bytes(payload)
        path.chmod(0o444)

    def test_golden_publish_receipt_layout_and_installed_resolution(self) -> None:
        receipt = self.publish_golden()

        self.assertEqual(receipt.schema_version, 1)
        self.assertEqual(receipt.profile_format_version, 1)
        self.assertEqual(receipt.id, EXPECTED_PROFILE_ID)
        self.assertEqual(receipt.content_digest, EXPECTED_PROFILE_ID)
        self.assertEqual(receipt.calibration_digest, EXPECTED_CALIBRATION_DIGEST)
        self.assertEqual(receipt.file_sha256, EXPECTED_FILE_SHA256)
        self.assertEqual(receipt.size, 41_314)
        self.assertEqual(receipt.published_at, "2026-08-14T17:00:00Z")

        entry = self.entry_path()
        self.assertEqual(
            {path.name for path in entry.iterdir()},
            {PROFILE_FILENAME, RECEIPT_FILENAME},
        )
        self.assertEqual((entry / PROFILE_FILENAME).read_bytes(), self.golden)
        self.assertEqual(
            json.loads((entry / RECEIPT_FILENAME).read_text(encoding="utf-8")),
            receipt.to_dict(),
        )
        self.assertFalse(entry.stat().st_mode & 0o222)
        self.assertFalse((entry / PROFILE_FILENAME).stat().st_mode & 0o222)
        self.assertFalse((entry / RECEIPT_FILENAME).stat().st_mode & 0o222)

        resolved = self.library.resolve(
            receipt.id, INSTALLED_INSTALLATION_PROFILE_TOPOLOGY
        )
        self.assertEqual(resolved.encoded, self.golden)
        self.assertEqual(resolved.global_profile.category.shape, (33, 138))
        self.assertEqual(
            tuple(
                (
                    logical_id,
                    profile.strip_origin,
                    profile.reversed_strip_order,
                )
                for logical_id, profile in resolved.receiver_profiles.items()
            ),
            (
                (0, 0, False),
                (1, 8, False),
                (3, 16, True),
                (2, 24, True),
                (4, 32, False),
            ),
        )

    def test_identical_republish_is_idempotent_and_retains_original_receipt(self) -> None:
        first = self.publish_golden()
        entry = self.entry_path()
        before_stat = entry.stat()
        before_receipt = (entry / RECEIPT_FILENAME).read_bytes()

        later_library = InstallationProfileLibrary(
            self.library_root,
            clock=lambda: datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
        second = later_library.publish(self.golden)
        clock_independent = InstallationProfileLibrary(
            self.library_root,
            clock=lambda: (_ for _ in ()).throw(
                AssertionError("idempotent publish must retain the stored timestamp")
            ),
        ).publish(self.golden)

        self.assertEqual(second, first)
        self.assertEqual(clock_independent, first)
        self.assertEqual(second.published_at, "2026-08-14T17:00:00Z")
        self.assertEqual(entry.stat().st_ino, before_stat.st_ino)
        self.assertEqual((entry / RECEIPT_FILENAME).read_bytes(), before_receipt)
        self.assertEqual(
            [path.name for path in (self.library_root / PROFILES_DIRECTORY).iterdir()],
            [EXPECTED_PROFILE_ID],
        )

    def test_concurrent_publishers_converge_on_one_complete_immutable_entry(self) -> None:
        barrier = threading.Barrier(2)
        libraries = (
            InstallationProfileLibrary(
                self.library_root, clock=lambda: FIXED_PUBLISH_TIME
            ),
            InstallationProfileLibrary(
                self.library_root,
                clock=lambda: datetime(2030, 1, 1, tzinfo=timezone.utc),
            ),
        )

        def publish(library: InstallationProfileLibrary):
            barrier.wait(timeout=2.0)
            return library.publish(self.golden)

        with ThreadPoolExecutor(max_workers=2) as executor:
            receipts = tuple(executor.map(publish, libraries))

        self.assertEqual(receipts[0], receipts[1])
        entry = self.entry_path()
        self.assertEqual(
            {path.name for path in entry.iterdir()},
            {PROFILE_FILENAME, RECEIPT_FILENAME},
        )
        self.assertEqual((entry / PROFILE_FILENAME).read_bytes(), self.golden)
        self.assertFalse(entry.stat().st_mode & 0o222)
        self.assertEqual(
            {path.name for path in (self.library_root / PROFILES_DIRECTORY).iterdir()},
            {EXPECTED_PROFILE_ID},
        )

    def test_invalid_malformed_noncanonical_and_slice_inputs_never_mutate_root(self) -> None:
        noncanonical = bytearray(self.golden)
        noncanonical[100] = 1  # Reserved header byte; keep the content hash valid.
        _rehash_content_digest(noncanonical)
        global_profile = decode_installation_profile(self.golden)
        receiver_slice = slice_installation_profile(
            global_profile, IDENTITY_INSTALLATION_PROFILE_TOPOLOGY
        )[0]
        cases = (
            bytearray(self.golden),
            b"LGIP",
            bytes(noncanonical),
            encode_installation_profile(receiver_slice),
        )
        for value in cases:
            with self.subTest(value=type(value).__name__, size=len(value)):
                isolated_root = self.base / f"invalid-{len(value)}-{type(value).__name__}"
                isolated = InstallationProfileLibrary(isolated_root)
                with self.assertRaises(InstallationProfileLibraryError):
                    isolated.publish(value)  # type: ignore[arg-type]
                self.assertFalse(isolated_root.exists())

    def test_resolution_rejects_traversal_invalid_ids_missing_and_symlink_escape(self) -> None:
        invalid_ids = (
            "../" + EXPECTED_PROFILE_ID,
            EXPECTED_PROFILE_ID.upper(),
            "g" * 64,
            "a" * 63,
            "/" + EXPECTED_PROFILE_ID,
        )
        for profile_id in invalid_ids:
            with self.subTest(profile_id=profile_id), self.assertRaises(
                InstallationProfileLibraryError
            ):
                self.library.resolve(profile_id)
        self.assertFalse(self.library_root.exists())

        with self.assertRaises(InstallationProfileNotFoundError):
            self.library.resolve("a" * 64)
        self.assertFalse(self.library_root.exists())

        outside = self.base / "outside"
        outside.mkdir()
        self.library_root.mkdir()
        (self.library_root / PROFILES_DIRECTORY).symlink_to(outside)
        with self.assertRaisesRegex(InstallationProfileLibraryError, "outside"):
            self.library.publish(self.golden)
        self.assertEqual(list(outside.iterdir()), [])

    def test_corrupt_existing_entry_fails_closed_for_resolve_and_republish(self) -> None:
        self.publish_golden()
        profile_path = self.entry_path() / PROFILE_FILENAME
        corrupt = bytearray(profile_path.read_bytes())
        corrupt[-1] ^= 1
        self.rewrite_immutable_file(profile_path, bytes(corrupt))

        with self.assertRaisesRegex(InstallationProfileLibraryError, "SHA-256"):
            self.library.resolve(EXPECTED_PROFILE_ID)
        with self.assertRaisesRegex(InstallationProfileLibraryError, "SHA-256"):
            self.library.publish(self.golden)
        self.assertEqual(profile_path.read_bytes(), bytes(corrupt))

    def test_conflicting_incomplete_entry_is_not_repaired_implicitly(self) -> None:
        entry = self.entry_path()
        entry.mkdir(parents=True)
        entry.chmod(0o555)

        with self.assertRaisesRegex(
            InstallationProfileLibraryError, "missing or unexpected"
        ):
            self.publish_golden()
        self.assertEqual(list(entry.iterdir()), [])

        extra_root = self.base / "library-with-extra-member"
        extra_library = InstallationProfileLibrary(
            extra_root, clock=lambda: FIXED_PUBLISH_TIME
        )
        receipt = extra_library.publish(self.golden)
        extra_entry = extra_root / PROFILES_DIRECTORY / receipt.id
        extra_entry.chmod(0o755)
        (extra_entry / "unexpected.bin").write_bytes(b"not managed")
        extra_entry.chmod(0o555)
        with self.assertRaisesRegex(
            InstallationProfileLibraryError, "missing or unexpected"
        ):
            extra_library.resolve(receipt.id)

    def test_atomic_rename_failure_removes_staging_and_exposes_no_entry(self) -> None:
        with mock.patch(
            "animation.core.installation_profile_library.os.rename",
            side_effect=OSError("injected rename failure"),
        ):
            with self.assertRaisesRegex(
                InstallationProfileLibraryError, "injected rename failure"
            ):
                self.publish_golden()

        profiles = self.library_root / PROFILES_DIRECTORY
        self.assertTrue(profiles.is_dir())
        self.assertEqual(list(profiles.iterdir()), [])
        self.assertFalse(self.entry_path().exists())

    def test_resolved_bytes_views_receipt_and_mapping_are_immutable(self) -> None:
        receipt = self.publish_golden()
        resolved = self.library.resolve(receipt.id)

        self.assertIsInstance(resolved.encoded, bytes)
        with self.assertRaises(FrozenInstanceError):
            receipt.size = 0  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            resolved.encoded = b""  # type: ignore[misc]
        with self.assertRaises(TypeError):
            resolved.receiver_profiles[0] = resolved.receiver_profiles[1]  # type: ignore[index]
        for profile in (
            resolved.global_profile,
            *resolved.receiver_profiles.values(),
        ):
            self.assertFalse(profile.category.flags.writeable)
            with self.assertRaises(ValueError):
                profile.category.flat[0] = 0

    def test_semantic_cache_uses_digest_lane_order_and_native_direction_only(self) -> None:
        first_receipt = self.publish_golden()
        baseline_topology = self.topology()
        inert_policy_change = self.topology(
            logical_to_transport_routes=(
                (9, 9), (9, 8), (8, 8), (8, 9), (7, 7)
            ),
            reverse_host_strips_by_logical_receiver=(
                True, True, False, False, True
            ),
        )
        lane_change = self.topology(physical_lane_order=(1, 0, 3, 2, 4))
        native_change = self.topology(
            reverse_native_strips_by_logical_receiver=(
                True,
                False,
                True,
                True,
                False,
            )
        )

        baseline = self.library.resolve(first_receipt.id, baseline_topology)
        inert = self.library.resolve(first_receipt.id, inert_policy_change)
        swapped = self.library.resolve(first_receipt.id, lane_change)
        reversed_native = self.library.resolve(first_receipt.id, native_change)

        self.assertIs(inert.global_profile, baseline.global_profile)
        self.assertIs(inert.receiver_profiles, baseline.receiver_profiles)
        self.assertIsNot(swapped.receiver_profiles, baseline.receiver_profiles)
        self.assertIsNot(
            reversed_native.receiver_profiles, baseline.receiver_profiles
        )
        self.assertEqual(swapped.receiver_profiles[0].strip_origin, 8)
        self.assertTrue(reversed_native.receiver_profiles[0].reversed_strip_order)
        for logical_id in range(5):
            for section_name in (
                "category",
                "clearance",
                "globe_region",
                "distance",
                "normal_x",
                "normal_y",
            ):
                np.testing.assert_array_equal(
                    getattr(inert.receiver_profiles[logical_id], section_name),
                    getattr(baseline.receiver_profiles[logical_id], section_name),
                )

        second_bytes = encode_installation_profile(
            compile_installation_profile(clearance_radius=2)
        )
        second_receipt = self.library.publish(second_bytes)
        second = self.library.resolve(second_receipt.id, baseline_topology)
        self.assertNotEqual(second.id, baseline.id)
        self.assertIsNot(second.global_profile, baseline.global_profile)
        self.assertIsNot(second.receiver_profiles, baseline.receiver_profiles)

    def test_cached_resolution_revalidates_changed_bytes_and_receipt(self) -> None:
        receipt = self.publish_golden()
        initial = self.library.resolve(receipt.id)
        entry = self.entry_path()
        profile_path = entry / PROFILE_FILENAME
        receipt_path = entry / RECEIPT_FILENAME

        # Keep the outer file hash internally consistent so this reaches the
        # LGIP decoder instead of being rejected only by the receipt hash.
        corrupt = bytearray(self.golden)
        corrupt[-1] ^= 1
        receipt_payload = receipt.to_dict()
        receipt_payload["file_sha256"] = hashlib.sha256(corrupt).hexdigest()
        self.rewrite_immutable_file(profile_path, bytes(corrupt))
        self.rewrite_immutable_file(
            receipt_path,
            json.dumps(
                receipt_payload, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            + b"\n",
        )
        with self.assertRaisesRegex(InstallationProfileLibraryError, "invalid"):
            self.library.resolve(receipt.id)

        # Restore the exact bytes and then prove receipt validation also occurs
        # on a warm artifact/slice cache.
        self.rewrite_immutable_file(profile_path, self.golden)
        receipt_payload["file_sha256"] = EXPECTED_FILE_SHA256
        receipt_payload["calibration_digest"] = "0" * 64
        self.rewrite_immutable_file(
            receipt_path,
            json.dumps(
                receipt_payload, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            + b"\n",
        )
        with self.assertRaisesRegex(
            InstallationProfileLibraryError, "calibration digest"
        ):
            self.library.resolve(receipt.id)
        self.assertEqual(initial.encoded, self.golden)

    def test_receipt_fails_closed_on_duplicate_unknown_and_wrong_typed_fields(self) -> None:
        receipt = self.publish_golden()
        receipt_path = self.entry_path() / RECEIPT_FILENAME
        canonical = receipt.to_dict()
        cases = (
            (
                (
                    '{"schema_version":1,"schema_version":1,'
                    + ",".join(
                        f"{json.dumps(key)}:{json.dumps(value)}"
                        for key, value in canonical.items()
                        if key != "schema_version"
                    )
                    + "}\n"
                ).encode("utf-8"),
                "duplicate",
            ),
            (
                json.dumps({**canonical, "extra": True}).encode("utf-8"),
                "exactly",
            ),
            (
                json.dumps({**canonical, "size": True}).encode("utf-8"),
                "non-negative integer",
            ),
        )
        for payload, message in cases:
            with self.subTest(message=message):
                self.rewrite_immutable_file(receipt_path, payload)
                with self.assertRaisesRegex(InstallationProfileLibraryError, message):
                    self.library.resolve(receipt.id)

    def test_artifact_path_is_managed_and_accepts_only_exact_digest_ids(self) -> None:
        self.publish_golden()
        path = self.library.artifact_path(EXPECTED_PROFILE_ID)
        self.assertEqual(path, self.entry_path() / PROFILE_FILENAME)
        self.assertEqual(
            os.path.commonpath((self.library_root, path)),
            os.fspath(self.library_root),
        )
        for invalid in (".", "..", "../escape", "A" * 64, "a" * 65):
            with self.subTest(invalid=invalid), self.assertRaises(
                InstallationProfileLibraryError
            ):
                self.library.artifact_path(invalid)
        self.assertEqual(
            {path.name for path in (self.library_root / PROFILES_DIRECTORY).iterdir()},
            {EXPECTED_PROFILE_ID},
        )


if __name__ == "__main__":
    unittest.main()
