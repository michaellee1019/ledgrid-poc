"""Regression coverage for removal of dashboard image-preview publication."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from web.app import AnimationWebInterface


ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_PATH = ROOT / "web/static/generated/composer/bootstrap.v1.json"


def _exact_key_count(value: object, key: str) -> int:
    if isinstance(value, dict):
        return int(key in value) + sum(
            _exact_key_count(item, key) for item in value.values()
        )
    if isinstance(value, list):
        return sum(_exact_key_count(item, key) for item in value)
    return 0


class _Controller:
    strip_count = 33
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip


class _Manager:
    controller = _Controller()
    preview_controller = controller

    @staticmethod
    def list_animations() -> list[dict]:
        return [{"plugin_name": "solid", "name": "Solid", "parameters": {}}]

    @staticmethod
    def get_animation_info(_name: str) -> dict:
        return {"parameters": {}}


class _Channel:
    @staticmethod
    def read_status() -> None:
        return None

    @staticmethod
    def send_command(*_args: object, **_kwargs: object) -> None:
        return None


class PreviewPublicationRemovalTests(unittest.TestCase):
    def test_asset_routes_are_unregistered_and_preset_discovery_remains_available(self) -> None:
        client = AnimationWebInterface(_Channel(), _Manager(), local_mode=True).app.test_client()
        self.assertEqual(client.get("/preview-assets/generated/old.webp").status_code, 404)
        self.assertEqual(client.get("/preview-assets/runtime/old.webp").status_code, 404)
        response = client.get("/api/animations/solid/presets")
        self.assertEqual(response.status_code, 200)
        self.assertIn("presets", response.get_json())

    def test_publication_modules_and_deployment_hooks_are_absent(self) -> None:
        removed = (
            "animation/core/preview_assets.py",
            "tools/generate_animation_previews.py",
            "web/preview_worker.py",
        )
        self.assertTrue(all(not (ROOT / relative).exists() for relative in removed))
        deploy_source = (ROOT / "tools/deployment/deploy_entrypoint.py").read_text(
            encoding="utf-8"
        )
        sync_source = (ROOT / "tools/deployment/sync_files.sh").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("animation-previews", deploy_source)
        self.assertNotIn("animation-previews", sync_source)
        self.assertNotIn("generate_animation_previews", deploy_source)
        self.assertNotIn("generate_animation_previews", sync_source)

    def test_composer_bootstrap_contains_no_published_preview_contract(self) -> None:
        payload = json.loads(BOOTSTRAP_PATH.read_text(encoding="utf-8"))
        self.assertEqual(_exact_key_count(payload, "preview"), 0)
        self.assertEqual(_exact_key_count(payload, "poster_url"), 0)
        self.assertEqual(_exact_key_count(payload, "loop_url"), 0)
