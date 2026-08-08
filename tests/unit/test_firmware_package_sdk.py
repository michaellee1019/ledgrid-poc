from __future__ import annotations

import hashlib
import io
import json
import stat
import struct
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path

from firmware_animations import (
    ActivePackageError,
    FirmwareAnimationLibrary,
    PackageValidationError,
    assemble_wall_frames,
    build_frame_package,
    build_native_package,
    decode_track,
    encode_image_tracks,
    inspect_package,
    load_image_frames,
    rgb565_to_rgb888,
    validate_parameters,
)
from firmware_animations.archive import deterministic_zip, read_safe_zip
from firmware_animations.constants import INDEX_PATH, MANIFEST_PATH, MAX_TRACK_BYTES, PREVIEW_PATH, SIGNATURE_PATH, track_path
from firmware_animations.examples.generate_sources import save_sources
from firmware_animations.index import PackageIndex
from firmware_animations.manifest import canonical_json, validate_parameter_schema
from firmware_animations.signing import generate_keypair, public_key_id, sign


def fake_xtensa_shared_object(payload: bytes = b"native-code") -> bytes:
    header = bytearray(52)
    header[:7] = b"\x7fELF\x01\x01\x01"
    header[16:18] = (3).to_bytes(2, "little")  # ET_DYN
    header[18:20] = (94).to_bytes(2, "little")  # EM_XTENSA
    return bytes(header) + payload


class FirmwarePackageTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.private = self.root / "private.pem"
        self.public = self.root / "public.pem"
        self.key_id = generate_keypair(self.private, self.public)
        self.trusted = {self.key_id: self.public}
        self.sources = self.root / "sources"
        save_sources(self.sources)
        self.frame_metadata = {
            "id": "representative-frames",
            "name": "Representative Frames",
            "version": "1.2.3",
            "description": "Small deterministic receiver-track fixture.",
            "preferred_fps": 30,
            "parameter_schema": {},
            "provenance": {
                "author": "ledgrid-poc tests",
                "license": "MIT",
                "source": "deterministic generated pixels",
                "generated_by": "firmware_animations test generator",
            },
        }
        self.native_metadata = {
            "id": "startup-rainbow-native",
            "name": "Startup Rainbow Native",
            "version": "1.0.0",
            "description": "Original receiver-local diagonal rainbow.",
            "preferred_fps": 60,
            "parameter_schema": {
                "speed": {"type": "float", "min": 0.1, "max": 4.0, "default": 1.0, "description": "Playback speed"},
                "direction": {"type": "enum", "options": ["up-right", "down-left"], "default": "up-right", "description": "Direction"},
                "palette": {"type": "color", "default": "#FF00AA", "description": "Accent"},
            },
            "provenance": {
                "author": "ledgrid-poc tests",
                "license": "MIT",
                "source": "original native fixture",
                "generated_by": "firmware_animations test builder",
            },
        }
        self.preview = (self.sources / "representative-native-preview.webp").read_bytes()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def frame_package(self, source_name: str = "representative.gif") -> bytes:
        return build_frame_package(self.sources / source_name, self.frame_metadata, self.private, keyframe_interval=2)

    def native_package(self) -> bytes:
        return build_native_package(
            fake_xtensa_shared_object(), self.preview, self.native_metadata,
            self.private, imports=[],
        )

    def rewrite_signed(self, package: bytes, mutate) -> bytes:
        _, members = read_safe_zip(package)
        mutate(members)
        manifest = json.loads(members[MANIFEST_PATH])
        manifest["payload_hashes"] = {
            path: hashlib.sha256(members[path]).hexdigest()
            for path in manifest["payload_hashes"]
        }
        members[MANIFEST_PATH] = canonical_json(manifest)
        old_index = PackageIndex.decode(members[INDEX_PATH])
        if manifest["kind"] == "native":
            payload_digest = hashlib.sha256(members["payload/native/module.so"]).digest()
            device_hashes = (payload_digest,) * 4
        else:
            device_hashes = tuple(hashlib.sha256(members[track_path(i)]).digest() for i in range(4))
        new_index = PackageIndex(manifest["kind"], hashlib.sha256(members[MANIFEST_PATH]).digest(), device_hashes).encode()
        members[INDEX_PATH] = new_index
        members[SIGNATURE_PATH] = sign(new_index, self.private)
        return deterministic_zip(members)


