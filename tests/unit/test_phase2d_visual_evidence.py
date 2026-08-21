"""Reproducibility and contract acceptance for Phase 2D visual evidence."""

from __future__ import annotations

import struct
import tempfile
import unittest
from datetime import datetime
from hashlib import sha256
from pathlib import Path

import numpy as np

from animation.core.compositing import source_over_rgb
from animation.core.feature_flags import AnimationPipelineFeatureFlags
from animation.core.manager import PreviewLEDController
from animation.core.plugin_loader import AnimationPluginLoader
from animation.core.presentation_contracts import CANONICAL_VIBE_IDS, resolve_vibe
from animation.core.receiver_presentation import apply_luminance_u8
from animation.core.receiver_static_component import (
    receiver_static_component_descriptor,
)
from tools.generate_phase2d_visual_evidence import (
    BACKGROUND,
    CAPTURE_TIMES,
    DISCLOSURE_TEXT,
    FIXED_CLOCK,
    FIXED_SCENE_EPOCH,
    FIXED_TIMEZONE,
    FIXED_WALL_TIME,
    HEADER_HEIGHT,
    HYBRID_DISCLOSURE,
    HYBRID_FLAGS,
    LEFT_LABEL_WIDTH,
    PIXEL_COUNT,
    ROW_SPECS,
    SHEET_HEIGHT,
    SHEET_WIDTH,
    WALL_HEIGHT,
    WALL_WIDTH,
    apply_receiver_luminance_rgba,
    build_contact_sheet,
    build_runtime_context,
    physical_rgb_to_visual,
    source_over_receiver_preview,
    write_contact_sheet,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = REPO_ROOT / "docs" / "phase2d-semantic-vibe-contact-sheet.png"
ARTIFACT_SHA256 = "6d0800196e93e63c5b746fa0884d2f7e3be776e576b4dee6b0f77d42c5bcbd92"


class Phase2DVisualEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.evidence = build_contact_sheet()

    def test_tracked_png_is_byte_reproducible_with_frozen_dimensions_and_sha(self):
        tracked = ARTIFACT.read_bytes()
        self.assertEqual(sha256(tracked).hexdigest(), ARTIFACT_SHA256)
        self.assertEqual(tracked[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", tracked[16:24]), (SHEET_WIDTH, SHEET_HEIGHT))
        self.assertEqual((SHEET_WIDTH, SHEET_HEIGHT), (746, 1836))

        with tempfile.TemporaryDirectory() as temporary:
            generated_path = Path(temporary) / "evidence.png"
            generated, digest = write_contact_sheet(generated_path)
            self.assertEqual(generated.pixels.shape, (SHEET_HEIGHT, SHEET_WIDTH, 3))
            self.assertEqual(digest, ARTIFACT_SHA256)
            self.assertEqual(generated_path.read_bytes(), tracked)

    def test_tiles_cover_each_family_and_vibe_and_are_globally_distinct(self):
        expected_pairs = {
            (spec.row_id, vibe_id)
            for spec in ROW_SPECS
            for vibe_id in CANONICAL_VIBE_IDS
        }
        actual_pairs = {
            (tile.row_id, tile.vibe_id) for tile in self.evidence.tiles
        }
        self.assertSetEqual(actual_pairs, expected_pairs)
        self.assertEqual(len(self.evidence.tiles), 20)
        self.assertEqual(
            len({tile.digest for tile in self.evidence.tiles}),
            len(self.evidence.tiles),
        )
        for tile in self.evidence.tiles:
            with self.subTest(row=tile.row_id, vibe=tile.vibe_id):
                self.assertEqual(tile.physical_rgb.shape, (PIXEL_COUNT, 3))
                self.assertEqual(tile.physical_rgb.dtype, np.uint8)
                self.assertTrue(tile.physical_rgb.flags.c_contiguous)

    def test_hybrid_tiles_are_explicit_host_simulation_not_framebuffer_claims(self):
        self.assertEqual(
            HYBRID_DISCLOSURE,
            "HOST-SIMULATION / NOT RECEIVER FRAMEBUFFER READBACK",
        )
        hybrid = [
            tile for tile in self.evidence.tiles if tile.row_id == "receiver_hybrid"
        ]
        self.assertEqual(len(hybrid), len(CANONICAL_VIBE_IDS))
        for tile in hybrid:
            self.assertEqual(tile.source_label, HYBRID_DISCLOSURE)
            self.assertFalse(tile.framebuffer_readback)

        # The same amber disclosure is visibly drawn in the global header and
        # again inside the hybrid row's left label panel.
        disclosure_color = np.asarray(DISCLOSURE_TEXT, dtype=np.uint8)
        header = self.evidence.pixels[45:66, :LEFT_LABEL_WIDTH]
        label_panel = self.evidence.pixels[:, :LEFT_LABEL_WIDTH]
        self.assertTrue(np.any(np.all(header == disclosure_color, axis=2)))
        self.assertGreater(
            np.count_nonzero(np.all(label_panel == disclosure_color, axis=2)),
            np.count_nonzero(np.all(header == disclosure_color, axis=2)),
        )

    def test_feature_gate_and_provider_role_contracts_fail_closed(self):
        enabled = receiver_static_component_descriptor(HYBRID_FLAGS)
        self.assertIsNotNone(enabled)
        self.assertEqual(enabled["provider"], "receiver_native")
        self.assertEqual(enabled["role"], "background")
        self.assertFalse(enabled["preview"]["framebuffer_readback"])

        for flags in (
            AnimationPipelineFeatureFlags(),
            AnimationPipelineFeatureFlags(receiver_local_background=True),
            AnimationPipelineFeatureFlags(receiver_sparse_overlay=True),
        ):
            with self.subTest(flags=flags):
                self.assertIsNone(receiver_static_component_descriptor(flags))
                with self.assertRaisesRegex(RuntimeError, "both rollout gates"):
                    build_contact_sheet(feature_flags=flags)

        loader = AnimationPluginLoader(allowed_plugins=("clock_overlay",))
        loader.scan_plugins()
        manifest = loader.plugin_manifests["clock_overlay"]
        self.assertEqual(manifest["provider"], "python")
        self.assertEqual(manifest["role"], "overlay")

    def test_runtime_contexts_are_complete_fixed_and_capability_exact(self):
        plugin_ids = tuple(
            spec.plugin_id for spec in ROW_SPECS if spec.plugin_id is not None
        )
        loader = AnimationPluginLoader(allowed_plugins=plugin_ids)
        plugins = loader.load_all_plugins()
        controller = PreviewLEDController(WALL_WIDTH, WALL_HEIGHT)
        self.assertEqual(
            datetime.fromtimestamp(FIXED_WALL_TIME, tz=FIXED_TIMEZONE),
            FIXED_CLOCK,
        )

        for spec in ROW_SPECS:
            if spec.plugin_id is None:
                continue
            animation = plugins[spec.plugin_id](controller, dict(spec.config))
            for vibe_id in CANONICAL_VIBE_IDS:
                resolved = resolve_vibe(vibe_id)
                runtime = build_runtime_context(
                    animation,
                    resolved,
                    elapsed=CAPTURE_TIMES[-1],
                    frame_index=len(CAPTURE_TIMES) - 1,
                )
                expected_tempo = (
                    resolved.profile.tempo_scale
                    if "tempo" in animation.VIBE_CAPABILITIES
                    else 1.0
                )
                expected_luminance = (
                    resolved.profile.luminance_scale
                    if "luminance" in animation.VIBE_CAPABILITIES
                    else 1.0
                )
                with self.subTest(plugin=spec.plugin_id, vibe=vibe_id):
                    self.assertEqual(runtime.wall_time, FIXED_WALL_TIME)
                    self.assertEqual(runtime.scene_epoch, FIXED_SCENE_EPOCH)
                    self.assertEqual(
                        (runtime.global_width, runtime.height),
                        (WALL_WIDTH, WALL_HEIGHT),
                    )
                    self.assertEqual(runtime.local_strip_offset, 0)
                    self.assertEqual(runtime.local_width, WALL_WIDTH)
                    self.assertEqual(runtime.palette_roles, resolved.profile.palette_roles)
                    self.assertEqual(
                        runtime.capability_values,
                        resolved.profile.capability_values,
                    )
                    self.assertEqual(runtime.tempo_scale, expected_tempo)
                    self.assertEqual(runtime.luminance_scale, expected_luminance)
                    self.assertEqual(
                        runtime.effective_time_scale,
                        runtime.authored_speed * expected_tempo,
                    )
                    self.assertEqual(
                        runtime.scaled_elapsed,
                        runtime.unscaled_elapsed * runtime.effective_time_scale,
                    )

    def test_receiver_luminance_and_source_over_match_scalar_golden_math(self):
        overlay = np.zeros((PIXEL_COUNT, 4), dtype=np.uint8)
        overlay[:4] = (
            (0, 0, 0, 0),
            (40, 20, 10, 80),
            (128, 64, 32, 128),
            (255, 220, 7, 255),
        )
        luminance_q8_8 = 141
        scaled = apply_receiver_luminance_rgba(overlay, luminance_q8_8)
        for pixel_index in range(4):
            alpha = int(overlay[pixel_index, 3])
            expected_rgb = tuple(
                min(
                    alpha,
                    apply_luminance_u8(
                        int(overlay[pixel_index, channel]), luminance_q8_8
                    ),
                )
                for channel in range(3)
            )
            self.assertEqual(tuple(scaled[pixel_index, :3]), expected_rgb)
            self.assertEqual(int(scaled[pixel_index, 3]), alpha)

        base = np.full((PIXEL_COUNT, 3), (17, 91, 203), dtype=np.uint8)
        composed = source_over_receiver_preview(base, scaled)
        for pixel_index in range(4):
            expected = source_over_rgb(
                tuple(int(value) for value in base[pixel_index]),
                tuple(int(value) for value in scaled[pixel_index]),
            )
            self.assertEqual(tuple(composed[pixel_index]), expected)

    def test_physical_to_visual_orientation_places_led_zero_at_bottom(self):
        frame = np.zeros((PIXEL_COUNT, 3), dtype=np.uint8)
        physical = frame.reshape(WALL_WIDTH, WALL_HEIGHT, 3)
        physical[0, 0] = (255, 0, 0)
        physical[0, WALL_HEIGHT - 1] = (0, 255, 0)
        physical[WALL_WIDTH - 1, 0] = (0, 0, 255)

        visual = physical_rgb_to_visual(frame)
        self.assertEqual(visual.shape, (WALL_HEIGHT, WALL_WIDTH, 3))
        self.assertEqual(tuple(visual[-1, 0]), (255, 0, 0))
        self.assertEqual(tuple(visual[0, 0]), (0, 255, 0))
        self.assertEqual(tuple(visual[-1, -1]), (0, 0, 255))
        self.assertEqual(
            tuple(self.evidence.pixels[0, LEFT_LABEL_WIDTH]), BACKGROUND
        )
        self.assertGreater(HEADER_HEIGHT, 0)


if __name__ == "__main__":
    unittest.main()
