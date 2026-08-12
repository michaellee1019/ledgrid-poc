"""Phase 1 rollout-gate contract tests."""

import unittest

from animation.core.feature_flags import (
    ANIMATION_PIPELINE_FEATURE_FLAG_SCHEMA,
    ANIMATION_PIPELINE_FEATURE_FLAG_SCHEMA_VERSION,
    ANIMATION_PIPELINE_FEATURE_FLAGS,
    AnimationPipelineFeatureFlags,
)


class AnimationPipelineFeatureFlagTests(unittest.TestCase):
    EXPECTED_FLAGS = {
        "vibe_context",
        "scene_layers",
        "receiver_local_background",
        "receiver_sparse_overlay",
        "receiver_geometry_profile",
        "receiver_native_modules",
    }

    def test_every_phase_one_flag_is_present_and_defaulted_off(self):
        payload = ANIMATION_PIPELINE_FEATURE_FLAGS.to_dict()

        self.assertEqual(set(payload), self.EXPECTED_FLAGS)
        self.assertTrue(ANIMATION_PIPELINE_FEATURE_FLAGS.all_disabled)
        self.assertEqual(set(payload.values()), {False})

    def test_flag_schema_identity_is_frozen(self):
        self.assertEqual(
            ANIMATION_PIPELINE_FEATURE_FLAG_SCHEMA,
            "ledgrid.animation-pipeline-feature-flags",
        )
        self.assertEqual(ANIMATION_PIPELINE_FEATURE_FLAG_SCHEMA_VERSION, 1)

    def test_partial_mapping_enables_only_the_requested_gate(self):
        flags = AnimationPipelineFeatureFlags.from_mapping({"scene_layers": True})

        self.assertTrue(flags.scene_layers)
        self.assertFalse(flags.vibe_context)
        self.assertFalse(flags.all_disabled)

    def test_none_resolves_to_the_canonical_all_off_state(self):
        self.assertEqual(
            AnimationPipelineFeatureFlags.from_mapping(None),
            ANIMATION_PIPELINE_FEATURE_FLAGS,
        )

    def test_unknown_flag_is_rejected_instead_of_silently_ignored(self):
        with self.assertRaisesRegex(ValueError, "unknown.*future_flag"):
            AnimationPipelineFeatureFlags.from_mapping({"future_flag": False})

    def test_non_mapping_payload_is_rejected(self):
        with self.assertRaisesRegex(TypeError, "must be a mapping"):
            AnimationPipelineFeatureFlags.from_mapping([])

    def test_boolean_validation_rejects_truthy_values(self):
        for value in (1, 0, "true", None):
            with self.subTest(value=value):
                with self.assertRaisesRegex(TypeError, "scene_layers"):
                    AnimationPipelineFeatureFlags.from_mapping(
                        {"scene_layers": value}
                    )

    def test_frozen_contract_cannot_be_mutated(self):
        with self.assertRaises((AttributeError, TypeError)):
            ANIMATION_PIPELINE_FEATURE_FLAGS.scene_layers = True


if __name__ == "__main__":
    unittest.main()
