"""Terminal-removal coverage for the retired direct-frame subsystem."""

from __future__ import annotations

import unittest
from pathlib import Path

from web.app import AnimationWebInterface


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_SOURCES = (
    ROOT / "animation/core/manager.py",
    ROOT / "animation/core/component_catalog.py",
    ROOT / "animation/core/plugin_loader.py",
    ROOT / "ipc/runtime_control.py",
    ROOT / "ipc/scene_contract.py",
    ROOT / "scripts/start_server.py",
    ROOT / "web/app.py",
    ROOT / "web/local_control.py",
    ROOT / "web/static/js/composer.js",
    ROOT / "tools/browser_qualification/fixture_server.py",
    ROOT / "tools/browser_qualification/playwright_probe.mjs",
)


class _Controller:
    strip_count = 33
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip


class _PreviewManager:
    controller = _Controller()
    preview_controller = controller

    def list_animations(self):
        return []


class _NoMutationChannel:
    def read_status(self):
        return {"is_running": False, "led_info": {"strip_count": 33, "leds_per_strip": 138}}

    def send_command(self, *_args, **_kwargs):
        raise AssertionError("retired direct-frame routes must not enqueue commands")


class PainterRuntimeRemovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.interface = AnimationWebInterface(
            _NoMutationChannel(), _PreviewManager(), project_root=ROOT,
        )
        self.client = self.interface.app.test_client()

    def test_every_retired_route_is_unregistered(self) -> None:
        for path in (
            "/painter",
            "/api/painter/updates",
            "/api/painter/frame",
            "/api/painter/clear",
            "/api/painter/masks",
            "/api/painter/presets",
            "/api/painter/presets/example",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path).status_code, 404)
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_runtime_and_browser_sources_have_no_retired_symbols(self) -> None:
        forbidden = (
            "painter_active",
            "painter_set_frame",
            "painter_apply_updates",
            "clear_painter_frame",
            "set_painter_frame",
            "apply_painter_updates",
            "compatibility:painter",
            "/api/painter",
        )
        for source in RUNTIME_SOURCES:
            contents = source.read_text(encoding="utf-8").lower()
            for symbol in forbidden:
                with self.subTest(source=source.name, symbol=symbol):
                    self.assertNotIn(symbol, contents)

    def test_no_runtime_archive_member_mentions_the_retired_subsystem(self) -> None:
        for archive in (ROOT / "animation/native", ROOT / "web/static/generated"):
            for path in archive.rglob("*"):
                if path.is_file():
                    with self.subTest(path=path):
                        self.assertNotIn(b"painter", path.read_bytes().lower())


if __name__ == "__main__":
    unittest.main()
