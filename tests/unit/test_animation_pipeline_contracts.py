from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path
from types import MappingProxyType

import numpy as np

from animation.core.compositing import (
    alpha_coverage_ranges,
    board_flat_slices,
    canvas_to_logical_flat_index,
    coverage_dirty_union,
    fold_overlays,
    logical_flat_index,
    normalize_dirty_ranges,
    receiver_local_index,
    round_u8_product,
    scale_premultiplied_rgba,
    source_over_rgb,
    source_over_rgba,
    union_dirty_ranges,
)
from animation.core.presentation_contracts import (
    ANIMATION_RUNTIME_CONTEXT_SCHEMA,
    COMPONENT_DESCRIPTOR_SCHEMA,
    DESIRED_DISPLAY_STATE_SCHEMA,
    FRAME_CONTRACT_SCHEMA,
    NEXT_DEADLINE_SEMANTICS,
    SCENE_STATE_SCHEMA,
    VIBE_PROFILE_SCHEMA,
    VIBE_STATE_SCHEMA,
    AnimationRuntimeContext,
    BaseFrame,
    CadenceContract,
    CadenceMode,
    ClipPolicy,
    ComponentDescriptor,
    ComponentProvider,
    ComponentRef,
    ComponentRole,
    DesiredDisplayState,
    ForegroundStalePolicy,
    OutputState,
    OverlayFrame,
    OverlayPlacement,
    OverlayRef,
    SceneState,
    StalePolicy,
    TimingAdapter,
    VibeProfile,
    VibeState,
)
from tools.generate_animation_pipeline_golden import build_fixture, render_fixture


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "animation_pipeline_v1.json"
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


class FixedPointGoldenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_fixture_identity_and_generator_equality(self):
        self.assertEqual(self.fixture["$schema"], "ledgrid.animation-pipeline-golden")
        self.assertEqual(self.fixture["version"], 1)
        self.assertEqual(FIXTURE_PATH.read_text(encoding="utf-8"), render_fixture())
        self.assertEqual(self.fixture, build_fixture())
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools/generate_animation_pipeline_golden.py"), "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_blend_vectors_are_exact(self):
        for vector in self.fixture["blend_vectors"]:
            with self.subTest(vector=vector["id"]):
                self.assertEqual(
                    list(source_over_rgb(vector["base_rgb"], vector["overlay_rgba"])),
                    vector["expected_rgb"],
                )
        expected = {item["id"]: item["expected_rgb"] for item in self.fixture["blend_vectors"]}
        self.assertEqual(expected["transparent_black"], [12, 34, 56])
        self.assertEqual(expected["opaque_black"], [0, 0, 0])
        self.assertEqual(expected["half_up_rounding"], [1, 1, 2])
        self.assertEqual(expected["channel_saturation"], [255, 254, 253])

    def test_opacity_and_ordered_fold_vectors_are_exact(self):
        for vector in self.fixture["opacity_vectors"]:
            with self.subTest(vector=vector["id"]):
                self.assertEqual(
                    list(scale_premultiplied_rgba(vector["input_rgba"], vector["opacity"])),
                    vector["expected_rgba"],
                )
        for vector in self.fixture["overlay_fold_vectors"]:
            with self.subTest(vector=vector["id"]):
                expected = tuple(vector["expected_rgba"])
                self.assertEqual(
                    source_over_rgba(vector["bottom_rgba"], vector["top_rgba"]), expected
                )
                self.assertEqual(fold_overlays([vector["bottom_rgba"], vector["top_rgba"]]), expected)
        ordered = self.fixture["overlay_fold_vectors"][2]
        self.assertNotEqual(
            source_over_rgba(ordered["bottom_rgba"], ordered["top_rgba"]),
            source_over_rgba(ordered["top_rgba"], ordered["bottom_rgba"]),
        )

    def test_product_rounding_and_validation_errors(self):
        self.assertEqual(round_u8_product(1, 127), 0)
        self.assertEqual(round_u8_product(1, 128), 1)
        self.assertEqual(round_u8_product(255, 254), 254)
        for value in (-1, 256, True, 1.5):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                round_u8_product(value, 1)
        with self.assertRaisesRegex(ValueError, "premultiplied RGBA8"):
            source_over_rgb((0, 0, 0), (20, 0, 0, 19))
        with self.assertRaisesRegex(ValueError, "3 channels"):
            source_over_rgb((0, 0), (0, 0, 0, 0))


class DirtyAndCoordinateGoldenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_dirty_union_vectors_cover_movement_and_complete_clear(self):
        for vector in self.fixture["dirty_range_vectors"]:
            with self.subTest(vector=vector["id"]):
                actual = union_dirty_ranges(
                    vector["previous_coverage"],
                    vector["next_coverage"],
                    pixel_count=vector["pixel_count"],
                )
                self.assertEqual([list(item) for item in actual], vector["expected_union"])
        by_id = {item["id"]: item for item in self.fixture["dirty_range_vectors"]}
        self.assertEqual(by_id["movement"]["expected_union"], [[10, 13], [20, 23]])
        self.assertEqual(by_id["complete_clear"]["expected_union"], [[4, 9], [40, 44]])

    def test_alpha_coverage_and_clear_use_previous_plus_new_coverage(self):
        previous = np.zeros((16, 4), dtype=np.uint8)
        current = np.zeros_like(previous)
        previous[2:5] = (1, 2, 3, 4)
        previous[11:13] = (0, 0, 0, 255)
        current[4:8] = (9, 9, 9, 9)
        self.assertEqual(alpha_coverage_ranges(previous), ((2, 5), (11, 13)))
        self.assertEqual(coverage_dirty_union(previous, current), ((2, 8), (11, 13)))
        current.fill(0)
        self.assertEqual(coverage_dirty_union(previous, current), ((2, 5), (11, 13)))

    def test_dirty_range_normalization_and_errors(self):
        self.assertEqual(
            normalize_dirty_ranges(((7, 9), (1, 3), (3, 5), (8, 10)), 10), ((1, 5), (7, 10))
        )
        for ranges in (((0, 0),), ((-1, 2),), ((2, 11),), ((5, 4),), ((1,),)):
            with self.subTest(ranges=ranges), self.assertRaises(ValueError):
                normalize_dirty_ranges(ranges, 10)
        with self.assertRaises(TypeError):
            normalize_dirty_ranges((("1", 2),), 10)
        with self.assertRaises(ValueError):
            coverage_dirty_union(np.zeros((2, 4), np.uint8), np.zeros((3, 4), np.uint8))
        with self.assertRaises(ValueError):
            alpha_coverage_ranges(np.zeros((2, 3), np.uint8))

    def test_all_four_board_offsets_and_boundaries_are_canonical(self):
        for vector in self.fixture["coordinate_vectors"]:
            with self.subTest(vector=vector["id"]):
                if vector["global_valid"]:
                    self.assertEqual(
                        logical_flat_index(
                            vector["global_strip"],
                            vector["led"],
                            strip_count=vector["global_strips"],
                            leds_per_strip=vector["leds_per_strip"],
                        ),
                        vector["expected_global_index"],
                    )
                if vector["valid"]:
                    self.assertEqual(
                        receiver_local_index(
                            vector["global_strip"],
                            vector["led"],
                            global_strip_offset=vector["global_strip_offset"],
                            local_strip_count=vector["local_strips"],
                            leds_per_strip=vector["leds_per_strip"],
                        ),
                        vector["expected_local_index"],
                    )
                else:
                    with self.assertRaises(ValueError):
                        receiver_local_index(
                            vector["global_strip"],
                            vector["led"],
                            global_strip_offset=vector["global_strip_offset"],
                            local_strip_count=vector["local_strips"],
                            leds_per_strip=vector["leds_per_strip"],
                        )

    def test_canvas_mapping_and_board_slices(self):
        for vector in self.fixture["canvas_to_logical_vectors"]:
            self.assertEqual(
                canvas_to_logical_flat_index(
                    vector["canvas_row"],
                    vector["canvas_column"],
                    strip_count=vector["global_strips"],
                    leds_per_strip=vector["leds_per_strip"],
                ),
                vector["expected_global_index"],
            )
        geometry = self.fixture["board_slices"]
        actual = board_flat_slices(
            global_strip_count=geometry["global_strips"],
            leds_per_strip=geometry["leds_per_strip"],
            strips_per_board=geometry["strips_per_board"],
        )
        expected = tuple(
            (board["start_flat_index"], board["end_flat_index"])
            for board in geometry["boards"]
        )
        self.assertEqual(actual, expected)
        self.assertEqual(expected, ((0, 1104), (1104, 2208), (2208, 3312), (3312, 4416)))
        with self.assertRaises(ValueError):
            board_flat_slices(global_strip_count=32, leds_per_strip=138, strips_per_board=7)

    def test_protocol_patch_limits_fit_exact_transaction_ceiling(self):
        protocol = self.fixture["firmware_protocol"]
        count = protocol["max_rgba_pixels_per_patch"]
        packet_bytes = protocol["header_bytes"]["overlay_patch"] + count * 4 + protocol["crc_bytes"]
        self.assertEqual(packet_bytes, protocol["max_transaction_bytes"])
        self.assertEqual(
            sum(patch["count"] for patch in protocol["full_snapshot_patches"]),
            protocol["local_pixels"],
        )
        self.assertEqual(protocol["command_ids"]["overlay_begin"], 48)

    def test_firmware_header_protocol_constants_match_json_authority(self):
        protocol = self.fixture["firmware_protocol"]
        header = (
            ROOT / "firmware/esp32/include/ledgrid/animation_pipeline_contract.hpp"
        ).read_text(encoding="utf-8")

        assignments = {
            "kAnimationPipelineProtocolVersion": protocol["version"],
            "kAnimationPipelineMaxTransactionBytes": protocol["max_transaction_bytes"],
            "kAnimationPipelineCrcBytes": protocol["crc_bytes"],
        }
        for name, expected in assignments.items():
            match = re.search(rf"\b{name}\s*=\s*(\d+)\s*;", header)
            self.assertIsNotNone(match, f"missing firmware constant {name}")
            self.assertEqual(int(match.group(1)), expected, name)

        command_names = {
            "controller_session_begin": "ControllerSessionBegin",
            "overlay_begin": "OverlayBegin",
            "overlay_patch": "OverlayPatch",
            "overlay_commit": "OverlayCommit",
            "overlay_clear": "OverlayClear",
            "overlay_renew": "OverlayRenew",
        }
        for fixture_name, cpp_name in command_names.items():
            match = re.search(rf"\b{cpp_name}\s*=\s*(0x[0-9A-Fa-f]+|\d+)\s*,", header)
            self.assertIsNotNone(match, f"missing firmware command {cpp_name}")
            self.assertEqual(int(match.group(1), 0), protocol["command_ids"][fixture_name])

        assertion_names = {
            "controller_session_begin": "kControllerSessionBeginHeaderBytes",
            "overlay_begin": "kOverlayBeginHeaderBytes",
            "overlay_patch": "kOverlayPatchHeaderBytes",
            "overlay_commit": "kOverlayCommitHeaderBytes",
            "overlay_clear": "kOverlayClearHeaderBytes",
            "overlay_renew": "kOverlayRenewHeaderBytes",
        }
        for fixture_name, cpp_name in assertion_names.items():
            match = re.search(rf"static_assert\({cpp_name}\s*==\s*(\d+)", header)
            self.assertIsNotNone(match, f"missing firmware size assertion {cpp_name}")
            self.assertEqual(int(match.group(1)), protocol["header_bytes"][fixture_name])

        for cpp_name, expected in (
            ("kMaxRgbaPixelsPerPatch", protocol["max_rgba_pixels_per_patch"]),
            ("kContractLocalPixels", protocol["local_pixels"]),
        ):
            match = re.search(rf"static_assert\({cpp_name}\s*==\s*(\d+)", header)
            self.assertIsNotNone(match, f"missing firmware limit assertion {cpp_name}")
            self.assertEqual(int(match.group(1)), expected)


