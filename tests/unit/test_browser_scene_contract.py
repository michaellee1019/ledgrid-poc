"""Portable browser-scene identity, validation, and web-boundary coverage."""

from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from animation.component_parameters import SCENE_EXTERNAL_COMPONENT_PARAMETERS
from animation.core.presentation_contracts import SceneState
from ipc.scene_contract import (
    BROWSER_SCENE_MAX_BYTES,
    BROWSER_SCENE_SCHEMA,
    SceneValidationError,
    browser_scene_to_host_scene,
    decorate_browser_component,
    decorate_catalog,
    normalize_browser_scene_document,
    normalize_scene_payload,
    validate_bounded_browser_json,
)
from web.app import AnimationWebInterface


EMPTY_PROFILE = "0" * 64


def _component(
    component_id: str,
    *,
    role: str = "background",
    composable: bool = True,
) -> dict:
    return {
        "plugin_id": component_id,
        "provider": "python",
        "role": role,
        "name": component_id.replace("_", " ").title(),
        "entrypoint": f"animation.plugins.{component_id}:FixtureAnimation",
        "parameter_schema_version": 1,
        "parameter_schema": {
            "speed": {
                "type": "float",
                "min": 0.1,
                "max": 5.0,
                "default": 1.0,
            },
            "map_path": {
                "type": "str",
                "default": "config/managed-map.json",
            },
            "plant_aware": {
                "type": "bool",
                "default": False,
            },
        },
        "defaults": {
            "speed": 1.0,
            "map_path": "config/managed-map.json",
            "plant_aware": False,
            "plant_modifiers": {"version": 1, "active": [], "strengths": {}},
            "vibe": {"id": "neutral"},
            "output": {"brightness": 50},
        },
        "availability": {"state": "ready"},
        "compatibility": {
            "composable": composable,
            "implementation_loaded": True,
            "diagnostic": (
                "Ready fixture." if composable else "Preview-only fixture."
            ),
        },
        "build": {},
    }


def _catalog() -> list[dict]:
    raw = decorate_catalog([
        _component("gradient"),
        _component("clock_overlay", role="overlay"),
        _component("preview_only", composable=False),
    ])
    result = []
    for index, component in enumerate(raw, start=1):
        result.append(decorate_browser_component(
            component,
            browser_runtime={
                "kind": "python",
                "supported": True,
                "engine": "python-pyodide-wasm",
                "digest": f"{index:064x}",
            },
        ))
    return result


def _binding(component: dict, *, speed: float = 0.7) -> dict:
    identity = component["browser_capabilities"]["managed_identity"]
    return {
        "provider": identity["provider"],
        "component_id": identity["component_id"],
        "component_digest": identity["component_digest"],
        "runtime_digest": identity["runtime_digest"],
        "parameter_schema_version": identity["parameter_schema_version"],
        "parameters": {
            "speed": speed,
            "map_path": "config/managed-map.json",
            "plant_aware": True,
        },
    }


def _document(catalog: list[dict]) -> dict:
    by_id = {item["plugin_id"]: item for item in catalog}
    background = _binding(by_id["gradient"])
    return {
        "schema": BROWSER_SCENE_SCHEMA,
        "schema_version": 1,
        "revision": 17,
        "background": background,
        "layers": [{
            "role": "clock",
            "component": _binding(by_id["clock_overlay"], speed=1.0),
            "enabled": True,
            "opacity": 220,
            "blend_mode": "source_over",
        }],
        "installation_profile": {"digest": EMPTY_PROFILE},
        "fallback": deepcopy(background),
    }


class BrowserScenePortableContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = _catalog()
        self.document = _document(self.catalog)

    def test_capabilities_distinguish_preview_save_and_activation(self) -> None:
        by_id = {item["plugin_id"]: item for item in self.catalog}
        ready = by_id["gradient"]["browser_capabilities"]
        self.assertEqual(
            {key: ready[key] for key in (
                "previewable", "saveable", "activation_ready"
            )},
            {
                "previewable": True,
                "saveable": True,
                "activation_ready": True,
            },
        )
        self.assertIsNone(ready["reason"])
        self.assertRegex(ready["managed_identity"]["component_digest"], r"^[0-9a-f]{64}$")

        preview_only = by_id["preview_only"]["browser_capabilities"]
        self.assertTrue(preview_only["previewable"])
        self.assertTrue(preview_only["saveable"])
        self.assertFalse(preview_only["activation_ready"])
        self.assertIn("Preview-only", preview_only["reason"])

    def test_scene_external_parameter_authority_is_shared_by_all_consumers(self) -> None:
        consumers = (
            "animation.core.presentation_contracts",
            "animation.core.manager",
            "animation.core.component_catalog",
            "animation.native.schema",
            "ipc.scene_contract",
        )
        for module_name in consumers:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                self.assertIs(
                    module.SCENE_EXTERNAL_COMPONENT_PARAMETERS,
                    SCENE_EXTERNAL_COMPONENT_PARAMETERS,
                )

    def test_document_round_trips_and_adapts_to_existing_host_scene(self) -> None:
        normalized = normalize_browser_scene_document(
            self.document, catalog=self.catalog, purpose="activation"
        )
        self.assertEqual(normalized, self.document)
        host = browser_scene_to_host_scene(normalized, catalog=self.catalog)
        self.assertEqual(host["schema"], "ledgrid.scene-state")
        self.assertEqual(host["revision"], 17)
        self.assertEqual(host["background"]["plugin_id"], "gradient")
        self.assertEqual(host["background"]["parameter_overrides"]["speed"], 0.7)
        self.assertEqual(host["overlays"][0]["slot_id"], "clock_overlay")
        self.assertEqual(host["overlays"][0]["opacity"], 220)
        reserved = {"plant_aware", "plant_modifiers", "vibe", "output"}
        refs = (
            host["background"],
            host["overlays"][0]["component"],
            host["known_python_fallback"],
        )
        for ref in refs:
            self.assertFalse(reserved & set(ref["parameter_overrides"]))
            self.assertFalse(reserved & set(ref["resolved_parameters"]))
            self.assertEqual(ref["resolved_parameters"]["map_path"], "config/managed-map.json")
        self.assertEqual(SceneState.from_payload(host).to_dict(), host)

    def test_host_boundary_rejects_scene_external_component_state(self) -> None:
        host = browser_scene_to_host_scene(self.document, catalog=self.catalog)
        cases = (
            ("background", "parameter_overrides", "plant_aware", False),
            ("background", "resolved_parameters", "plant_modifiers", {}),
            ("known_python_fallback", "parameter_overrides", "vibe", {}),
            ("known_python_fallback", "resolved_parameters", "output", {}),
        )
        for ref_name, collection, name, value in cases:
            with self.subTest(ref=ref_name, collection=collection, name=name):
                candidate = deepcopy(host)
                candidate[ref_name][collection][name] = value
                with self.assertRaisesRegex(
                    SceneValidationError,
                    rf"{ref_name}.*scene-external state.*{name}",
                ):
                    normalize_scene_payload(candidate, catalog=self.catalog)

    def test_identity_schema_parameter_and_revision_errors_name_the_field(self) -> None:
        mutations = (
            (("schema_version",), 2, r"scene\.schema_version"),
            (("revision",), -1, r"scene\.revision"),
            (("background", "provider"), "javascript", r"background\.provider"),
            (("background", "component_id"), "missing", r"background\.component_id"),
            (("background", "component_digest"), "f" * 64, r"background\.component_digest"),
            (("background", "runtime_digest"), "e" * 64, r"background\.runtime_digest"),
            (("background", "parameter_schema_version"), 2, r"background\.parameter_schema_version"),
            (("background", "parameters", "speed"), 99, r"parameters\.speed"),
        )
        for path, value, message in mutations:
            with self.subTest(path=path):
                candidate = deepcopy(self.document)
                target = candidate
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaisesRegex(SceneValidationError, message):
                    normalize_browser_scene_document(
                        candidate, catalog=self.catalog, purpose="activation"
                    )

    def test_fixed_layer_fallback_and_managed_asset_contracts_are_strict(self) -> None:
        cases = []

        duplicate = deepcopy(self.document)
        duplicate["layers"].append(deepcopy(duplicate["layers"][0]))
        cases.append((duplicate, "at most one clock layer"))

        wrong_role = deepcopy(self.document)
        wrong_role["layers"][0]["role"] = "foreground"
        cases.append((wrong_role, r"layers\[0\]\.role"))

        wrong_blend = deepcopy(self.document)
        wrong_blend["layers"][0]["blend_mode"] = "screen"
        cases.append((wrong_blend, r"blend_mode"))

        wrong_clock = deepcopy(self.document)
        wrong_clock["layers"][0]["component"] = deepcopy(wrong_clock["background"])
        cases.append((wrong_clock, "catalog role 'overlay'"))

        mismatched_fallback = deepcopy(self.document)
        mismatched_fallback["fallback"]["parameters"]["speed"] = 1.1
        cases.append((mismatched_fallback, "fallback must match"))

        traversal = deepcopy(self.document)
        traversal["background"]["parameters"]["map_path"] = "../../secret.json"
        traversal["fallback"] = deepcopy(traversal["background"])
        cases.append((traversal, r"parameters\.map_path"))

        for candidate, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(SceneValidationError, message):
                    normalize_browser_scene_document(
                        candidate, catalog=self.catalog, purpose="activation"
                    )

    def test_capability_is_enforced_for_the_requested_boundary(self) -> None:
        by_id = {item["plugin_id"]: item for item in self.catalog}
        preview_only = deepcopy(self.document)
        preview_only["background"] = _binding(by_id["preview_only"])
        preview_only["fallback"] = deepcopy(preview_only["background"])

        normalize_browser_scene_document(
            preview_only, catalog=self.catalog, purpose="preview"
        )
        normalize_browser_scene_document(
            preview_only, catalog=self.catalog, purpose="save"
        )
        with self.assertRaisesRegex(SceneValidationError, "activation_ready"):
            normalize_browser_scene_document(
                preview_only, catalog=self.catalog, purpose="activation"
            )

    def test_bounded_cell_coordinates_are_portable_activation_parameters(self) -> None:
        catalog = deepcopy(self.catalog)
        descriptor = next(
            item for item in catalog if item["plugin_id"] == "gradient"
        )
        descriptor["parameter_schema"]["seed_cells"] = {
            "type": "cells", "default": [], "max_items": 4,
            "strip_min": 0, "strip_max": 32,
            "led_min": 0, "led_max": 137,
        }
        descriptor["defaults"]["seed_cells"] = []
        valid = deepcopy(self.document)
        for slot in ("background", "fallback"):
            valid[slot]["parameters"]["seed_cells"] = [[0, 0], [32, 137]]
        normalize_browser_scene_document(
            valid, catalog=catalog, purpose="activation"
        )

        invalid = deepcopy(valid)
        for slot in ("background", "fallback"):
            invalid[slot]["parameters"]["seed_cells"] = [[33, 0]]
        with self.assertRaisesRegex(SceneValidationError, "strip must be from 0 to 32"):
            normalize_browser_scene_document(
                invalid, catalog=catalog, purpose="activation"
            )

    def test_bounded_json_rejects_pollution_depth_size_count_and_nonfinite_values(self) -> None:
        nested: dict = {}
        cursor = nested
        for _ in range(18):
            cursor["child"] = {}
            cursor = cursor["child"]
        invalid = (
            ({"__proto__": {}}, "__proto__"),
            (nested, "nesting depth"),
            ({"value": float("nan")}, "finite JSON values"),
            ({"value": "x" * BROWSER_SCENE_MAX_BYTES}, "byte limit"),
            ({"values": [0] * 5000}, "value limit"),
        )
        for value, message in invalid:
            with self.subTest(message=message):
                with self.assertRaisesRegex(SceneValidationError, message):
                    validate_bounded_browser_json(value)