class FirmwarePackageBuildTests(FirmwarePackageTestCase):
    def test_frame_and_native_rebuilds_are_byte_identical_and_round_trip(self) -> None:
        first = self.frame_package()
        self.assertEqual(first, self.frame_package())
        frames = inspect_package(first, self.trusted)
        self.assertEqual(frames.manifest["kind"], "frames")
        self.assertEqual([len(frames.payload_for_device(i)) for i in range(4)], [len(frames.members[track_path(i)]) for i in range(4)])

        native = self.native_package()
        self.assertEqual(native, self.native_package())
        verified_native = inspect_package(native, self.trusted)
        self.assertEqual(verified_native.manifest["kind"], "native")
        self.assertEqual(len({verified_native.payload_for_device(i) for i in range(4)}), 1)

    def test_gif_and_webp_tracks_preserve_timing_looping_slices_and_rgb565_pixels(self) -> None:
        for source_name in ("representative.gif", "representative.webp"):
            with self.subTest(source=source_name):
                source = load_image_frames(self.sources / source_name)
                _, encoded = encode_image_tracks(self.sources / source_name, keyframe_interval=2)
                decoded = tuple(decode_track(track, expected_device_index=i) for i, track in enumerate(encoded))
                self.assertEqual(decoded[0].durations_ms, source.durations_ms)
                self.assertEqual(decoded[0].loop_count, 0)
                rebuilt = assemble_wall_frames(decoded)
                self.assertEqual(len(rebuilt), len(source.frames_rgb))
                for expected, actual in zip(source.frames_rgb, rebuilt):
                    for offset in range(0, len(expected), 3):
                        self.assertLessEqual(abs(expected[offset] - actual[offset]), 7)
                        self.assertLessEqual(abs(expected[offset + 1] - actual[offset + 1]), 3)
                        self.assertLessEqual(abs(expected[offset + 2] - actual[offset + 2]), 7)
                for device in range(4):
                    start = device * 8 * 138 * 3
                    end = start + 8 * 138 * 3
                    self.assertEqual(rgb565_to_rgb888(decoded[device].frames[0]), rebuilt[0][start:end])

    def test_tracks_use_receiver_lgt1_header_and_full_coverage_opcodes(self) -> None:
        _, tracks = encode_image_tracks(self.sources / "representative.gif", keyframe_interval=2)
        for device, track in enumerate(tracks):
            self.assertEqual(track[:5], b"LGT1\x01")
            self.assertEqual(track[6], 8)
            self.assertEqual(track[7], device)
            self.assertEqual(int.from_bytes(track[8:10], "big"), 138)
            self.assertEqual(int.from_bytes(track[10:12], "big"), 3)
            self.assertEqual(int.from_bytes(track[12:16], "big"), len(track) - 20)
            self.assertEqual(track[5], 1)
            self.assertEqual(track[16:20], b"\0\0\0\0")
            first_payload_size = int.from_bytes(track[24:28], "big")
            self.assertEqual(track[28], 1)
            cursor = 32
            pixels = 0
            while cursor < 32 + first_payload_size:
                opcode = track[cursor]
                count = int.from_bytes(track[cursor + 1:cursor + 3], "big")
                self.assertIn(opcode, {0, 2})  # keyframes cannot skip
                pixels += count
                cursor += 3 + (count * 2 if opcode == 0 else 2)
            self.assertEqual(pixels, 8 * 138)
            self.assertEqual(cursor, 32 + first_payload_size)

        noncanonical = bytearray(tracks[0])
        noncanonical[5] = 0
        noncanonical[16:20] = (1).to_bytes(4, "big")
        with self.assertRaisesRegex(PackageValidationError, "infinite-loop"):
            decode_track(bytes(noncanonical), expected_device_index=0)

    def test_receiver_verification_envelope_is_exact_313_byte_big_endian_contract(self) -> None:
        package = inspect_package(self.frame_package(), self.trusted)
        envelope = package.verification_envelope(2)
        command = envelope.asset_begin_command()
        self.assertEqual(len(command), 313)
        self.assertEqual(command[:2], b"\x22\x01")
        self.assertEqual(int.from_bytes(command[2:4], "big"), 309)
        self.assertEqual(int.from_bytes(command[4:8], "big"), len(package.payload_for_device(2)))
        self.assertEqual(command[8:40], hashlib.sha256(package.payload_for_device(2)).digest())
        self.assertEqual(command[40], 2)
        self.assertEqual(int.from_bytes(command[41:43], "big"), 1)
        self.assertEqual(int.from_bytes(command[43:45], "big"), 1)
        self.assertEqual(command[45], 8)
        self.assertEqual(int.from_bytes(command[46:48], "big"), 138)
        self.assertEqual(command[48], 2)
        self.assertEqual(command[49], 20)
        self.assertEqual(command[50:70].decode("ascii"), self.key_id)
        self.assertEqual(int.from_bytes(command[70:72], "big"), 176)
        self.assertEqual(command[72:248], package.members[INDEX_PATH])
        self.assertEqual(command[248], 64)
        self.assertEqual(command[249:313], package.members[SIGNATURE_PATH])

    def test_parameter_types_defaults_bounds_and_unknowns_are_strict(self) -> None:
        package = inspect_package(self.native_package(), self.trusted)
        self.assertEqual(
            validate_parameters(package.manifest, {"direction": "down-left", "palette": "#00aaFF"}),
            {"speed": 1.0, "direction": "down-left", "palette": "#00AAFF"},
        )
        for values in ({"speed": True}, {"speed": 5.0}, {"direction": "sideways"}, {"extra": 1}):
            with self.subTest(values=values), self.assertRaises(PackageValidationError):
                validate_parameters(package.manifest, values)

    def test_manifest_parameters_fit_the_receiver_wire_abi(self) -> None:
        with self.assertRaisesRegex(PackageValidationError, "more than 31"):
            validate_parameter_schema({
                f"value_{index}": {"type": "bool", "default": False}
                for index in range(32)
            })
        invalid_specs = (
            {"type": "int", "min": -(2**31) - 1, "max": 1, "default": 0},
            {"type": "int", "min": 0, "max": 2**31, "default": 0},
            {"type": "float", "min": 0.1, "max": 1e100, "default": 1.0},
            {"type": "enum", "options": ["contains space"], "default": "contains space"},
            {"type": "enum", "options": ["é" * 32], "default": "é" * 32},
        )
        for spec in invalid_specs:
            with self.subTest(spec=spec), self.assertRaises(PackageValidationError):
                validate_parameter_schema({"value": spec})

    def test_frame_packages_expose_only_controls_the_receiver_implements(self) -> None:
        metadata = dict(self.frame_metadata)
        metadata["parameter_schema"] = {
            "ignored": {"type": "bool", "default": False}
        }
        with self.assertRaisesRegex(PackageValidationError, "cannot declare custom"):
            build_frame_package(
                self.sources / "representative.gif", metadata, self.private
            )

        def add_ignored_control(members):
            manifest = json.loads(members[MANIFEST_PATH])
            manifest["parameter_schema"]["ignored"] = {
                "type": "bool", "default": False,
            }
            members[MANIFEST_PATH] = canonical_json(manifest)

        with self.assertRaisesRegex(PackageValidationError, "receiver frame controls"):
            inspect_package(
                self.rewrite_signed(self.frame_package(), add_ignored_control),
                self.trusted,
            )

    def test_checked_in_example_metadata_matches_generated_sources(self) -> None:
        expected_path = Path(__file__).parents[2] / "firmware_animations/examples/expected_sources.json"
        expected = json.loads(expected_path.read_text())
        actual = {}
        for name, filename in (("gif", "representative.gif"), ("webp", "representative.webp"), ("native_package_preview", "representative-native-preview.webp")):
            frames = load_image_frames(self.sources / filename)
            actual[name] = {"durations_ms": list(frames.durations_ms), "frames": len(frames.frames_rgb), "loop_count": frames.loop_count, "size": [32, 138]}
        self.assertEqual(actual, expected)


