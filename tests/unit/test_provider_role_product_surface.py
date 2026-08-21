"""Phase 2D provider/role dashboard and compatibility acceptance."""

from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path

from animation.core.feature_flags import AnimationPipelineFeatureFlags
from animation.core.plugin_loader import AnimationPluginLoader
from animation.core.preview_assets import empty_catalog, write_catalog
from animation.core.receiver_static_component import (
    COMPILED_RAINBOW_PLUGIN_ID,
    receiver_static_component_descriptor,
)
from ipc.scene_contract import SceneProviderPolicy
from web.app import AnimationWebInterface

ROOT = Path(__file__).resolve().parents[2]
PILOT_ID = "aurora_curtains_native"


class _DashboardDom(HTMLParser):
    """Dependency-free reader for the rendered dashboard contract."""

    def __init__(self) -> None:
        super().__init__()
        self.components: dict[str, dict] = {}
        self.galleries: dict[str, dict] = {}
        self.select_options: dict[str, list[dict[str, str]]] = {}
        self.elements_by_id: dict[str, dict[str, str]] = {}
        self._component: str | None = None
        self._gallery: str | None = None
        self._select: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        element_id = attributes.get("id")
        if element_id:
            self.elements_by_id[element_id] = attributes
        if tag == "article" and attributes.get("data-component-id"):
            self._component = (
                f'{attributes.get("data-provider")}:'
                f'{attributes["data-component-id"]}'
            )
            self.components[self._component] = self._record(attributes)
        elif tag == "article" and attributes.get("data-animation-card"):
            self._gallery = attributes["data-animation-card"]
            self.galleries[self._gallery] = self._record(attributes)
        if self._component is not None:
            self._append_element(self.components[self._component], tag, attributes)
        if self._gallery is not None:
            self._append_element(self.galleries[self._gallery], tag, attributes)
        if tag == "select" and element_id:
            self._select = element_id
            self.select_options.setdefault(element_id, [])
        elif tag == "option" and self._select is not None:
            self.select_options[self._select].append(attributes)

    def handle_endtag(self, tag: str) -> None:
        if tag == "article":
            self._component = None
            self._gallery = None
        elif tag == "select":
            self._select = None

    def handle_data(self, data: str) -> None:
        if not data.strip():
            return
        if self._component is not None:
            self.components[self._component]["text"].append(data.strip())
        if self._gallery is not None:
            self.galleries[self._gallery]["text"].append(data.strip())

    @staticmethod
    def _record(attributes: dict[str, str]) -> dict:
        return {"attrs": attributes, "tags": [], "elements": [], "text": []}

    @staticmethod
    def _append_element(record: dict, tag: str, attributes: dict[str, str]) -> None:
        record["tags"].append(tag)
        record["elements"].append((tag, attributes))


class _Controller:
    strip_count = 32
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip


class _ProductPreviewManager:
    controller = _Controller()
    preview_controller = controller

    def __init__(self, *, enabled: bool, duplicate_id: bool = False) -> None:
        self.feature_flags = AnimationPipelineFeatureFlags(
            receiver_local_background=enabled,
            receiver_sparse_overlay=enabled,
        )
        self.plugin_loader = AnimationPluginLoader()
        self.duplicate_id = duplicate_id

    def scene_provider_policy(self) -> SceneProviderPolicy:
        return SceneProviderPolicy(
            receiver_local_background=self.feature_flags.receiver_local_background,
            receiver_sparse_overlay=self.feature_flags.receiver_sparse_overlay,
        )

    def list_animations(self) -> list[dict]:
        return [{
            "plugin_name": "gradient",
            "name": "Gradient",
            "description": "A smooth host-rendered gradient.",
            "emoji": "🌈",
            "is_test": False,
            "parameters": {},
        }]

    def list_components(self) -> list[dict]:
        catalog = [
            {
                "plugin_id": "gradient",
                "name": "Gradient",
                "description": "A smooth host-rendered gradient.",
                "icon": "🌈",
                "provider": "python",
                "role": "background",
                "parameter_schema": {},
                "defaults": {},
                "compatibility": {"composable": True},
            },
            {
                "plugin_id": "clock_overlay",
                "name": "Clock overlay",
                "description": "Fixed sparse clock overlay.",
                "icon": "🕒",
                "provider": "python",
                "role": "overlay",
                "parameter_schema": {},
                "defaults": {},
                "compatibility": {"composable": True},
            },
            {
                "plugin_id": "clock",
                "name": "Clock",
                "description": "Compatibility full scene.",
                "icon": "🕰️",
                "provider": "python",
                "role": "full_scene",
                "parameter_schema": {},
                "defaults": {},
                "compatibility": {"composable": False},
            },
        ]
        pilot = self.plugin_loader.get_component_descriptor(PILOT_ID)
        assert pilot is not None
        catalog.append(pilot)
        compiled = receiver_static_component_descriptor(self.feature_flags)
        if compiled is not None:
            catalog.append(compiled)
        if self.duplicate_id:
            catalog.append({
                "plugin_id": "gradient",
                "name": "Receiver Gradient",
                "description": "Conflicting provider-qualified identity.",
                "icon": "⚠️",
                "provider": "receiver_native",
                "role": "background",
                "parameter_schema": {},
                "defaults": {},
                "preview": {
                    "kind": "native_host_build",
                    "framebuffer_readback": False,
                },
                "compatibility": {"composable": True},
            })
        return catalog

    def get_animation_info(self, name: str) -> dict | None:
        return next((
            item for item in self.list_animations()
            if item["plugin_name"] == name
        ), None)

    @staticmethod
    def get_vibe_status() -> dict:
        from animation.core.presentation_contracts import resolve_vibe

        return {"state": resolve_vibe("neutral").state.to_dict()}


