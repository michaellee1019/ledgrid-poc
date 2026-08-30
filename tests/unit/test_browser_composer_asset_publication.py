"""Regression coverage for the atomic Composer generated-asset publication."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.build_browser_composer_assets import (
    asset_manifest,
    check_published,
    publish,
    validate_asset_set,
)


ROOT = Path(__file__).resolve().parents[2]
PUBLISHED = ROOT / "web/static/generated/composer"


class ComposerAssetPublicationTests(unittest.TestCase):
    def copied_assets(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        copied = Path(temporary.name) / "composer"
        shutil.copytree(PUBLISHED, copied)
        return temporary, copied

    def test_clean_publication_check_rebuilds_the_same_complete_asset_set(self) -> None:
        first = check_published(ROOT)
        second = check_published(ROOT)
        self.assertEqual(first, second)

    def test_check_fails_closed_for_stale_missing_and_orphan_generated_output(self) -> None:
        for name, mutate, expected in (
            (
                "stale",
                lambda path: path.joinpath("bootstrap.v1.json").write_bytes(b"stale"),
                "offline manifest is stale",
            ),
            (
                "missing",
                lambda path: path.joinpath("compiled_rainbow.wasm").unlink(),
                "stale, missing, or orphaned",
            ),
            (
                "orphan",
                lambda path: path.joinpath("painter_preview.png").write_bytes(b"ghost"),
                "ghost Composer artifacts",
            ),
            (
                "nested-orphan",
                lambda path: (path / "retained" / "emoji").mkdir(parents=True),
                "ghost Composer artifacts",
            ),
        ):
            with self.subTest(name=name):
                temporary, copied = self.copied_assets()
                with temporary:
                    mutate(copied)
                    with self.assertRaisesRegex(ValueError, expected):
                        validate_asset_set(ROOT, copied)

    def test_failed_final_replace_restores_the_exact_prior_publication(self) -> None:
        before = asset_manifest(PUBLISHED)
        original_replace = __import__("os").replace

        def staged_copy(_root: Path, stage: Path) -> dict[str, object]:
            shutil.copytree(PUBLISHED, stage)
            return asset_manifest(stage)

        def fail_final_replace(source: Path, destination: Path) -> None:
            if source.name == "composer" and destination == PUBLISHED:
                raise OSError("injected final publication failure")
            original_replace(source, destination)

        with patch("tools.build_browser_composer_assets.build_stage", staged_copy), patch(
            "tools.build_browser_composer_assets.os.replace", fail_final_replace
        ):
            with self.assertRaisesRegex(OSError, "injected final publication failure"):
                publish(ROOT)
        self.assertEqual(asset_manifest(PUBLISHED), before)
        self.assertEqual(
            sorted(PUBLISHED.parent.glob("composer-assets-stage-*")), [],
            "failed publication must not expose a partial staging directory",
        )


if __name__ == "__main__":
    unittest.main()