class FirmwarePackageSecurityTests(FirmwarePackageTestCase):
    def test_key_generation_is_private_from_creation_and_never_overwrites(self) -> None:
        private = self.root / "generated-private.pem"
        public = self.root / "generated-public.pem"
        generated_key_id = generate_keypair(private, public)
        self.assertEqual(private.stat().st_mode & 0o777, 0o600)
        self.assertEqual(generated_key_id, public_key_id(public))

        private_before = private.read_bytes()
        public_before = public.read_bytes()
        with self.assertRaisesRegex(PackageValidationError, "cannot create signing keypair"):
            generate_keypair(private, public)
        self.assertEqual(private.read_bytes(), private_before)
        self.assertEqual(public.read_bytes(), public_before)

    def test_altered_signature_index_payload_and_unknown_key_fail(self) -> None:
        package = self.frame_package()
        _, members = read_safe_zip(package)
        mutations = []
        for path in (SIGNATURE_PATH, INDEX_PATH, track_path(0)):
            changed = dict(members)
            changed[path] = changed[path][:-1] + bytes([changed[path][-1] ^ 1])
            mutations.append(deterministic_zip(changed))
        for changed in mutations:
            with self.assertRaises(PackageValidationError):
                inspect_package(changed, self.trusted)
        with self.assertRaisesRegex(PackageValidationError, "unknown signing key"):
            inspect_package(package, {})

    def test_high_s_signature_is_rejected_before_receiver_install(self) -> None:
        import ecdsa

        package = self.frame_package()
        _, members = read_safe_zip(package)
        scalar_r, scalar_s = ecdsa.util.sigdecode_string(
            members[SIGNATURE_PATH], ecdsa.NIST256p.order
        )
        members[SIGNATURE_PATH] = ecdsa.util.sigencode_string(
            scalar_r, ecdsa.NIST256p.order - scalar_s, ecdsa.NIST256p.order
        )
        with self.assertRaisesRegex(PackageValidationError, "low-S"):
            inspect_package(deterministic_zip(members), self.trusted)

    def test_wrong_abi_target_geometry_import_and_bad_default_fail(self) -> None:
        cases = [
            lambda m: json.loads(m[MANIFEST_PATH]),
        ]
        def mutate_field(field, value):
            def apply(members):
                manifest = json.loads(members[MANIFEST_PATH])
                manifest[field] = value
                members[MANIFEST_PATH] = canonical_json(manifest)
            return apply
        mutations = [
            mutate_field("abi", "lga-animation-v2"),
            mutate_field("target", "wrong-target"),
            mutate_field("geometry", {"strips": 31, "leds_per_strip": 138, "receiver_count": 4, "strips_per_receiver": 8}),
            mutate_field("imports", ["driver_write_register"]),
        ]
        def bad_default(members):
            manifest = json.loads(members[MANIFEST_PATH])
            manifest["parameter_schema"]["speed"]["default"] = 99.0
            members[MANIFEST_PATH] = canonical_json(manifest)
        mutations.append(bad_default)
        for mutate in mutations:
            with self.subTest(mutate=mutate), self.assertRaises(PackageValidationError):
                inspect_package(self.rewrite_signed(self.native_package(), mutate), self.trusted)

    def test_traversal_duplicate_symlink_bomb_and_oversize_archives_fail(self) -> None:
        def raw_zip(entries):
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for info, data in entries:
                    archive.writestr(info, data)
            return output.getvalue()
        traversal = raw_zip([(zipfile.ZipInfo("../manifest.json"), b"{}")])
        # Capture the warning raised while deliberately creating an invalid
        # duplicate-member fixture; package inspection must still reject it.
        with self.assertWarnsRegex(UserWarning, "Duplicate name"):
            duplicate = raw_zip([(zipfile.ZipInfo("manifest.json"), b"{}"), (zipfile.ZipInfo("manifest.json"), b"{}")])
        symlink_info = zipfile.ZipInfo("manifest.json")
        symlink_info.create_system = 3
        symlink_info.external_attr = (stat.S_IFLNK | 0o777) << 16
        symlink = raw_zip([(symlink_info, b"target")])
        bomb = raw_zip([(zipfile.ZipInfo("manifest.json"), b"0" * (2 * 1024 * 1024))])
        for payload in (traversal, duplicate, symlink, bomb, b"x" * (16 * 1024 * 1024 + 1)):
            with self.subTest(size=len(payload)), self.assertRaises(PackageValidationError):
                inspect_package(payload, self.trusted)

    def test_malformed_truncated_and_out_of_bounds_tracks_fail_before_install(self) -> None:
        package = self.frame_package()
        def truncated(members):
            members[track_path(0)] = members[track_path(0)][:-1]
        def out_of_bounds(members):
            track = bytearray(members[track_path(0)])
            header_size = struct.calcsize(">4sBBBBHHII")
            frame_header_size = struct.calcsize(">IIB3x")
            count_offset = header_size + frame_header_size + 1
            struct.pack_into(">H", track, count_offset, 1105)
            members[track_path(0)] = bytes(track)
        for mutate in (truncated, out_of_bounds):
            forged = self.rewrite_signed(package, mutate)
            with self.assertRaises(PackageValidationError):
                inspect_package(forged, self.trusted)
            library = FirmwareAnimationLibrary(self.root / f"library-{mutate.__name__}", self.trusted)
            with self.assertRaises(PackageValidationError):
                library.install(forged)
            self.assertEqual(library.list(), [])

    def test_native_and_track_size_limits_are_enforced(self) -> None:
        self.assertEqual(MAX_TRACK_BYTES, 2_621_440)
        with self.assertRaisesRegex(PackageValidationError, "512 KiB"):
            build_native_package(fake_xtensa_shared_object(b"x" * (512 * 1024)), self.preview, self.native_metadata, self.private, imports=[])
        _, tracks = encode_image_tracks(self.sources / "representative.gif")
        with self.assertRaisesRegex(PackageValidationError, "2.5 MiB"):
            decode_track(tracks[0] + b"x" * MAX_TRACK_BYTES)