class PresentationValueContractTest(unittest.TestCase):
    def component_ref(self, plugin_id="aurora", provider=ComponentProvider.PYTHON, **kwargs):
        return ComponentRef(plugin_id=plugin_id, provider=provider, **kwargs)

    def test_schema_names_and_deadline_semantics_are_frozen(self):
        self.assertEqual(COMPONENT_DESCRIPTOR_SCHEMA, "ledgrid.component-descriptor")
        self.assertEqual(SCENE_STATE_SCHEMA, "ledgrid.scene-state")
        self.assertEqual(VIBE_STATE_SCHEMA, "ledgrid.vibe-state")
        self.assertEqual(VIBE_PROFILE_SCHEMA, "ledgrid.vibe-profile")
        self.assertEqual(DESIRED_DISPLAY_STATE_SCHEMA, "ledgrid.desired-display-state")
        self.assertEqual(ANIMATION_RUNTIME_CONTEXT_SCHEMA, "ledgrid.animation-runtime-context")
        self.assertEqual(FRAME_CONTRACT_SCHEMA, "ledgrid.layer-frame")
        self.assertEqual(NEXT_DEADLINE_SEMANTICS, "absolute_unscaled_seconds_since_scene_epoch")

    def test_descriptor_is_strict_and_deeply_immutable(self):
        descriptor = ComponentDescriptor(
            manifest_version=1,
            plugin_id="aurora",
            name="Aurora",
            description="An analytic background",
            icon="waves",
            gallery="show",
            provider="python",
            role="background",
            entrypoint="animation.plugins.aurora:Aurora",
            parameter_schema={"palette": {"type": "string", "enum": ["cool", "warm"]}},
            defaults={"palette": "cool"},
            cadence=CadenceContract("fixed_fps", preferred_fps=30),
            timing_adapter="scaled_context",
            vibe_capabilities=("palette_roles",),
        )
        self.assertIs(descriptor.provider, ComponentProvider.PYTHON)
        self.assertIs(descriptor.role, ComponentRole.BACKGROUND)
        self.assertIs(descriptor.timing_adapter, TimingAdapter.SCALED_CONTEXT)
        self.assertIsInstance(descriptor.parameter_schema, MappingProxyType)
        self.assertEqual(descriptor.parameter_schema["palette"]["enum"], ("cool", "warm"))
        with self.assertRaises(TypeError):
            descriptor.defaults["palette"] = "warm"
        with self.assertRaises(ValueError):
            ComponentDescriptor(
                **{**descriptor.__dict__, "manifest_version": 2}
            )
        with self.assertRaises(ValueError):
            CadenceContract(CadenceMode.EVENT_DRIVEN, preferred_fps=1)
        with self.assertRaises(ValueError):
            CadenceContract(CadenceMode.FIXED_FPS)

    def test_component_ref_native_binding_and_preset_identity(self):
        native = self.component_ref(
            "native_aurora",
            ComponentProvider.RECEIVER_NATIVE,
            bundle_digest=DIGEST_A,
            expected_payload_digest=DIGEST_B,
            preset_id="night",
            preset_fingerprint=DIGEST_A,
            parameter_overrides={"gain": 0.5},
            resolved_parameters={"gain": 0.5, "seed": 3},
        )
        self.assertEqual(native.bundle_digest, DIGEST_A)
        with self.assertRaisesRegex(ValueError, "preset_fingerprint requires"):
            self.component_ref(preset_fingerprint=DIGEST_A)
        with self.assertRaisesRegex(ValueError, "native payload digests"):
            self.component_ref(bundle_digest=DIGEST_A)
        with self.assertRaisesRegex(ValueError, "expected_payload_digest"):
            self.component_ref(
                "native_aurora", ComponentProvider.RECEIVER_NATIVE, bundle_digest=DIGEST_A
            )

    def test_scene_has_ordered_unique_slots_and_python_fallback(self):
        background = self.component_ref()
        clock = OverlayRef(
            slot_id="clock",
            component=self.component_ref("clock_overlay"),
            enabled=True,
            opacity=192,
            placement=OverlayPlacement(strip_translation=1, led_translation=-2),
            stale_policy=StalePolicy(ForegroundStalePolicy.CLEAR_AFTER_LEASE, lease_ms=2500),
        )
        alert = OverlayRef(
            slot_id="alert",
            component=self.component_ref("alert_overlay"),
            enabled=False,
            opacity=255,
            placement=OverlayPlacement(clip_policy=ClipPolicy.CLIP_TO_WALL),
            stale_policy=StalePolicy(ForegroundStalePolicy.HOLD),
        )
        scene = SceneState(3, background, (clock, alert), background)
        self.assertEqual([item.slot_id for item in scene.overlays], ["clock", "alert"])
        with self.assertRaisesRegex(ValueError, "unique"):
            SceneState(3, background, (clock, clock), background)
        native_fallback = self.component_ref(
            "native_aurora",
            ComponentProvider.RECEIVER_NATIVE,
            bundle_digest=DIGEST_A,
            expected_payload_digest=DIGEST_B,
        )
        with self.assertRaisesRegex(ValueError, "python provider"):
            SceneState(3, background, (), native_fallback)
        with self.assertRaises(ValueError):
            StalePolicy(ForegroundStalePolicy.HOLD, lease_ms=1)

    def test_vibe_runtime_and_desired_state_are_immutable(self):
        profile = VibeProfile(
            vibe_id="neutral",
            profile_version=1,
            palette_roles={"primary": [1, 2, 3]},
            tempo_scale=1.0,
            luminance_scale=0.8,
            capability_values={"energy": 0.0},
        )
        vibe = VibeState(7, "neutral", 1, DIGEST_A)
        context = AnimationRuntimeContext(
            wall_time=100.0,
            unscaled_elapsed=4.5,
            scaled_elapsed=6.75,
            frame_index=99,
            scene_epoch=12,
            global_width=32,
            height=138,
            local_strip_offset=8,
            local_width=8,
            vibe_id="neutral",
            vibe_profile_version=1,
            palette_roles={"primary": [1, 2, 3]},
            capability_values={"energy": 0.0},
            installation_profile_view={"digest": DIGEST_B},
            plant_modifiers={"active": ["illuminate"]},
        )
        self.assertEqual(profile.palette_roles["primary"], (1, 2, 3))
        self.assertEqual(context.next_deadline_clock, 4.5)
        background = self.component_ref()
        state = DesiredDisplayState(
            revision=9,
            scene=SceneState(3, background, (), background),
            vibe=vibe,
            plant_modifiers={"active": []},
            installation_profile_digest=DIGEST_B,
            output=OutputState(0.8, 1.25, True),
        )
        self.assertEqual(state.schema, DESIRED_DISPLAY_STATE_SCHEMA)
        with self.assertRaises(TypeError):
            state.plant_modifiers["active"] = ("obstacle",)
        with self.assertRaisesRegex(ValueError, "fit within"):
            AnimationRuntimeContext(
                **{**context.__dict__, "local_strip_offset": 30, "local_width": 8}
            )
        with self.assertRaisesRegex(ValueError, "RGB triplet"):
            VibeProfile("bad", 1, {"primary": [1, 2]}, 1.0, 1.0)

    def test_frame_contracts_validate_layout_revision_and_premultiplication(self):
        base_pixels = np.zeros((8, 3), dtype=np.uint8)
        base = BaseFrame(base_pixels, dirty_ranges=((4, 6), (1, 3), (3, 4)))
        self.assertEqual(base.dirty_ranges, ((1, 6),))
        overlay_pixels = np.zeros((8, 4), dtype=np.uint8)
        overlay_pixels[2] = (10, 5, 0, 10)
        overlay = OverlayFrame(overlay_pixels, revision=2, dirty_ranges=((2, 3),))
        self.assertEqual(overlay.revision, 2)
        with self.assertRaisesRegex(ValueError, "premultiplied"):
            OverlayFrame(np.array([[2, 0, 0, 1]], dtype=np.uint8), revision=1)
        with self.assertRaisesRegex(ValueError, "C-contiguous"):
            BaseFrame(np.zeros((8, 4), dtype=np.uint8)[:, :3])
        with self.assertRaisesRegex(ValueError, "changed=False"):
            BaseFrame(base_pixels, changed=False, dirty_ranges=((0, 1),))
        with self.assertRaisesRegex(ValueError, "layer-frame"):
            BaseFrame(base_pixels, schema="wrong")
        with self.assertRaises(TypeError):
            OverlayFrame(overlay_pixels, revision=True)


if __name__ == "__main__":
    unittest.main()