class _StatusChannel:
    def __init__(self, flags: AnimationPipelineFeatureFlags) -> None:
        self.commands: list[dict] = []
        self.status = {
            "is_running": True,
            "current_animation": COMPILED_RAINBOW_PLUGIN_ID,
            "feature_flags": flags.to_dict(),
            "plant_modifiers": {"version": 1, "active": [], "strengths": {}},
            "led_info": {
                "strip_count": 32,
                "leds_per_strip": 138,
                "total_leds": 4416,
            },
            "scene": {
                "provider_mode": "receiver_hybrid",
                "receiver": {
                    "healthy": False,
                    "degraded": True,
                    "telemetry_complete": False,
                    "release_acceptance": False,
                    "transport_policy": "degraded_spi1_01_readable",
                    "readable_devices": [0, 1],
                    "unverified_devices": [2, 3],
                    "fallback_active": True,
                    "source_scene_revision": 17,
                    "context_revision": 9,
                    "publisher": {
                        "lease_ms": 3000,
                        "generation": 7,
                        "last_operation": "renew_failed",
                        "last_error": "receiver agreement lost",
                    },
                },
            },
        }

    def read_status(self) -> dict:
        return deepcopy(self.status)

    def send_command(self, action: str, **data) -> dict:
        command = {"action": action, "data": data}
        self.commands.append(command)
        return command


