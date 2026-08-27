from __future__ import annotations

from copy import deepcopy
import io
import struct
import unittest
import zipfile

from animation.native.archive import deterministic_zip
from animation.native.bundle import inspect_bundle
from animation.native.constants import (
    ABI_HEADER_PATH,
    MANIFEST_PATH,
    MAX_BUNDLE_BYTES,
    MAX_PAYLOAD_BYTES,
    MAX_PREVIEW_BYTES,
    PAYLOAD_PATH,
    PREVIEW_PATH,
)
from animation.native.elf import validate_target_elf
from animation.native.errors import NativeBundleError, NativeElfError, NativeManifestError
from animation.native.schema import canonical_json, validate_bundle_manifest

from .native_background_test_support import deterministic_pair, toolchain_available


@unittest.skipUnless(toolchain_available(), "pinned PlatformIO Xtensa toolchain unavailable")
class NativeBackgroundBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = deterministic_pair()[0]
        assert cls.result.bundle_path is not None
        cls.verified = inspect_bundle(cls.result.bundle_path)

    def test_target_elf_has_exact_surface(self) -> None:
        inspection = validate_target_elf(self.verified.payload)
        self.assertEqual(inspection.exports, ("ledgrid_native_background_v2",))
        self.assertEqual(inspection.imports, ())
        self.assertFalse(set(inspection.sections) & {".init", ".fini", ".init_array", ".fini_array"})

    def test_target_elf_rejects_wrong_endianness(self) -> None:
        payload = bytearray(self.verified.payload)
        payload[5] = 2
        with self.assertRaisesRegex(NativeElfError, "little-endian"):
            validate_target_elf(bytes(payload))

    def test_target_elf_rejects_header_identity_drift(self) -> None:
        mutations = []
        for offset, replacement in ((4, 2), (7, 3)):
            payload = bytearray(self.verified.payload)
            payload[offset] = replacement
            mutations.append(payload)
        for offset, value, kind in (
            (16, 2, "H"),  # ET_EXEC, not ET_DYN.
            (18, 0, "H"),  # No Xtensa machine.
            (20, 0, "I"),  # Invalid ELF version.
            (36, 0, "I"),  # Missing frozen ESP32-S3 flags.
        ):
            payload = bytearray(self.verified.payload)
            struct.pack_into("<" + kind, payload, offset, value)
            mutations.append(payload)
        for index, payload in enumerate(mutations):
            with self.subTest(index=index), self.assertRaises(NativeElfError):
                validate_target_elf(bytes(payload))

    def test_inspection_rejects_noncanonical_and_extra_archives(self) -> None:
        members = dict(self.verified.members)
        members["extra"] = b"unexpected"
        with self.assertRaisesRegex(NativeBundleError, "invalid member count|members must be exactly"):
            inspect_bundle(deterministic_zip(members))
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in self.verified.members.items():
                archive.writestr(name, data)
        with self.assertRaises(NativeBundleError):
            inspect_bundle(stream.getvalue())

    def test_inspection_rejects_traversal_member_before_extraction(self) -> None:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("../manifest.json", b"{}")
        with self.assertRaisesRegex(NativeBundleError, "unsafe"):
            inspect_bundle(stream.getvalue())

    def test_inspection_rejects_oversized_archive_and_members(self) -> None:
        with self.assertRaises(NativeBundleError):
            inspect_bundle(b"x" * (MAX_BUNDLE_BYTES + 1))
        members = dict(self.verified.members)
        members[PAYLOAD_PATH] = b"x" * (MAX_PAYLOAD_BYTES + 1)
        with self.assertRaisesRegex(NativeBundleError, "payload"):
            inspect_bundle(deterministic_zip(members))
        members = dict(self.verified.members)
        members[PREVIEW_PATH] = b"x" * (MAX_PREVIEW_BYTES + 1)
        with self.assertRaisesRegex(NativeBundleError, "preview"):
            inspect_bundle(deterministic_zip(members))

    def test_generated_manifest_rejects_identity_and_semantic_drift(self) -> None:
        manifest = self.verified.manifest
        mutations = []

        value = deepcopy(manifest)
        value["build"]["source_sha256"] = "0" * 64
        mutations.append(value)
        value = deepcopy(manifest)
        value["abi"]["version"] = 3
        mutations.append(value)
        value = deepcopy(manifest)
        value["schema_version"] = True
        mutations.append(value)
        value = deepcopy(manifest)
        value["abi"]["version"] = 2.0
        mutations.append(value)
        value = deepcopy(manifest)
        value["abi"]["version"] = True
        mutations.append(value)
        value = deepcopy(manifest)
        value["target"]["name"] = "esp32"
        mutations.append(value)
        value = deepcopy(manifest)
        value["target"]["elf_class"] = 32.0
        mutations.append(value)
        value = deepcopy(manifest)
        value["target"]["elf_class"] = True
        mutations.append(value)
        value = deepcopy(manifest)
        value["geometry"]["receiver_views"][2]["reverse_local_strip_order"] = False
        mutations.append(value)
        value = deepcopy(manifest)
        value["geometry"]["global_strips"] = 32.0
        mutations.append(value)
        value = deepcopy(manifest)
        value["geometry"]["receiver_offsets"][0] = False
        mutations.append(value)
        value = deepcopy(manifest)
        value["geometry"]["receiver_views"][0]["logical_receiver_id"] = False
        mutations.append(value)
        value = deepcopy(manifest)
        value["geometry"]["receiver_views"][0]["reverse_local_strip_order"] = 0
        mutations.append(value)
        value = deepcopy(manifest)
        value["abi"]["header_sha256"] = "0" * 64
        mutations.append(value)
        value = deepcopy(manifest)
        value["build"]["source_inputs"][0]["path"] = "../background.cpp"
        mutations.append(value)
        value = deepcopy(manifest)
        value["build"]["toolchains"]["target"]["compiler"] = "untrusted-g++"
        mutations.append(value)
        value = deepcopy(manifest)
        value["build"]["target_flags"].append("-DUNTRACKED=1")
        mutations.append(value)
        value = deepcopy(manifest)
        value["parameter_schema"]["vibe"] = value["parameter_schema"].pop("shimmer")
        value["defaults"]["vibe"] = value["defaults"].pop("shimmer")
        mutations.append(value)
        value = deepcopy(manifest)
        value["installation_profile_requirements"] = ["mask", "mask"]
        mutations.append(value)
        value = deepcopy(manifest)
        value["vibe"]["capabilities"].remove("tempo")
        mutations.append(value)
        value = deepcopy(manifest)
        value["preview"]["capture_seconds"][1] = 0.0
        mutations.append(value)
        value = deepcopy(manifest)
        value["preview"]["duration_ms"] = 99
        mutations.append(value)
        value = deepcopy(manifest)
        value["preview"]["capture_seconds"][-1] = float(2**64)
        mutations.append(value)
        value = deepcopy(manifest)
        value["preview"]["capture_seconds"] = [0.0]
        value["preview"]["frame_count"] = 1
        mutations.append(value)
        value = deepcopy(manifest)
        value["preview"]["capture_seconds"] = [index / 60 for index in range(17)]
        value["preview"]["frame_count"] = 17
        mutations.append(value)
        value = deepcopy(manifest)
        value["preview"]["capture_seconds"] = [0.0, 0.0000001]
        value["preview"]["frame_count"] = 2
        mutations.append(value)
        value = deepcopy(manifest)
        value["preview"]["capture_seconds"][-1] = 1e308
        mutations.append(value)
        value = deepcopy(manifest)
        value["defaults"]["brightness"] = 2.0
        mutations.append(value)
        value = deepcopy(manifest)
        value["parameter_schema"]["layers"]["default"] = 1
        value["defaults"]["layers"] = True
        mutations.append(value)
        value = deepcopy(manifest)
        value["parameter_schema"]["brightness"]["default"] = 2.0
        mutations.append(value)
        value = deepcopy(manifest)
        value["parameter_schema"]["brightness"]["description"] = "   "
        mutations.append(value)
        value = deepcopy(manifest)
        value["preview"]["width"] = 32.0
        mutations.append(value)
        value = deepcopy(manifest)
        value["preview"]["width"] = True
        mutations.append(value)
        value = deepcopy(manifest)
        value["parameter_schema"]["brightness"]["max"] = 1e100
        mutations.append(value)
        value = deepcopy(manifest)
        value["parameter_schema"]["brightness"]["min"] = 1e-100
        mutations.append(value)
        value = deepcopy(manifest)
        current_platform = value["build"]["toolchains"]["host"]["platform"]
        value["build"]["toolchains"]["host"]["platform"] = (
            "linux" if current_platform == "darwin" else "darwin"
        )
        mutations.append(value)

        for index, mutation in enumerate(mutations):
            with self.subTest(index=index), self.assertRaises(NativeManifestError):
                validate_bundle_manifest(mutation)

    def test_malformed_json_types_raise_native_manifest_error(self) -> None:
        manifest = self.verified.manifest
        mutations = []

        value = deepcopy(manifest)
        value["preview"]["capture_seconds"] = 7
        mutations.append(value)
        value = deepcopy(manifest)
        value["preview"]["capture_seconds"] = [0.0, "later"]
        mutations.append(value)
        value = deepcopy(manifest)
        value["preview"]["capture_seconds"] = [0.0, 10**10_000]
        mutations.append(value)
        value = deepcopy(manifest)
        value["vibe"]["capabilities"] = "tempo"
        mutations.append(value)
        value = deepcopy(manifest)
        value["vibe"]["capabilities"] = ["palette_roles", 7]
        mutations.append(value)
        value = deepcopy(manifest)
        value["vibe"]["semantic_roles"] = ["accent", {"bad": "role"}]
        mutations.append(value)
        value = deepcopy(manifest)
        value["installation_profile_requirements"] = "mask"
        mutations.append(value)
        value = deepcopy(manifest)
        value["installation_profile_requirements"] = ["mask", 7]
        mutations.append(value)
        value = deepcopy(manifest)
        value["parameter_schema"]["layers"]["type"] = []
        mutations.append(value)
        value = deepcopy(manifest)
        value["parameter_schema"]["layers"] = {
            "type": "str",
            "default": "one",
            "description": "Layer count",
            "options": ["one", {"bad": "option"}],
        }
        value["defaults"]["layers"] = "one"
        mutations.append(value)
        value = deepcopy(manifest)
        value["build"]["toolchains"]["host"]["platform"] = []
        mutations.append(value)
        value = deepcopy(manifest)
        value["vibe"]["color_policy"] = {}
        mutations.append(value)
        value = deepcopy(manifest)
        value["vibe"]["timing_adapter"] = []
        mutations.append(value)

        for index, mutation in enumerate(mutations):
            with self.subTest(index=index), self.assertRaises(NativeManifestError):
                validate_bundle_manifest(mutation)

    def test_hash_mutation_cannot_be_hidden_by_canonical_reencoding(self) -> None:
        manifest = deepcopy(self.verified.manifest)
        manifest["payload"]["sha256"] = "f" * 64
        members = {
            MANIFEST_PATH: canonical_json(manifest),
            PAYLOAD_PATH: self.verified.payload,
            PREVIEW_PATH: self.verified.preview,
        }
        with self.assertRaisesRegex(NativeBundleError, "payload hash"):
            inspect_bundle(deterministic_zip(members))

    def test_duplicate_manifest_fields_are_rejected(self) -> None:
        members = dict(self.verified.members)
        members[MANIFEST_PATH] = b'{"schema":1,"schema":2}'
        with self.assertRaisesRegex(NativeBundleError, "duplicate field"):
            inspect_bundle(deterministic_zip(members))

    def test_source_input_contract_is_exact_and_cross_linked(self) -> None:
        build = self.verified.manifest["build"]
        by_path = {item["path"]: item["sha256"] for item in build["source_inputs"]}
        self.assertEqual(build["source_sha256"], by_path[
            f"animation/plugins/{self.verified.manifest['plugin_id']}/native/background.cpp"
        ])
        self.assertEqual(
            self.verified.manifest["abi"]["header_sha256"], by_path[ABI_HEADER_PATH]
        )
        self.assertEqual(
            self.verified.manifest["cadence"]["abi_next_deadline_semantics"],
            "absolute_unscaled_microseconds_since_scene_epoch",
        )
        views = self.verified.manifest["geometry"]["receiver_views"]
        self.assertEqual(
            [view["logical_receiver_id"] for view in views], [0, 1, 2, 3, 4]
        )
        self.assertEqual(
            [view["local_strips"] for view in views], [8, 8, 8, 8, 1]
        )
        self.assertEqual(
            [view["reverse_local_strip_order"] for view in views],
            [False, False, True, True, False],
        )


if __name__ == "__main__":
    unittest.main()
