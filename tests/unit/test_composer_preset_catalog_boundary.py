"""Integrity boundary for the finite Composer-authored preset packet."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from animation.plugins.ascii_drop import AsciiDropAnimation
from web import composer_component_presets
from web.composer_component_presets import ComponentPresetCatalog


ROOT = Path(__file__).resolve().parents[2]


class ComposerPresetCatalogBoundaryTests(unittest.TestCase):
    @staticmethod
    def _raw(**changes: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "version": 2,
            "preset_id": "amber-terminal",
            "name": "Amber terminal",
            "description": "A compact terminal rain.",
            "animation": "ascii_drop",
            "params": {
                "phrase": "SYSTEM READY",
                "story": "terminal",
                "fall_speed": 8.0,
                "density": 0.34,
                "seed": 8088,
            },
        }
        payload.update(changes)
        return payload

    def _catalog(self, payload: dict[str, object]) -> ComponentPresetCatalog:
        if hasattr(self, "tempdir"):
            self.tempdir.cleanup()
        self.tempdir = TemporaryDirectory()
        directory = Path(self.tempdir.name) / "animation/plugins/ascii_drop/presets"
        directory.mkdir(parents=True)
        for preset_id in (
            "amber-terminal", "cyan-datastream", "love-letter", "matrix-rain", "maximum-overflow",
        ):
            candidate = copy.deepcopy(payload)
            candidate["preset_id"] = preset_id
            (directory / f"{preset_id}.json").write_text(json.dumps(candidate), encoding="utf-8")
        return ComponentPresetCatalog(Path(self.tempdir.name), {
            "ascii_drop": AsciiDropAnimation._normalized_parameters,
        })

    def tearDown(self) -> None:
        if hasattr(self, "tempdir"):
            self.tempdir.cleanup()

    def test_membership_freezes_the_current_component_and_preset_packet(self) -> None:
        catalog = ComponentPresetCatalog(ROOT, {
            "ascii_drop": AsciiDropAnimation._normalized_parameters,
        })
        self.assertEqual(len(catalog._membership), 40)
        self.assertEqual(sum(len(entry["preset_ids"]) for entry in catalog._membership.values()), 217)
        self.assertEqual(catalog._membership["clock_overlay"]["preset_ids"], [
            "local-12-hour", "precision-seconds", "remote-team-plus-six",
        ])

    def test_membership_versions_require_exact_integers(self) -> None:
        payload = {
            "version": 1,
            "components": {
                "ascii_drop": {
                    "provider": "python", "component_version": 1,
                    "preset_ids": ["amber-terminal"],
                },
            },
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "membership.json"
            for field, value in (("version", 1.0), ("version", True), ("component_version", 1.0), ("component_version", True)):
                candidate = copy.deepcopy(payload)
                target = candidate if field == "version" else candidate["components"]["ascii_drop"]
                target[field] = value
                path.write_text(json.dumps(candidate), encoding="utf-8")
                with self.subTest(field=field, value=value), patch.object(composer_component_presets, "_MEMBERSHIP_PATH", path):
                    with self.assertRaisesRegex(RuntimeError, "membership is malformed"):
                        ComponentPresetCatalog(Path(directory), {"ascii_drop": AsciiDropAnimation._normalized_parameters})

    def test_untracked_file_is_never_discovered_or_applied(self) -> None:
        catalog = self._catalog(self._raw())
        path = Path(self.tempdir.name) / "animation/plugins/ascii_drop/presets/untracked.json"
        path.write_text(json.dumps(self._raw(preset_id="untracked")), encoding="utf-8")

        self.assertEqual([choice["preset_id"] for choice in catalog.choices("ascii_drop")], [
            "amber-terminal", "cyan-datastream", "love-letter", "matrix-rain", "maximum-overflow",
        ])
        with self.assertRaisesRegex(ValueError, "Unknown authored preset"):
            catalog.apply(self._scene(), "untracked")

    @staticmethod
    def _scene() -> dict[str, object]:
        return {
            "animation": {
                "component_id": "ascii_drop", "provider": "python",
                "version": 1, "role": "animation", "parameters": {"untouched": True},
            },
            "widgets": [],
        }

    def test_invalid_metadata_or_parameters_fail_before_candidate_mutation(self) -> None:
        missing_version = self._raw()
        missing_version.pop("version")
        invalid_payloads = [
            missing_version,
            self._raw(version=1),
            self._raw(version=2.0),
            self._raw(version=True),
            self._raw(name=7),
            self._raw(description=7),
            self._raw(params={**self._raw()["params"], "brightness": 0.5}),
            self._raw(params={**self._raw()["params"], "untracked": True}),
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                catalog = self._catalog(payload)
                scene = self._scene()
                before = copy.deepcopy(scene)
                with self.assertRaises(ValueError):
                    catalog.apply(scene, "amber-terminal")
                self.assertEqual(scene, before)

    def test_malformed_json_is_rejected_before_candidate_mutation(self) -> None:
        catalog = self._catalog(self._raw())
        path = Path(self.tempdir.name) / "animation/plugins/ascii_drop/presets/amber-terminal.json"
        path.write_text("{", encoding="utf-8")
        scene = self._scene()
        before = copy.deepcopy(scene)
        with self.assertRaisesRegex(ValueError, "unreadable"):
            catalog.apply(scene, "amber-terminal")
        self.assertEqual(scene, before)

    def test_component_provider_and_version_must_match_checked_identity(self) -> None:
        catalog = self._catalog(self._raw())
        for field, value in (("provider", "receiver_native"), ("version", 2), ("version", 1.0), ("version", True), ("role", "widget")):
            with self.subTest(field=field):
                scene = self._scene()
                scene["animation"][field] = value
                before = copy.deepcopy(scene)
                with self.assertRaisesRegex(ValueError, "identity"):
                    catalog.apply(scene, "amber-terminal")
                self.assertEqual(scene, before)


if __name__ == "__main__":
    unittest.main()
