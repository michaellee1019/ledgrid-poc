"""Exercise every current Composer Animation through preview and host playback."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from animation.core.component_catalog import ComponentRole
from animation.core.manager import AnimationManager, PreviewLEDController
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT
from tools.benchmarks.animation_render import scene_for
from web.composer_final_preview import ComposerFinalPreview, current_component_catalog


ROOT = Path(__file__).resolve().parents[2]


class AllComposerAnimationsLiveTests(unittest.TestCase):
    def test_every_animation_renders_the_same_installed_preview_and_host_frame(self):
        catalog = current_component_catalog()
        animations = [item for item in catalog.descriptors if item.role is ComponentRole.ANIMATION]
        self.assertTrue(animations)
        preview = ComposerFinalPreview(catalog, ROOT)
        controller = PreviewLEDController(DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP)
        manager = AnimationManager(controller, auto_start=False)
        manager._launch_animation_loop = lambda: None
        wall_time = datetime(2026, 9, 2, 12, tzinfo=timezone.utc).astimezone()
        try:
            for descriptor in animations:
                with self.subTest(component=descriptor.component_id):
                    canonical = scene_for(catalog, descriptor.component_id, {})
                    with patch("animation.core.canonical_receiver_scene.datetime") as controller_clock:
                        controller_clock.now.return_value = wall_time
                        self.assertTrue(manager.start_canonical_scene(canonical.scene), manager._playback_error)
                    for elapsed in (0.0, 0.2):
                        final = preview.render(canonical, elapsed, wall_time)
                        host = manager.render_scene_v2_presentation(
                            manager._canonical_receiver_runtime, manager._canonical_receiver_scene,
                            monotonic_elapsed=elapsed, wall_time=wall_time,
                        )
                        self.assertEqual(host.pixels.shape, (controller.total_leds, 3))
                        self.assertEqual(host.pixels.dtype, np.uint8)
                        self.assertTrue(host.pixels.flags.c_contiguous)
                        np.testing.assert_array_equal(host.pixels, final.pixels)
        finally:
            manager.stop_animation(clear_leds=False)


if __name__ == "__main__":
    unittest.main()
