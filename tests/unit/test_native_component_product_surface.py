"""Phase 3D product acceptance for the repository-native pilot component."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from animation.core.manager import AnimationManager, PreviewLEDController
from animation.core.plugin_loader import AnimationPluginLoader
from animation.core.presentation_contracts import component_preset_fingerprint
from web.app import AnimationWebInterface


ROOT = Path(__file__).resolve().parents[2]
PILOT_ID = "aurora_curtains_native"


class NativePilotPresetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.loader = AnimationPluginLoader()
        cls.plugins = cls.loader.load_all_plugins()
        cls.descriptor = cls.loader.get_component_descriptor(PILOT_ID)

    def test_pilot_is_a_manifest_only_catalog_peer_not_a_python_plugin(self):
        self.assertIsNotNone(self.descriptor)
        self.assertEqual(self.descriptor["provider"], "receiver_native")
        self.assertEqual(self.descriptor["role"], "background")
        self.assertEqual(
            self.descriptor["entrypoint"], "ledgrid.native-background-abi:2"
        )
        self.assertEqual(
            self.descriptor["compatibility"]["classification"],
            "receiver_native_source",
        )
        self.assertFalse(self.descriptor["compatibility"]["implementation_loaded"])
        package = self.loader.get_component_dir(PILOT_ID)
        self.assertEqual(
            package, ROOT / "animation" / "plugins" / PILOT_ID
        )
        self.assertFalse((package / "__init__.py").exists())
        self.assertTrue((package / "native" / "background.cpp").is_file())
        self.assertNotIn(PILOT_ID, self.loader.scan_plugins())
        self.assertNotIn(PILOT_ID, AnimationManager.ALLOWED_PLUGINS)
        self.assertNotIn(PILOT_ID, self.plugins)
        self.assertIsNone(self.loader.get_plugin_file(PILOT_ID))
        self.assertIsNone(self.loader.get_plugin_info(PILOT_ID))
        self.assertNotIn(f"animation.plugins.{PILOT_ID}", sys.modules)

    def test_every_native_preset_is_provider_resolved_and_schema_valid(self):
        paths = list(self.loader.iter_component_preset_files(
            PILOT_ID, provider="receiver_native"
        ))
        self.assertGreaterEqual(len(paths), 1)
        self.assertEqual(list(self.loader.iter_curated_preset_files(PILOT_ID)), [])
        self.assertEqual(
            self.loader.validate_component_parameters(
                PILOT_ID, self.descriptor["defaults"]
            ),
            self.descriptor["defaults"],
        )
        fingerprints = set()
        for path in paths:
            with self.subTest(preset=path.name):
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(payload["preset_id"], path.stem)
                self.assertEqual(payload["animation"], PILOT_ID)
                self.assertIsInstance(payload.get("name"), str)
                self.assertTrue(payload["name"].strip())
                self.assertIsInstance(payload.get("params"), dict)
                validated = self.loader.validate_component_parameters(
                    PILOT_ID, payload["params"]
                )
                self.assertEqual(validated, payload["params"])
                fingerprints.add(component_preset_fingerprint(
                    PILOT_ID, path.stem, payload["params"]
                ))
        self.assertEqual(len(fingerprints), len(paths))

    def test_preview_declaration_is_host_build_only_and_not_readback(self):
        preview = self.descriptor["preview"]
        self.assertEqual(preview["kind"], "native_host_build")
        self.assertFalse(preview["framebuffer_readback"])
        self.assertGreaterEqual(len(preview["capture_seconds"]), 2)
        self.assertGreater(preview["simulation_fps"], 0)


class _RecordingChannel:
    def __init__(self) -> None:
        self.commands: list[dict] = []

    def read_status(self):
        return None

    def send_command(self, action, **data):
        command = {"action": action, "data": data, "command_id": len(self.commands) + 1}
        self.commands.append(command)
        return command


class NativePilotWebProductTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        temporary_root = Path(self.temporary.name)
        self.manager = AnimationManager(
            PreviewLEDController(strips=33, leds_per_strip=138), auto_start=False
        )
        self.channel = _RecordingChannel()
        self.interface = AnimationWebInterface(
            self.channel, self.manager, local_mode=True
        )
        self.interface.animation_presets_dir = temporary_root / "runtime-presets"
        self.interface.scene_presets_dir = temporary_root / "scenes"
        self.interface.animation_presets_dir.mkdir()
        self.interface.scene_presets_dir.mkdir()

        paths = list(self.manager.plugin_loader.iter_component_preset_files(
            PILOT_ID, provider="receiver_native"
        ))
        self.preset_ids = [path.stem for path in paths]
        self.client = self.interface.app.test_client()

    def tearDown(self) -> None:
        self.manager.stop_animation()
        self.temporary.cleanup()

    def test_catalog_and_component_presets_exclude_published_preview_contract(self):
        response = self.client.get(
            "/api/v1/components?provider=receiver_native&role=background"
        )
        self.assertEqual(response.status_code, 200)
        components = response.get_json()["components"]
        pilot = next(item for item in components if item["plugin_id"] == PILOT_ID)
        self.assertNotIn("preview", pilot)
        self.assertFalse(pilot["scene_compatibility"]["selectable"])
        self.assertEqual(pilot["scene_compatibility"]["slots"], [])
        self.assertIn(
            "catalog-visible but not executable",
            pilot["scene_compatibility"]["diagnostic"],
        )

        presets = self.client.get(
            f"/api/v1/components/{PILOT_ID}/presets"
        )
        self.assertEqual(presets.status_code, 200)
        payload = presets.get_json()
        self.assertEqual(payload["component_id"], PILOT_ID)
        for preset in payload["presets"]:
            with self.subTest(preset=preset["preset_id"]):
                self.assertEqual(preset["animation"], PILOT_ID)
                self.assertNotIn("preview", preset)
        self.assertEqual(self.client.get("/preview-assets/generated/missing.webp").status_code, 404)
        self.assertEqual(self.client.get("/preview-assets/runtime/missing.webp").status_code, 404)
        self.assertEqual(self.channel.commands, [])

    def test_python_and_scene_execution_surfaces_reject_native_without_side_effects(self):
        self.assertNotIn(PILOT_ID, self.manager.plugin_loader.loaded_plugins)
        self.assertIsNone(self.manager.get_animation_info(PILOT_ID))
        self.assertIsNone(self.manager._preview_session)
        preset_id = self.preset_ids[0]
        descriptor = self.manager.plugin_loader.get_component_descriptor(PILOT_ID)
        scene = {
            "schema": "ledgrid.scene-state",
            "schema_version": 1,
            "revision": 1,
            "background": {
                "plugin_id": PILOT_ID,
                "provider": "receiver_native",
                "parameter_overrides": {},
                "resolved_parameters": descriptor["defaults"],
                "bundle_digest": "a" * 64,
                "expected_payload_digest": "b" * 64,
            },
            "overlays": [],
            "known_python_fallback": {
                "plugin_id": "solid",
                "provider": "python",
                "parameter_overrides": {},
                "resolved_parameters": {},
            },
        }

        requests = (
            self.client.get(f"/api/animations/{PILOT_ID}"),
            self.client.post(f"/api/start/{PILOT_ID}", json={}),
            self.client.post(
                f"/api/animations/{PILOT_ID}/presets/{preset_id}/apply"
            ),
            self.client.get(f"/api/preview/{PILOT_ID}"),
            self.client.post(
                f"/api/preview/{PILOT_ID}/with_params",
                json={"params": descriptor["defaults"]},
            ),
            self.client.post("/api/v1/scene/validate", json=scene),
            self.client.put("/api/v1/scene", json=scene),
            self.client.post("/api/v1/scene/preview", json={"scene": scene}),
        )
        self.assertEqual(
            [response.status_code for response in requests],
            [404, 428, 428, 400, 400, 400, 503, 400],
        )
        self.assertEqual(self.channel.commands, [])
        self.assertIsNone(self.manager._preview_session)
        self.assertNotIn(PILOT_ID, self.manager.plugin_loader.loaded_plugins)

    def test_native_operations_are_explicit_digest_bound_commands(self):
        invalid = self.client.post(
            "/api/v1/native-backgrounds/not-a-digest/install"
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(self.channel.commands, [])

        install = self.client.post(
            f"/api/v1/native-backgrounds/{'a' * 64}/install"
        )
        self.assertEqual(install.status_code, 202)
        self.assertEqual(
            self.channel.commands[-1],
            {
                "action": "install_native_background",
                "data": {"bundle_digest": "a" * 64},
                "command_id": 1,
            },
        )
        clear = self.client.post(
            f"/api/v1/native-backgrounds/{'a' * 64}/clear-quarantine"
        )
        self.assertEqual(clear.status_code, 202)
        self.assertEqual(
            self.channel.commands[-1]["action"],
            "clear_native_background_quarantine",
        )
        recovery = self.client.post("/api/v1/receiver-native/recover")
        self.assertEqual(recovery.status_code, 202)
        self.assertEqual(
            self.channel.commands[-1]["action"], "recover_receiver_native"
        )

    def test_dashboard_exposes_native_operation_health_and_explicit_recovery(self):
        template = (ROOT / "web/templates/index.html").read_text(encoding="utf-8")
        script = (ROOT / "web/static/js/dashboard.js").read_text(encoding="utf-8")
        for marker in (
            "receiverNativeOperation",
            "receiverNativeArtifact",
            "receiverNativeProgress",
            "receiverNativeRecovery",
        ):
            self.assertIn(marker, template)
            self.assertIn(marker, script)
        self.assertIn("/api/v1/receiver-native/recover", script)


if __name__ == "__main__":
    unittest.main()
