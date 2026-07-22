"""Tests for deterministic dashboard preview assets and catalogs."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from animation.core.plugin_loader import AnimationPluginLoader
from animation.core.preview_assets import (
    FIXED_CLOCK,
    PreviewRenderer,
    clean_stale_assets,
    empty_catalog,
    load_catalog,
    merge_catalogs,
    write_catalog,
)
from web.preview_worker import RuntimePreviewWorker
from web.app import AnimationWebInterface


ROOT = Path(__file__).resolve().parents[2]


class AnimationPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.output = Path(self.temporary_dir.name)

    def tearDown(self):
        self.temporary_dir.cleanup()

    def test_renderer_writes_native_lossless_loop_and_reuses_digest(self):
        renderer = PreviewRenderer(
            ROOT, self.output, "/preview-test", strips=4, leds_per_strip=8
        )
        renderer.loader.plugin_manifests["rainbow"]["preview"] = {
            "capture_seconds": [0, 0.05, 0.1], "simulation_fps": 20,
        }
        entry = renderer.render("rainbow")
        poster = self.output / entry["poster_url"].rsplit("/", 1)[-1]
        loop = self.output / entry["loop_url"].rsplit("/", 1)[-1]
        self.assertTrue(poster.is_file())
        self.assertTrue(loop.is_file())
        with Image.open(poster) as image:
            self.assertEqual(image.size, (4, 8))
            self.assertEqual(image.format, "WEBP")
        with Image.open(loop) as image:
            self.assertEqual(image.info.get("loop"), 0)
            self.assertGreaterEqual(getattr(image, "n_frames", 1), 2)
        # Pillow's WebP decoder exposes loop count and frame count, but not the
        # encoded frame timestamps. The catalog retains the authored cadence.
        self.assertEqual(entry["duration_ms"], 500)
        before = (poster.stat().st_mtime_ns, loop.stat().st_mtime_ns)
        reused = renderer.render("rainbow")
        self.assertEqual(entry["digest"], reused["digest"])
        self.assertEqual(before, (poster.stat().st_mtime_ns, loop.stat().st_mtime_ns))

    def test_physical_led_zero_is_rendered_at_image_bottom(self):
        renderer = PreviewRenderer(
            ROOT, self.output, "/preview-test", strips=2, leds_per_strip=3
        )
        pixels = np.zeros((6, 3), dtype=np.uint8)
        pixels[0] = (255, 0, 0)
        pixels[2] = (0, 255, 0)
        image = renderer._normalize_frame(pixels)
        self.assertEqual(tuple(image[-1, 0]), (255, 0, 0))
        self.assertEqual(tuple(image[0, 0]), (0, 255, 0))

    def test_fixed_clock_is_1019(self):
        self.assertEqual((FIXED_CLOCK.hour, FIXED_CLOCK.minute, FIXED_CLOCK.second), (10, 19, 0))

    def test_manifest_rejects_bad_preview_profiles(self):
        manifest = self.output / "manifest.json"
        manifest.write_text(json.dumps({
            "plugin_id": "sample", "class": "Sample", "icon": "✨",
            "preview": {"capture_seconds": [1, 1], "simulation_fps": 30},
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            AnimationPluginLoader._validate_manifest(manifest, "sample")
        manifest.write_text(json.dumps({
            "plugin_id": "sample", "class": "Sample", "icon": "✨",
            "preview": {"capture_seconds": [0, 1], "simulation_fps": 121},
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "1 to 120"):
            AnimationPluginLoader._validate_manifest(manifest, "sample")

    def test_runtime_catalog_overrides_curated_and_cleanup_is_scoped(self):
        curated = empty_catalog(32, 138)
        curated["animations"]["rainbow"] = {"status": "ready", "digest": "old"}
        runtime = empty_catalog(32, 138)
        runtime["animations"]["rainbow"] = {"status": "ready", "digest": "new"}
        runtime["presets"]["rainbow"] = {
            "saved": {"status": "ready", "loop_url": "/preview/saved.webp"}
        }
        merged = merge_catalogs(curated, runtime)
        self.assertEqual(merged["animations"]["rainbow"]["digest"], "new")
        keep = self.output / "saved.webp"
        stale = self.output / "stale.webp"
        unrelated = self.output / "notes.txt"
        for path in (keep, stale, unrelated):
            path.write_bytes(b"x")
        clean_stale_assets(self.output, merged)
        self.assertTrue(keep.exists())
        self.assertFalse(stale.exists())
        self.assertTrue(unrelated.exists())

    def test_runtime_worker_deduplicates_queue_and_deletes_assets(self):
        project = self.output / "project"
        preset = project / "presets" / "animations" / "rainbow" / "saved.json"
        preset.parent.mkdir(parents=True)
        preset.write_text("{}", encoding="utf-8")
        worker = RuntimePreviewWorker(project, strips=32, leds_per_strip=138)

        class _AliveThread:
            @staticmethod
            def is_alive():
                return True

        worker._thread = _AliveThread()
        fallback = {"poster_url": "/poster.webp", "loop_url": "/loop.webp"}
        worker.queue("rainbow", "saved", preset, fallback)
        worker.queue("rainbow", "saved", preset, fallback)
        self.assertEqual(worker._jobs.qsize(), 1)
        self.assertEqual(
            load_catalog(worker.catalog_path)["presets"]["rainbow"]["saved"]["status"],
            "pending",
        )
        worker.delete("rainbow", "saved")
        self.assertNotIn(
            "rainbow", load_catalog(worker.catalog_path).get("presets", {})
        )

    def test_dashboard_and_preset_api_expose_lazy_preview_metadata(self):
        class _Controller:
            strip_count = 32
            leds_per_strip = 138
            total_leds = strip_count * leds_per_strip

        class _Manager:
            controller = _Controller()
            preview_controller = controller

            @staticmethod
            def list_animations():
                return [{
                    "plugin_name": "rainbow", "name": "Rainbow",
                    "description": "Color motion", "emoji": "🌈", "is_test": False,
                }]

            @staticmethod
            def get_animation_info(_name):
                return {"parameters": {}}

        class _Channel:
            @staticmethod
            def read_status():
                return None

            @staticmethod
            def send_command(*_args, **_kwargs):
                return None

        interface = AnimationWebInterface(_Channel(), _Manager())
        interface.generated_preview_dir = self.output / "generated"
        interface.runtime_preview_dir = self.output / "runtime"
        interface.animation_presets_dir = self.output / "presets"
        catalog = empty_catalog(32, 138)
        catalog["animations"]["rainbow"] = {
            "status": "ready", "poster_url": "/poster.webp", "loop_url": "/loop.webp",
        }
        catalog["presets"]["rainbow"] = {
            "calm": {
                "status": "ready", "poster_url": "/calm-poster.webp",
                "loop_url": "/calm-loop.webp",
            }
        }
        write_catalog(interface.generated_preview_dir / "catalog.json", catalog)
        preset_dir = interface.animation_presets_dir / "rainbow"
        preset_dir.mkdir(parents=True)
        (preset_dir / "calm.json").write_text(json.dumps({
            "preset_id": "calm", "name": "Calm", "animation": "rainbow", "params": {},
        }), encoding="utf-8")

        client = interface.app.test_client()
        html = client.get("/").get_data(as_text=True)
        self.assertIn('class="generated-preview animation-preview"', html)
        self.assertIn('loading="lazy"', html)
        self.assertIn('data-loop-src="/calm-loop.webp"', html)
        summary = client.get("/api/animations/rainbow/presets").get_json()["presets"][0]
        self.assertEqual(summary["preview"]["status"], "ready")


if __name__ == "__main__":
    unittest.main()