class ProviderRoleProductSurfaceTests(unittest.TestCase):
    def _surface(
        self, *, enabled: bool = True, duplicate_id: bool = False
    ) -> tuple[AnimationWebInterface, object, _StatusChannel]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        manager = _ProductPreviewManager(
            enabled=enabled, duplicate_id=duplicate_id
        )
        channel = _StatusChannel(manager.feature_flags)
        interface = AnimationWebInterface(channel, manager, local_mode=True)
        interface.generated_preview_dir = root / "generated"
        interface.runtime_preview_dir = root / "runtime-previews"
        interface.animation_presets_dir = root / "runtime-presets"
        interface.scene_presets_dir = root / "scenes"
        interface.animation_presets_dir.mkdir()
        interface.scene_presets_dir.mkdir()

        catalog = empty_catalog(32, 138)
        for component_id in ("gradient", PILOT_ID):
            catalog["animations"][component_id] = {
                "status": "ready",
                "digest": ("1" if component_id == "gradient" else "2") * 64,
                "poster_url": f"/preview-assets/generated/{component_id}-poster.webp",
                "loop_url": f"/preview-assets/generated/{component_id}-loop.webp",
                "frame_count": 12,
                "duration_ms": 80,
                "static": False,
            }
        write_catalog(interface.generated_preview_dir / "catalog.json", catalog)
        return interface, interface.app.test_client(), channel

    def test_rendered_catalog_uses_descriptors_and_keeps_native_pilot_read_only(self):
        _interface, client, channel = self._surface()
        html = client.get("/").get_data(as_text=True)
        dom = _DashboardDom()
        dom.feed(html)

        gallery_text = " ".join(dom.galleries["gradient"]["text"])
        self.assertIn("Host Python", gallery_text)
        self.assertIn("Background", gallery_text)

        pilot = dom.components[f"receiver_native:{PILOT_ID}"]
        self.assertEqual(pilot["attrs"]["data-provider"], "receiver_native")
        self.assertEqual(pilot["attrs"]["data-role"], "background")
        self.assertEqual(pilot["attrs"]["data-selectable"], "false")
        self.assertEqual(pilot["attrs"]["data-identity-ambiguous"], "false")
        self.assertGreater(int(pilot["attrs"]["data-preset-count"]), 0)
        pilot_text = " ".join(pilot["text"])
        self.assertIn("Catalog / build only", pilot_text)
        self.assertIn("Host-build preview", pilot_text)
        self.assertIn("not current wall output", pilot_text)
        self.assertIn("receiver framebuffer readback", pilot_text)
        self.assertNotIn("button", pilot["tags"])

        preview_images = [
            attributes for tag, attributes in pilot["elements"]
            if tag == "img" and "component-native-preview" in attributes.get(
                "class", ""
            )
        ]
        self.assertEqual(len(preview_images), 1)
        self.assertIn("not current wall output", preview_images[0]["alt"])

        options = {
            (item.get("data-provider"), item.get("data-component-id"))
            for item in dom.select_options["sceneBackgroundSelect"]
        }
        self.assertIn(("python", "gradient"), options)
        self.assertIn(("receiver_native", COMPILED_RAINBOW_PLUGIN_ID), options)
        self.assertNotIn(("receiver_native", PILOT_ID), options)
        self.assertEqual(
            dom.elements_by_id["sceneBackgroundMetadata"]["aria-live"], "polite"
        )
        self.assertEqual(channel.commands, [])

        presets = client.get(f"/api/v1/components/{PILOT_ID}/presets")
        self.assertEqual(presets.status_code, 200)
        payload = presets.get_json()
        self.assertEqual(payload["component"]["provider"], "receiver_native")
        self.assertEqual(payload["component"]["role"], "background")
        self.assertGreater(len(payload["presets"]), 0)
        self.assertEqual(channel.commands, [])

    def test_compiled_rainbow_visibility_requires_both_feature_gates(self):
        _enabled_interface, enabled_client, _enabled_channel = self._surface()
        enabled = enabled_client.get(
            "/api/v1/components?provider=receiver_native&role=background"
        ).get_json()["components"]
        self.assertIn(
            COMPILED_RAINBOW_PLUGIN_ID,
            {item["plugin_id"] for item in enabled},
        )

        _disabled_interface, disabled_client, disabled_channel = self._surface(
            enabled=False
        )
        disabled = disabled_client.get(
            "/api/v1/components?provider=receiver_native&role=background"
        ).get_json()["components"]
        self.assertEqual({item["plugin_id"] for item in disabled}, {PILOT_ID})
        html = disabled_client.get("/").get_data(as_text=True)
        self.assertNotIn(
            f'id="componentCatalog-receiver_native-{COMPILED_RAINBOW_PLUGIN_ID}"',
            html,
        )
        self.assertIn(
            f'id="componentCatalog-receiver_native-{PILOT_ID}"', html
        )
        self.assertNotIn('id="receiverHybridStatus"', html)
        self.assertEqual(disabled_channel.commands, [])

    def test_cross_provider_duplicate_id_fails_closed_for_id_only_assets(self):
        _interface, client, channel = self._surface(duplicate_id=True)
        response = client.get("/api/v1/components/gradient/presets")
        self.assertEqual(response.status_code, 409)
        payload = response.get_json()
        self.assertEqual(payload["providers"], ["python", "receiver_native"])
        self.assertIn("provider-qualified preset storage", payload["error"])

        html = client.get("/").get_data(as_text=True)
        dom = _DashboardDom()
        dom.feed(html)
        host = dom.components["python:gradient"]
        receiver = dom.components["receiver_native:gradient"]
        for record in (host, receiver):
            self.assertEqual(record["attrs"]["data-identity-ambiguous"], "true")
            self.assertEqual(record["attrs"]["data-preset-count"], "0")
            self.assertIn(
                "Preview and preset decoration are withheld",
                " ".join(record["text"]),
            )
        self.assertFalse(any(tag == "img" for tag in receiver["tags"]))
        self.assertEqual(channel.commands, [])

    def test_degraded_status_and_unsupported_quarantine_are_explicit(self):
        _interface, client, _channel = self._surface()
        status = client.get("/api/status").get_json()
        receiver = status["scene"]["receiver"]
        self.assertTrue(receiver["fallback_active"])
        self.assertFalse(receiver["telemetry_complete"])
        self.assertFalse(receiver["release_acceptance"])
        self.assertEqual(
            receiver["transport_policy"], "degraded_spi1_01_readable"
        )

        html = client.get("/").get_data(as_text=True)
        for element_id in (
            "receiverAgreementState",
            "receiverFallbackState",
            "receiverTelemetryState",
            "receiverReleaseAcceptance",
            "receiverTransportPolicy",
            "receiverQuarantineState",
            "receiverHybridDetail",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn("Not supported in this phase", html)

        javascript = (ROOT / "web/static/js/dashboard.js").read_text(
            encoding="utf-8"
        )
        for contract in (
            "item.provider === provider",
            "Unknown or ambiguous scene component",
            "sceneBackgroundProvider",
            "sceneBackgroundRole",
            "telemetry_complete",
            "release_acceptance",
            "transport_policy",
            "readable_devices",
            "unverified_devices",
            "Not supported in this phase",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, javascript)

        css = (ROOT / "web/static/css/dashboard.css").read_text(encoding="utf-8")
        self.assertIn(".component-catalog-grid", css)
        self.assertIn("@media (max-width: 767.98px)", css)
        self.assertIn(
            ".component-catalog-grid { grid-template-columns: 1fr; }", css
        )


if __name__ == "__main__":
    unittest.main()
