"""Acceptance for removing the final semantic clock parameter bridges."""

from __future__ import annotations

import hashlib
import json
import unittest
from copy import deepcopy
from datetime import datetime, timezone

import numpy as np

from animation.core.plugin_loader import AnimationPluginLoader
from animation.core.presentation_contracts import (
    CANONICAL_VIBE_IDS,
    AnimationRuntimeContext,
    component_preset_fingerprint,
    resolve_vibe,
)

PLUGIN_IDS = ("clock", "clock_overlay")
FIXED_NOW = datetime(2026, 8, 21, 14, 37, 42, tzinfo=timezone.utc)
CAPTURE_SECONDS = (0.0, 1.0, 61.0)
BASELINE_DIGESTS = {
    "clock": {
        "neutral": "252f3c723fb929ad5dd5e01b33164446179baae4bba222b9dbf239aab728e9dc",
        "quiet": "ab31af58b36513cccc9c16a83cca02f7611e5c6fee9182045ce554de41f860e2",
        "cozy": "605d01ab9633c8cdd81e7c20d61bf583f53b97e18c0868a0202d3dfeb68b4ff7",
        "vivid": "2277161240db8c062939d309df5aa6da271dd75022f05f82fbed831c1ded5543",
        "celebration": "9247fc9b626cf838ea1896f60815fa637a7fff8a60ac507cf5d3eb5a98ce5dab",
    },
    "clock_overlay": {
        "neutral": "7c1b6962a273abd6a34eb1712a1e49b0ca11990a5f6e3bd8b75fc057902d1517",
        "quiet": "b63d9dc6855cd5e267a23fff3978d980b73a315a5ab1095d79864c913a7d2805",
        "cozy": "36f0b77fa3534d88f6e0681967046109bb33532912dd66963d80481c782b2515",
        "vivid": "9c84c1658466ba67329c1bc75d524c62058c3e9e48f8fd134259f4b3c1e41232",
        "celebration": "864174bc3bdc528a81439dbe5c3f650fe8793bebe76df48feb90a0a6b8cb5771",
    },
}
CONFIGS = {
    "clock": {
        "face": "orbit",
        "background": "aurora",
        "palette": "ocean",
        "show_seconds": True,
        "motion": 1.1,
        "density": 0.72,
        "brightness": 0.83,
    },
    "clock_overlay": {
        "face": "analog",
        "palette": "ocean",
        "show_seconds": True,
        "glow": 0.6,
        "backdrop_opacity": 0.2,
    },
}


class Controller:
    strip_count = 32
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip
    debug = False


def runtime_context(
    vibe_id: str,
    *,
    elapsed: float,
    frame_index: int,
) -> AnimationRuntimeContext:
    resolved = resolve_vibe(vibe_id)
    return AnimationRuntimeContext(
        wall_time=FIXED_NOW.timestamp() + elapsed,
        unscaled_elapsed=elapsed,
        scaled_elapsed=elapsed,
        frame_index=frame_index,
        scene_epoch=51,
        global_width=32,
        height=138,
        local_strip_offset=0,
        local_width=32,
        vibe_id=vibe_id,
        vibe_profile_version=resolved.state.profile_version,
        resolved_profile_digest=resolved.state.resolved_profile_digest,
        palette_roles=resolved.profile.palette_roles,
        capability_values=resolved.profile.capability_values,
        tempo_scale=1.0,
        luminance_scale=resolved.profile.luminance_scale,
        operator_tempo_scale=1.0,
        authored_speed=1.0,
        effective_time_scale=1.0,
        installation_profile_view={},
        plant_modifiers={},
    )


class ClockBridgeCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.loader = AnimationPluginLoader(allowed_plugins=PLUGIN_IDS)
        cls.plugins = cls.loader.load_all_plugins()
        cls.controller = Controller()

        class FixedClock(cls.plugins["clock"]):
            def _clock_now(self):
                return self._apply_clock_offset(FIXED_NOW)

        class FixedOverlay(cls.plugins["clock_overlay"]):
            def _clock_now(self):
                return self._apply_clock_offset(FIXED_NOW)

        cls.fixed_classes = {
            "clock": FixedClock,
            "clock_overlay": FixedOverlay,
        }

    def test_final_semantic_manifests_have_no_legacy_parameter_bridges(self) -> None:
        for plugin_id, animation_class in self.plugins.items():
            vibe = self.loader.plugin_manifests[plugin_id]["vibe"]
            with self.subTest(plugin=plugin_id):
                self.assertEqual(vibe["color_policy"], "semantic")
                self.assertEqual(vibe["timing_adapter"], "wall_clock")
                self.assertEqual(vibe["capabilities"], ["luminance", "palette_roles"])
                self.assertNotIn("legacy_parameter_mappings", vibe)
                self.assertEqual(animation_class.VIBE_PARAMETER_MAPPINGS, {})

        catalog = AnimationPluginLoader()
        catalog.scan_components()
        remaining = {
            plugin_id: manifest["vibe"]["legacy_parameter_mappings"]
            for plugin_id, manifest in catalog.component_manifests.items()
            if manifest.get("vibe", {}).get("legacy_parameter_mappings")
        }
        self.assertEqual(remaining, {})

    def test_neutral_and_non_neutral_frames_match_pre_removal_bytes(self) -> None:
        for plugin_id, animation_class in self.fixed_classes.items():
            fingerprints = set()
            for vibe_id in CANONICAL_VIBE_IDS:
                animation = animation_class(self.controller, CONFIGS[plugin_id])
                frames = []
                for index, elapsed in enumerate(CAPTURE_SECONDS):
                    rendered = animation.generate_frame_with_context(runtime_context(
                        vibe_id, elapsed=elapsed, frame_index=index
                    ))
                    frames.append(rendered.pixels.tobytes())
                digest = hashlib.sha256(b"".join(frames)).hexdigest()
                fingerprints.add(digest)
                with self.subTest(plugin=plugin_id, vibe=vibe_id):
                    self.assertEqual(digest, BASELINE_DIGESTS[plugin_id][vibe_id])
                    self.assertEqual(
                        animation.authored_params_snapshot()["palette"], "ocean"
                    )
            self.assertEqual(len(fingerprints), len(CANONICAL_VIBE_IDS))

    def test_shared_renderer_replaces_slots_directly_without_parameter_mapping(self) -> None:
        vivid = runtime_context("vivid", elapsed=0.0, frame_index=0)
        expected = (
            vivid.palette_roles["background_low"],
            vivid.palette_roles["accent"],
            vivid.palette_roles["hud"],
            vivid.palette_roles["background_mid"],
        )
        for plugin_id, animation_class in self.fixed_classes.items():
            animation = animation_class(self.controller, CONFIGS[plugin_id])
            animation.set_presentation_context(vivid)
            with self.subTest(plugin=plugin_id):
                self.assertEqual(animation._presentation_palette(), expected)
                self.assertEqual(animation.authored_params_snapshot()["palette"], "ocean")

    def test_live_switch_keeps_fixed_clock_and_authored_state(self) -> None:
        for plugin_id, animation_class in self.fixed_classes.items():
            animation = animation_class(self.controller, CONFIGS[plugin_id])
            quiet = animation.generate_frame_with_context(runtime_context(
                "quiet", elapsed=0.17, frame_index=1
            ))
            authored = animation.authored_params_snapshot()
            clock_before = animation._clock_now()
            celebration = animation.generate_frame_with_context(runtime_context(
                "celebration", elapsed=0.17, frame_index=2
            ))
            with self.subTest(plugin=plugin_id):
                self.assertEqual(animation._clock_now(), clock_before)
                self.assertEqual(animation.authored_params_snapshot(), authored)
                self.assertTrue(celebration.changed)
                self.assertFalse(
                    np.array_equal(quiet.pixels, celebration.pixels)
                )

    def test_clock_fixed_rate_and_overlay_event_cadence_remain_intact(self) -> None:
        clock = self.fixed_classes["clock"](self.controller, CONFIGS["clock"])
        clock_changed = 0
        for index in range(200):
            rendered = clock.generate_frame_with_context(runtime_context(
                "vivid", elapsed=index / 200.0, frame_index=index
            ))
            clock_changed += int(rendered.changed)
        self.assertGreaterEqual(clock_changed, 12)
        self.assertLessEqual(clock_changed, 13)

        overlay = self.plugins["clock_overlay"](
            self.controller, CONFIGS["clock_overlay"]
        )
        overlay_changed = 0
        for index in range(400):
            rendered = overlay.generate_frame_with_context(runtime_context(
                "vivid", elapsed=index / 200.0, frame_index=index
            ))
            overlay_changed += int(rendered.changed)
        self.assertEqual(overlay_changed, 2)

    def test_all_curated_clock_presets_keep_identity_under_every_vibe(self) -> None:
        paths = tuple(self.loader.iter_curated_preset_files("clock"))
        self.assertEqual(len(paths), 24)
        fingerprints = {vibe_id: set() for vibe_id in CANONICAL_VIBE_IDS}
        animation_class = self.fixed_classes["clock"]
        for path in paths:
            payload = json.loads(path.read_text(encoding="utf-8"))
            original = deepcopy(payload)
            identity = component_preset_fingerprint(
                "clock", payload["preset_id"], payload["params"]
            )
            validated = self.loader.validate_component_parameters(
                "clock", payload["params"]
            )
            for vibe_id in CANONICAL_VIBE_IDS:
                animation = animation_class(self.controller, validated)
                rendered = animation.generate_frame_with_context(runtime_context(
                    vibe_id, elapsed=0.25, frame_index=1
                ))
                fingerprints[vibe_id].add(
                    hashlib.sha256(rendered.pixels.tobytes()).digest()
                )
                self.assertEqual(
                    animation.authored_params_snapshot()["palette"],
                    payload["params"]["palette"],
                )
            with self.subTest(preset=path.stem):
                self.assertEqual(payload, original)
                self.assertEqual(payload["preset_id"], path.stem)
                self.assertEqual(
                    component_preset_fingerprint(
                        "clock", payload["preset_id"], payload["params"]
                    ),
                    identity,
                )
        for vibe_id, values in fingerprints.items():
            with self.subTest(vibe=vibe_id):
                self.assertEqual(len(values), len(paths))
        self.assertEqual(tuple(self.loader.iter_curated_preset_files("clock_overlay")), ())


if __name__ == "__main__":
    unittest.main()
