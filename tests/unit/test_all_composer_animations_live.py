"""Catalog-wide local-preview and strict live-manager qualification."""

from __future__ import annotations

import contextlib
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import unittest

import numpy as np

from animation.core.manager import AnimationManager, PreviewLEDController
from animation.core.presentation_contracts import ComponentRef, SceneState
from ipc.scene_contract import (
    browser_scene_to_host_scene,
    normalize_browser_scene_document,
    normalize_composer_scene,
)
from web.composer_final_preview import (
    ComposerFinalPreview,
    NATIVE_AURORA_BUNDLE_DIGEST,
    current_component_catalog,
)


ROOT = Path(__file__).resolve().parents[2]


class AllComposerAnimationsLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = current_component_catalog()
        cls.animations = tuple(
            descriptor
            for descriptor in cls.catalog.descriptors
            if descriptor.role.value == "animation"
        )

    def _canonical(self, descriptor):
        return normalize_composer_scene({
            "origin": "composer",
            "scene": {
                "schema": "ledgrid.scene.v2",
                "background": {
                    "component_id": "native_aurora",
                    "version": 1,
                    "provider": "receiver_native",
                    "role": "background",
                    "bundle_digest": NATIVE_AURORA_BUNDLE_DIGEST,
                    "parameters": {"gain": 0.0, "source_fps": 30.0, "seed": 0},
                },
                "animation": {
                    "component_id": descriptor.component_id,
                    "version": descriptor.version,
                    "provider": descriptor.provider.value,
                    "role": descriptor.role.value,
                    "parameters": {},
                },
                "widgets": [],
                "plants": {
                    "effects": {"version": 1, "active": [], "strengths": {}},
                },
                "look": {
                    "palette_id": "neutral",
                    "pace": 1.0,
                    "presentation_brightness": 1.0,
                },
            },
        }, self.catalog)

    def test_every_composer_animation_renders_in_installed_local_preview(self) -> None:
        self.assertEqual(len(self.animations), 39)
        preview = ComposerFinalPreview(self.catalog, ROOT)
        wall_time = datetime(2026, 9, 2, 12, tzinfo=timezone.utc)
        for descriptor in self.animations:
            with self.subTest(component=descriptor.component_id):
                preview.render(self._canonical(descriptor), 0.0, wall_time)
                frame = preview.render(self._canonical(descriptor), 0.2, wall_time)
                self.assertEqual(frame.pixels.shape, (33 * 138, 3))
                self.assertEqual(frame.pixels.dtype, np.uint8)
                self.assertTrue(frame.pixels.flags.c_contiguous)

    def test_every_composer_animation_starts_in_strict_live_manager(self) -> None:
        controller = PreviewLEDController(strips=33, leds_per_strip=138)
        with contextlib.redirect_stdout(io.StringIO()):
            manager = AnimationManager(controller, auto_start=False)
        manager._launch_animation_loop = lambda: None
        try:
            for revision, descriptor in enumerate(self.animations, 1):
                with self.subTest(component=descriptor.component_id):
                    component = ComponentRef(
                        plugin_id=descriptor.component_id,
                        provider=descriptor.provider,
                        resolved_parameters=descriptor.default_parameters(),
                    )
                    self.assertTrue(manager.start_scene(SceneState(
                        revision=revision,
                        background=component,
                        overlays=(),
                        known_python_fallback=component,
                    )))
                    frame = manager.render_composed_scene_frame()
                    self.assertEqual(frame.pixels.shape, (33 * 138, 3))
                    self.assertEqual(frame.pixels.dtype, np.uint8)
        finally:
            manager.stop_animation(clear_leds=False)

    def test_every_published_animation_passes_portable_activation_validation(self) -> None:
        bootstrap = json.loads((
            ROOT / "web/static/generated/composer/bootstrap.v1.json"
        ).read_text(encoding="utf-8"))
        catalog = bootstrap["components"]
        by_id = {
            item["plugin_id"]: item for item in catalog
            if item.get("provider") == "python"
            and item.get("role") == "background"
        }
        profile_digest = bootstrap["installation_profile"]["digest"]
        controller = PreviewLEDController(strips=33, leds_per_strip=138)
        with contextlib.redirect_stdout(io.StringIO()):
            manager = AnimationManager(controller, auto_start=False)
        manager._launch_animation_loop = lambda: None
        try:
            for descriptor in self.animations:
                component_id = descriptor.component_id
                with self.subTest(component=component_id):
                    published = by_id[component_id]
                    managed = published["browser_capabilities"]["managed_identity"]
                    binding = {
                        "provider": managed["provider"],
                        "component_id": managed["component_id"],
                        "component_digest": managed["component_digest"],
                        "runtime_digest": managed["runtime_digest"],
                        "parameter_schema_version": managed[
                            "parameter_schema_version"
                        ],
                        "parameters": deepcopy(published.get("defaults") or {}),
                    }
                    document = {
                        "schema": "ledgrid.browser-scene",
                        "schema_version": 1,
                        "revision": 1,
                        "background": binding,
                        "layers": [],
                        "installation_profile": {"digest": profile_digest},
                        "fallback": deepcopy(binding),
                    }
                    normalized = normalize_browser_scene_document(
                        document, catalog=catalog, purpose="activation"
                    )
                    host_scene = browser_scene_to_host_scene(
                        normalized, catalog=catalog
                    )
                    self.assertTrue(manager.start_scene(host_scene))
                    frame = manager.render_composed_scene_frame()
                    self.assertEqual(frame.pixels.shape, (33 * 138, 3))
        finally:
            manager.stop_animation(clear_leds=False)


if __name__ == "__main__":
    unittest.main()