class _Controller:
    strip_count = 33
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip


class _Manager:
    controller = _Controller()
    preview_controller = controller

    def __init__(self) -> None:
        self.components = [
            _component("gradient"),
            _component("clock_overlay", role="overlay"),
        ]

    def list_components(self) -> list[dict]:
        return deepcopy(self.components)

    def list_animations(self) -> list[dict]:
        return []

    def get_animation_info(self, component_id: str) -> dict | None:
        component = next((
            item for item in self.components
            if item["plugin_id"] == component_id
        ), None)
        return (
            {"parameters": deepcopy(component["parameter_schema"])}
            if component is not None else None
        )


class _Channel:
    def __init__(self) -> None:
        self.commands: list[dict] = []
        self.read_count = 0

    def read_status(self) -> dict:
        self.read_count += 1
        return {}

    def send_command(self, action: str, **data) -> dict:
        self.commands.append({"action": action, "data": deepcopy(data)})
        return {"command_id": "contract-test"}


class BrowserSceneWebBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.channel = _Channel()
        self.interface = AnimationWebInterface(
            self.channel, _Manager(), local_mode=True
        )
        root = Path(self.temporary.name)
        self.interface.animation_presets_dir = root / "animations"
        self.interface.scene_presets_dir = root / "scenes"
        self.client = self.interface.app.test_client()
        bootstrap = self.client.get("/api/v1/composer/bootstrap").get_json()
        self.assertEqual(self.channel.commands, [])
        self.assertEqual(self.channel.read_count, 0)
        self.catalog = bootstrap["components"]
        self.document = _document(self.catalog)

    def test_bootstrap_and_unified_catalog_expose_explicit_capabilities(self) -> None:
        bootstrap = self.client.get("/api/v1/composer/bootstrap").get_json()
        self.assertEqual(bootstrap["installation_profile"]["digest"], EMPTY_PROFILE)
        self.assertEqual(bootstrap["installation_profile"]["authority"], "host")
        self.assertEqual(bootstrap["installation_profile"]["plant_modifiers"]["version"], 1)
        for component in self.catalog:
            with self.subTest(component=component["key"]):
                capability = component["browser_capabilities"]
                self.assertIn("previewable", capability)
                self.assertIn("saveable", capability)
                self.assertIn("activation_ready", capability)
                self.assertIn("reason", capability)
                self.assertIn("managed_identity", capability)
                self.assertRegex(component["browser_runtime"]["digest"], r"^[0-9a-f]{64}$")

        unified = self.client.get("/api/v1/components").get_json()["components"]
        self.assertTrue(all("browser_capabilities" in item for item in unified))

    def test_same_document_validates_for_import_save_but_activation_is_guarded(self) -> None:
        imported = self.client.post(
            "/api/v1/composer/presets/validate", json=self.document
        )
        self.assertEqual(imported.status_code, 200, imported.get_json())
        self.assertEqual(imported.get_json()["kind"], "browser_scene")
        self.assertEqual(
            imported.get_json()["draft"]["browser_scene"], self.document
        )
        self.assertEqual(self.channel.commands, [])

        saved = self.client.post("/api/v1/scene-presets", json={
            "name": "Bound scene",
            "scene": self.document,
        })
        self.assertEqual(saved.status_code, 200, saved.get_json())
        path = self.interface.scene_presets_dir / "bound_scene.json"
        self.assertEqual(json.loads(path.read_text())["scene"], self.document)
        self.assertEqual(self.channel.commands, [])

        validated = self.client.post(
            "/api/v1/scene/validate", json=self.document
        )
        self.assertEqual(validated.status_code, 200, validated.get_json())
        self.assertEqual(validated.get_json()["scene"]["revision"], 17)
        self.assertEqual(self.channel.commands, [])

        activated = self.client.put("/api/v1/scene", json=self.document)
        self.assertEqual(activated.status_code, 503, activated.get_json())
        self.assertEqual(activated.get_json()["code"], "activation_unavailable")
        self.assertEqual(self.channel.commands, [])

    def test_digest_mismatch_and_oversized_import_fail_before_mutation(self) -> None:
        candidate = deepcopy(self.document)
        candidate["background"]["runtime_digest"] = "f" * 64
        invalid = self.client.post("/api/v1/scene/validate", json=candidate)
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("background.runtime_digest", invalid.get_json()["error"])
        self.assertEqual(self.channel.commands, [])

        oversized = self.client.post(
            "/api/v1/composer/presets/validate",
            data=json.dumps({"payload": "x" * BROWSER_SCENE_MAX_BYTES}),
            content_type="application/json",
        )
        self.assertEqual(oversized.status_code, 400)
        self.assertIn("byte limit", oversized.get_json()["error"])
        self.assertEqual(self.channel.commands, [])


if __name__ == "__main__":
    unittest.main()
