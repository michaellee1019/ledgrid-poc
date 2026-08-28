"""Fail-closed Python browser runtime binding to an exact LGIP artifact."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from animation.browser_preview.python.runtime import BrowserPreviewRuntime
from animation.core.plant_awareness import GLOBE_REGION_ORDER


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/installation_profile_v1.bin"


class BrowserComposerProfileRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.artifact = FIXTURE.read_bytes()
        cls.digest = cls.artifact[68:100].hex()

    def test_initialize_fails_without_bound_exact_profile(self) -> None:
        runtime = BrowserPreviewRuntime()
        with self.assertRaisesRegex(RuntimeError, "exact managed installation-profile"):
            runtime.initialize(
                "rainbow",
                "RainbowAnimation",
                {"width": 33, "height": 138},
                {"brightness": 0.5},
                installation_profile_digest=self.digest,
            )

    def test_verified_artifact_binds_immutable_named_geometry(self) -> None:
        runtime = BrowserPreviewRuntime()
        with tempfile.TemporaryDirectory() as temporary:
            artifact_path = Path(temporary) / "profile.bin"
            artifact_path.write_bytes(self.artifact)
            receipt = runtime.bind_installation_profile_path(
                str(artifact_path), self.digest
            )

        self.assertEqual(receipt["digest"], self.digest)
        self.assertEqual(tuple(receipt["globeRegions"]), GLOBE_REGION_ORDER)
        ready = runtime.initialize(
            "rainbow",
            "RainbowAnimation",
            {"width": 33, "height": 138},
            {"brightness": 0.5},
            installation_profile_digest=self.digest,
        )
        self.assertEqual(ready["installationProfileDigest"], self.digest)
        masks = runtime.animation.get_plant_masks()
        self.assertEqual(tuple(masks.globe_region_masks), GLOBE_REGION_ORDER)
        self.assertFalse(masks.foliage.flags.writeable)
        self.assertFalse(masks.globe_region_masks["top_left"].flags.writeable)

    def test_mismatched_expected_digest_is_rejected_before_render(self) -> None:
        runtime = BrowserPreviewRuntime()
        with tempfile.TemporaryDirectory() as temporary:
            artifact_path = Path(temporary) / "profile.bin"
            artifact_path.write_bytes(self.artifact)
            with self.assertRaisesRegex(ValueError, "selected content digest"):
                runtime.bind_installation_profile_path(str(artifact_path), "f" * 64)


if __name__ == "__main__":
    unittest.main()