class FirmwarePackageLibraryTests(FirmwarePackageTestCase):
    def replacement_package(self) -> bytes:
        metadata = dict(self.frame_metadata)
        metadata["version"] = "1.2.4"
        metadata["description"] = "Replacement content for the same stable package ID."
        return build_frame_package(
            self.sources / "representative.webp", metadata, self.private,
            keyframe_interval=1,
        )

    def test_install_list_get_payload_delete_and_active_rejection(self) -> None:
        active = [None]
        library = FirmwareAnimationLibrary(self.root / "library", self.trusted, active_id_provider=lambda: active[0])
        installed = library.install(self.frame_package())
        self.assertEqual(library.get(installed.package_id), installed)
        self.assertEqual([item.package_id for item in library.list()], [installed.package_id])
        self.assertEqual(library.read_payload(installed.package_id, 2), inspect_package(self.frame_package(), self.trusted).payload_for_device(2))
        active[0] = installed.package_id
        with self.assertRaises(ActivePackageError):
            library.delete(installed.package_id)
        active[0] = None
        library.delete(installed.package_id)
        self.assertIsNone(library.get(installed.package_id))
        self.assertEqual(library.list(), [])

    def test_interrupted_install_is_invisible_recovery_cleans_and_retry_succeeds(self) -> None:
        calls = []
        def fail(stage):
            calls.append(stage)
            if stage == "before_publish":
                raise RuntimeError("simulated power loss")
        root = self.root / "library"
        library = FirmwareAnimationLibrary(root, self.trusted, fault_injector=fail)
        with self.assertRaisesRegex(RuntimeError, "power loss"):
            library.install(self.frame_package())
        self.assertEqual(library.list(), [])
        recovered = FirmwareAnimationLibrary(root, self.trusted)
        self.assertEqual(list((root / ".staging").glob("*.part")), [])
        self.assertEqual(list((root / "objects").glob("*.lga")), [])
        installed = recovered.install(self.frame_package())
        self.assertEqual(recovered.get(installed.package_id), installed)

    def test_recovery_waits_for_an_in_progress_publish_before_collecting_objects(self) -> None:
        root = self.root / "library"
        recovery_started = threading.Event()
        recovery_finished = threading.Event()
        recovery_thread = None

        def recover_concurrently(stage):
            nonlocal recovery_thread
            if stage != "after_object":
                return
            def recover():
                recovery_started.set()
                FirmwareAnimationLibrary(root, self.trusted)
                recovery_finished.set()
            recovery_thread = threading.Thread(target=recover)
            recovery_thread.start()
            self.assertTrue(recovery_started.wait(1))
            time.sleep(0.05)
            self.assertFalse(recovery_finished.is_set())

        publisher = FirmwareAnimationLibrary(
            root, self.trusted, fault_injector=recover_concurrently
        )
        installed = publisher.install(self.frame_package())
        assert recovery_thread is not None
        recovery_thread.join(2)
        self.assertFalse(recovery_thread.is_alive())
        self.assertTrue(installed.package_path.exists())
        self.assertEqual(
            FirmwareAnimationLibrary(root, self.trusted).get(installed.package_id),
            installed,
        )

    def test_same_id_replacement_publishes_new_metadata_then_reclaims_old_object(self) -> None:
        root = self.root / "library"
        library = FirmwareAnimationLibrary(root, self.trusted)
        original = library.install(self.frame_package())
        replacement = library.install(self.replacement_package())
        self.assertEqual(original.package_id, replacement.package_id)
        self.assertNotEqual(original.digest, replacement.digest)
        self.assertEqual(library.get(original.package_id), replacement)
        self.assertFalse(original.package_path.exists())
        self.assertEqual(list((root / "objects").glob("*.lga")), [replacement.package_path])

    def test_same_id_replacement_is_rejected_while_original_is_active(self) -> None:
        root = self.root / "library"
        active = [None]
        library = FirmwareAnimationLibrary(
            root, self.trusted, active_id_provider=lambda: active[0]
        )
        original = library.install(self.frame_package())
        active[0] = original.digest
        with self.assertRaisesRegex(ActivePackageError, "cannot replace active package"):
            library.install(self.replacement_package())
        self.assertEqual(library.get(original.package_id), original)
        self.assertEqual(list((root / "objects").glob("*.lga")), [original.package_path])

    def test_shared_object_reference_survives_replacement_until_last_reference_is_deleted(self) -> None:
        root = self.root / "library"
        library = FirmwareAnimationLibrary(root, self.trusted)
        original = library.install(self.frame_package())
        metadata = json.loads((root / "packages" / f"{original.package_id}.json").read_text())
        metadata["manifest"] = dict(metadata["manifest"])
        metadata["manifest"]["id"] = "shared-reference"
        metadata["manifest"]["name"] = "Shared Reference"
        (root / "packages/shared-reference.json").write_bytes(canonical_json(metadata))

        replacement = library.install(self.replacement_package())
        self.assertTrue(original.package_path.exists())
        self.assertTrue(replacement.package_path.exists())
        library.delete("shared-reference")
        self.assertFalse(original.package_path.exists())
        self.assertTrue(replacement.package_path.exists())

    def test_failed_replacement_retains_old_visibility_and_retry_leaves_no_orphans(self) -> None:
        root = self.root / "library"
        original_library = FirmwareAnimationLibrary(root, self.trusted)
        original = original_library.install(self.frame_package())

        def fail_before_publish(stage):
            if stage == "before_publish":
                raise RuntimeError("simulated replacement interruption")

        interrupted = FirmwareAnimationLibrary(root, self.trusted, fault_injector=fail_before_publish)
        with self.assertRaisesRegex(RuntimeError, "replacement interruption"):
            interrupted.install(self.replacement_package())
        self.assertEqual(interrupted.get(original.package_id), original)

        recovered = FirmwareAnimationLibrary(root, self.trusted)
        self.assertEqual(recovered.get(original.package_id), original)
        self.assertEqual(list((root / "objects").glob("*.lga")), [original.package_path])
        replacement = recovered.install(self.replacement_package())
        self.assertEqual(recovered.get(original.package_id), replacement)
        self.assertEqual(list((root / "objects").glob("*.lga")), [replacement.package_path])

    def test_corrupt_metadata_is_skipped_retained_and_blocks_unsafe_gc(self) -> None:
        root = self.root / "library"
        library = FirmwareAnimationLibrary(root, self.trusted)
        installed = library.install(self.frame_package())
        corrupt_path = root / "packages/broken.json"
        corrupt_path.write_bytes(b'{"digest":"truncated"')
        diagnostic_object = root / "objects" / ("a" * 64 + ".lga")
        diagnostic_object.write_bytes(b"diagnostic orphan candidate")

        library.recover()
        self.assertEqual([item.package_id for item in library.list()], [installed.package_id])
        self.assertIsNone(library.get("broken"))
        self.assertEqual(corrupt_path.read_bytes(), b'{"digest":"truncated"')
        self.assertTrue(diagnostic_object.exists())
        self.assertEqual(library.get(installed.package_id), installed)

    def test_unsafe_ids_never_escape_library(self) -> None:
        library = FirmwareAnimationLibrary(self.root / "library", self.trusted)
        for package_id in ("../escape", "/absolute", "UpperCase", "a.b"):
            with self.subTest(package_id=package_id), self.assertRaises(PackageValidationError):
                library.get(package_id)


if __name__ == "__main__":
    unittest.main()
