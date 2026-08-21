"""Acceptance coverage for the Pi-authoritative native background library."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from animation.core.native_background_fake_cache import (
    FakeNativeBackgroundError,
    FakeNativeReceiver,
    FakeNativeReceiverWall,
)
from animation.core.native_background_library import (
    BUNDLE_FILENAME,
    BUNDLES_DIRECTORY,
    PAYLOADS_DIRECTORY,
    RECEIPT_FILENAME,
    NativeBackgroundLibrary,
    NativeBackgroundLibraryError,
    NativeBackgroundNotFoundError,
)


FIXED_TIME = datetime(2026, 8, 21, 18, 0, tzinfo=timezone.utc)
PAYLOAD_PATH = "payload/module.so"


@dataclass(frozen=True)
class _Verified:
    raw: bytes
    manifest: dict[str, object]
    members: dict[str, bytes]
    bundle_digest: str
    payload_digest: str


def _verified(raw: bytes, payload: bytes, plugin_id: str = "aurora_curtains_native"):
    return _Verified(
        raw=raw,
        manifest={"plugin_id": plugin_id},
        members={PAYLOAD_PATH: payload},
        bundle_digest=hashlib.sha256(raw).hexdigest(),
        payload_digest=hashlib.sha256(payload).hexdigest(),
    )


class NativeBackgroundLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "receiver_library/native_backgrounds"
        self.library = NativeBackgroundLibrary(self.root, clock=lambda: FIXED_TIME)
        self.first = _verified(b"canonical-bundle-one", b"shared-module")
        self.second = _verified(
            b"canonical-bundle-two", b"shared-module", "aurora_curtains_variant"
        )
        self.by_raw = {
            self.first.raw: (self.first, self.first.members[PAYLOAD_PATH], "aurora_curtains_native"),
            self.second.raw: (
                self.second,
                self.second.members[PAYLOAD_PATH],
                "aurora_curtains_variant",
            ),
        }
        self.inspect_patch = mock.patch(
            "animation.core.native_background_library._inspect_bundle",
            side_effect=lambda source: self.by_raw[
                source.read_bytes() if isinstance(source, Path) else source
            ],
        )
        self.inspect_patch.start()

    def tearDown(self) -> None:
        self.inspect_patch.stop()
        self.temporary.cleanup()

    def publish(self, verified: _Verified | None = None):
        return self.library.publish((verified or self.first).raw)

    def test_publish_layout_receipt_permissions_and_resolution(self) -> None:
        receipt = self.publish()
        self.assertEqual(receipt.schema_version, 1)
        self.assertEqual(receipt.package_id, "aurora_curtains_native")
        self.assertEqual(receipt.bundle_digest, self.first.bundle_digest)
        self.assertEqual(receipt.payload_digest, self.first.payload_digest)
        self.assertEqual(receipt.published_at, "2026-08-21T18:00:00Z")

        entry = self.root / BUNDLES_DIRECTORY / self.first.bundle_digest
        payload = self.root / PAYLOADS_DIRECTORY / f"{self.first.payload_digest}.so"
        self.assertEqual(
            {path.name for path in entry.iterdir()},
            {BUNDLE_FILENAME, RECEIPT_FILENAME},
        )
        self.assertEqual((entry / BUNDLE_FILENAME).read_bytes(), self.first.raw)
        self.assertEqual(payload.read_bytes(), b"shared-module")
        self.assertFalse(entry.stat().st_mode & 0o222)
        self.assertFalse((entry / BUNDLE_FILENAME).stat().st_mode & 0o222)
        self.assertFalse((entry / RECEIPT_FILENAME).stat().st_mode & 0o222)
        self.assertFalse(payload.stat().st_mode & 0o222)
        self.assertEqual(
            json.loads((entry / RECEIPT_FILENAME).read_text(encoding="utf-8")),
            receipt.to_dict(),
        )

        resolved = self.library.resolve(receipt.bundle_digest)
        self.assertEqual(resolved.bundle, self.first.raw)
        self.assertEqual(resolved.payload, b"shared-module")
        self.assertEqual(resolved.bundle_path, entry / BUNDLE_FILENAME)
        self.assertEqual(resolved.payload_path, payload)

    def test_identical_publication_and_concurrent_publish_are_idempotent(self) -> None:
        first = self.publish()
        entry = self.root / BUNDLES_DIRECTORY / first.bundle_digest
        before = entry.stat().st_mtime_ns
        self.assertEqual(self.publish(), first)
        self.assertEqual(entry.stat().st_mtime_ns, before)

        with ThreadPoolExecutor(max_workers=8) as executor:
            receipts = list(executor.map(lambda _index: self.publish(), range(16)))
        self.assertTrue(all(receipt == first for receipt in receipts))
        self.assertEqual(
            [path.name for path in (self.root / BUNDLES_DIRECTORY).iterdir()],
            [first.bundle_digest],
        )

    def test_manifest_only_bundle_change_reuses_one_payload_object(self) -> None:
        first = self.publish(self.first)
        second = self.publish(self.second)
        self.assertNotEqual(first.bundle_digest, second.bundle_digest)
        self.assertEqual(first.payload_digest, second.payload_digest)
        self.assertEqual(
            [path.name for path in (self.root / PAYLOADS_DIRECTORY).iterdir()],
            [f"{first.payload_digest}.so"],
        )

    def test_invalid_input_and_missing_resolution_do_not_create_library(self) -> None:
        self.inspect_patch.stop()
        with mock.patch(
            "animation.core.native_background_library._inspect_bundle",
            side_effect=NativeBackgroundLibraryError("invalid"),
        ):
            with self.assertRaisesRegex(NativeBackgroundLibraryError, "invalid"):
                self.library.publish(b"bad")
        self.inspect_patch.start()
        self.assertFalse(self.root.exists())
        with self.assertRaises(NativeBackgroundNotFoundError):
            self.library.resolve("a" * 64)
        self.assertFalse(self.root.exists())

    def test_corrupt_existing_bundle_receipt_and_payload_fail_closed(self) -> None:
        receipt = self.publish()
        entry = self.root / BUNDLES_DIRECTORY / receipt.bundle_digest
        payload = self.root / PAYLOADS_DIRECTORY / f"{receipt.payload_digest}.so"
        cases = (
            (entry / BUNDLE_FILENAME, b"corrupt-bundle"),
            (entry / RECEIPT_FILENAME, b"{}\n"),
            (payload, b"corrupt-payload"),
        )
        originals = {path: path.read_bytes() for path, _contents in cases}
        for path, contents in cases:
            with self.subTest(path=path.name):
                path.chmod(0o644)
                path.write_bytes(contents)
                path.chmod(0o444)
                with self.assertRaises((NativeBackgroundLibraryError, KeyError)):
                    self.library.resolve(receipt.bundle_digest)
                path.chmod(0o644)
                path.write_bytes(originals[path])
                path.chmod(0o444)

    def test_unexpected_members_mutability_traversal_and_symlink_root_reject(self) -> None:
        receipt = self.publish()
        entry = self.root / BUNDLES_DIRECTORY / receipt.bundle_digest
        entry.chmod(0o755)
        (entry / "unexpected").write_bytes(b"x")
        entry.chmod(0o555)
        with self.assertRaisesRegex(NativeBackgroundLibraryError, "unexpected"):
            self.library.resolve(receipt.bundle_digest)

        for invalid in ("../" + "a" * 64, "A" * 64, "g" * 64, "a"):
            with self.subTest(invalid=invalid), self.assertRaises(
                NativeBackgroundLibraryError
            ):
                self.library.resolve(invalid)

        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        symlink_root = Path(self.temporary.name) / "symlink/native_backgrounds"
        symlink_root.parent.mkdir()
        symlink_root.symlink_to(outside, target_is_directory=True)
        library = NativeBackgroundLibrary(symlink_root, clock=lambda: FIXED_TIME)
        with self.assertRaisesRegex(NativeBackgroundLibraryError, "real directory"):
            library.publish(self.first.raw)

    def test_atomic_rename_failure_leaves_no_bundle_or_orphan_payload(self) -> None:
        with mock.patch(
            "animation.core.native_background_library.os.rename",
            side_effect=OSError("injected rename failure"),
        ):
            with self.assertRaisesRegex(NativeBackgroundLibraryError, "rename failure"):
                self.publish()
        self.assertEqual(list((self.root / BUNDLES_DIRECTORY).iterdir()), [])
        self.assertEqual(list((self.root / PAYLOADS_DIRECTORY).iterdir()), [])

    def test_payload_claim_never_replaces_a_racing_destination(self) -> None:
        with mock.patch(
            "animation.core.native_background_library.os.link",
            side_effect=FileExistsError("injected race"),
        ):
            with self.assertRaisesRegex(
                NativeBackgroundLibraryError, "appeared during publication"
            ):
                self.publish()
        self.assertEqual(list((self.root / BUNDLES_DIRECTORY).iterdir()), [])
        self.assertEqual(list((self.root / PAYLOADS_DIRECTORY).iterdir()), [])


class FakeNativeReceiverWallTests(unittest.TestCase):
    @staticmethod
    def resolved(bundle: bytes = b"bundle", payload: bytes = b"payload"):
        return type(
            "Resolved",
            (),
            {
                "bundle_digest": hashlib.sha256(bundle).hexdigest(),
                "payload_digest": hashlib.sha256(payload).hexdigest(),
                "payload": payload,
            },
        )()

    def test_install_and_activate_are_unanimous_and_idempotently_reuse_payload(self) -> None:
        wall = FakeNativeReceiverWall()
        resolved = self.resolved()
        installed = wall.install(resolved)
        self.assertEqual(installed.outcome, "installed")
        self.assertEqual(set(installed.active_by_receiver.values()), {None})
        used = [receiver.used for receiver in wall.receivers]
        wall.install(resolved)
        self.assertEqual([receiver.used for receiver in wall.receivers], used)

        active = wall.activate(resolved.bundle_digest)
        self.assertEqual(active.outcome, "active")
        self.assertEqual(
            set(active.active_by_receiver.values()), {resolved.bundle_digest}
        )

    def test_capacity_preflight_fails_before_any_receiver_mutation(self) -> None:
        receivers = tuple(
            FakeNativeReceiver(index, capacity=4, reserve=1) for index in range(4)
        )
        wall = FakeNativeReceiverWall(receivers)
        with self.assertRaisesRegex(FakeNativeBackgroundError, "capacity"):
            wall.install(self.resolved(payload=b"too-large"))
        self.assertTrue(all(not receiver.payloads for receiver in receivers))

    def test_install_failure_compensates_every_receiver(self) -> None:
        def fail(phase: str, logical_id: int) -> None:
            if phase == "verify" and logical_id == 2:
                raise RuntimeError("injected")

        wall = FakeNativeReceiverWall(failure_injector=fail)
        with self.assertRaisesRegex(FakeNativeBackgroundError, "recovered=true"):
            wall.install(self.resolved())
        self.assertTrue(all(not receiver.payloads for receiver in wall.receivers))
        self.assertTrue(all(receiver.staged is None for receiver in wall.receivers))

    def test_conflicting_preexisting_payload_fails_closed(self) -> None:
        wall = FakeNativeReceiverWall()
        resolved = self.resolved()
        wall.receivers[0].payloads[resolved.payload_digest] = b"conflicting-bytes"
        before = dict(wall.receivers[0].payloads)

        with self.assertRaisesRegex(FakeNativeBackgroundError, "conflicting payload"):
            wall.install(resolved)

        self.assertEqual(wall.receivers[0].payloads, before)
        self.assertTrue(all(not receiver.installed for receiver in wall.receivers))

    def test_install_reports_when_compensation_itself_fails(self) -> None:
        def fail(phase: str, logical_id: int) -> None:
            if phase == "stage" and logical_id == 2:
                raise RuntimeError("injected install failure")
            if phase == "compensate_install" and logical_id == 0:
                raise RuntimeError("injected compensation failure")

        wall = FakeNativeReceiverWall(failure_injector=fail)
        with self.assertRaisesRegex(FakeNativeBackgroundError, "recovered=false"):
            wall.install(self.resolved())
        self.assertTrue(wall.receivers[0].payloads)
        self.assertIsNotNone(wall.receivers[0].staged)
        self.assertTrue(all(not receiver.payloads for receiver in wall.receivers[1:]))

    def test_partial_activation_failure_restores_exact_prior_bindings(self) -> None:
        state = {"fail": False}

        def fail(phase: str, logical_id: int) -> None:
            if state["fail"] and phase == "activate" and logical_id == 2:
                raise RuntimeError("injected")

        wall = FakeNativeReceiverWall(failure_injector=fail)
        prior = self.resolved(b"prior", b"prior-payload")
        candidate = self.resolved(b"candidate", b"candidate-payload")
        wall.install(prior)
        wall.activate(prior.bundle_digest)
        wall.install(candidate)
        state["fail"] = True
        with self.assertRaisesRegex(FakeNativeBackgroundError, "recovered=true"):
            wall.activate(candidate.bundle_digest)
        self.assertEqual(
            {receiver.active.bundle_digest for receiver in wall.receivers},
            {prior.bundle_digest},
        )

    def test_activation_reports_when_compensation_itself_fails(self) -> None:
        state = {"fail": False}

        def fail(phase: str, logical_id: int) -> None:
            if state["fail"] and phase == "activate" and logical_id == 2:
                raise RuntimeError("injected activation failure")
            if state["fail"] and phase == "compensate_activate" and logical_id == 0:
                raise RuntimeError("injected compensation failure")

        wall = FakeNativeReceiverWall(failure_injector=fail)
        prior = self.resolved(b"prior", b"prior-payload")
        candidate = self.resolved(b"candidate", b"candidate-payload")
        wall.install(prior)
        wall.activate(prior.bundle_digest)
        wall.install(candidate)
        state["fail"] = True

        with self.assertRaisesRegex(FakeNativeBackgroundError, "recovered=false"):
            wall.activate(candidate.bundle_digest)

        self.assertEqual(wall.receivers[0].active.bundle_digest, candidate.bundle_digest)
        self.assertEqual(
            {receiver.active.bundle_digest for receiver in wall.receivers[1:]},
            {prior.bundle_digest},
        )


if __name__ == "__main__":
    unittest.main()
