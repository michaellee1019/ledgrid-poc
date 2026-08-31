"""Focused acceptance tests for the current-only Scene v2 identity boundary."""

from __future__ import annotations

import copy
import unittest

from animation.core.component_catalog import ComponentCatalog, ComponentDescriptor
from ipc.scene_contract import (
    SCENE_V2_REVISION,
    SceneContractError,
    build_scene_activation_command,
    normalize_composer_scene,
)


def _catalog() -> ComponentCatalog:
    return ComponentCatalog([
        ComponentDescriptor(
            component_id="native-aurora", version=1, provider="receiver_native", role="background",
            timing_policy="scaled_context", alpha_behavior="none", palette_policy="semantic",
            plant_capabilities=("final_optics",), fidelity_exceptions=(),
            defaults={"bundle_digest": "a" * 64, "gain": 0.5},
        ),
        ComponentDescriptor(
            component_id="aurora", version=1, provider="python", role="animation",
            timing_policy="scaled_context", alpha_behavior="premultiplied_rgba", palette_policy="semantic",
            plant_capabilities=("effect_intent", "simulation_inputs"), fidelity_exceptions=(),
            optional_simulation_inputs=("foliage_density",), defaults={"seed": 17},
        ),
        ComponentDescriptor(
            component_id="opaque-film", version=1, provider="python", role="animation",
            timing_policy="scaled_context", alpha_behavior="opaque", palette_policy="preserve",
            plant_capabilities=("none",), fidelity_exceptions=("authored_media_color",),
        ),
        ComponentDescriptor(
            component_id="clock", version=1, provider="python", role="widget",
            timing_policy="wall_clock", alpha_behavior="premultiplied_rgba", palette_policy="semantic",
            plant_capabilities=("none",), fidelity_exceptions=(), defaults={"show_seconds": True},
        ),
    ])


def _scene(**changes: object) -> dict:
    scene = {
        "schema": "ledgrid.scene.v2",
        "background": {
            "component_id": "native-aurora", "version": 1, "provider": "receiver_native",
            "role": "background", "parameters": {"gain": 0.75}, "bundle_digest": "a" * 64,
        },
        "animation": {
            "component_id": "aurora", "version": 1, "provider": "python",
            "role": "animation", "parameters": {},
        },
        "widgets": [{
            "id": "clock-upper", "component": {
                "component_id": "clock", "version": 1, "provider": "python",
                "role": "widget", "parameters": {},
            }, "visible": True, "placement": {"mode": "auto"},
        }],
        "plants": {"effects": {
            "version": 1, "active": ["illuminate"], "strengths": {"illuminate": 0.4},
        }},
        "look": {"palette_id": "mist", "pace": 0.7, "presentation_brightness": 0.82},
    }
    scene.update(changes)
    return scene


def _request(**changes: object) -> dict:
    request = {"origin": "composer", "scene": _scene()}
    request.update(changes)
    return request


class SceneV2ContractTests(unittest.TestCase):
    def test_canonical_identity_is_stable_for_whole_scene(self) -> None:
        first = normalize_composer_scene(_request(), _catalog())
        reordered = _request(scene={
            "look": _scene()["look"], "plants": _scene()["plants"], "widgets": _scene()["widgets"],
            "animation": _scene()["animation"], "background": _scene()["background"], "schema": "ledgrid.scene.v2",
        })
        second = normalize_composer_scene(reordered, _catalog())

        self.assertEqual(first.canonical_bytes, second.canonical_bytes)
        self.assertEqual(first.identity, second.identity)
        self.assertEqual(first.identity.revision, SCENE_V2_REVISION)
        self.assertEqual(first.scene["animation"]["parameters"], {"seed": 17})
        self.assertNotIn("output_power", first.scene)
        self.assertNotIn("calibration", first.scene["plants"])
        self.assertEqual(build_scene_activation_command(first)["scene"], first.scene)

    def test_alpha_and_opaque_animations_are_both_valid(self) -> None:
        transparent = normalize_composer_scene(_request(), _catalog())
        opaque = normalize_composer_scene(_request(scene=_scene(animation={
            "component_id": "opaque-film", "version": 1, "provider": "python",
            "role": "animation", "parameters": {},
        })), _catalog())
        self.assertEqual(transparent.scene["animation"]["component_id"], "aurora")
        self.assertEqual(opaque.scene["animation"]["component_id"], "opaque-film")

    def test_widgets_keep_author_order_identity_visibility_and_placement(self) -> None:
        scene = _scene(widgets=[
            _scene()["widgets"][0],
            {"id": "clock-lower", "component": {
                "component_id": "clock", "version": 1, "provider": "python", "role": "widget", "parameters": {},
            }, "visible": False, "placement": {"mode": "manual", "strip_translation": 2, "led_translation": -8}},
        ])
        first = normalize_composer_scene(_request(scene=scene), _catalog())
        second_scene = copy.deepcopy(scene)
        second_scene["widgets"].reverse()
        second = normalize_composer_scene(_request(scene=second_scene), _catalog())
        self.assertEqual([item["id"] for item in first.scene["widgets"]], ["clock-upper", "clock-lower"])
        self.assertFalse(first.scene["widgets"][1]["visible"])
        self.assertNotEqual(first.identity.digest, second.identity.digest)

    def test_legacy_roles_and_installation_or_output_data_reject_without_mutation(self) -> None:
        request = _request()
        original = copy.deepcopy(request)
        invalid = (
            _request(scene={**_scene(), "schema": "ledgrid.scene.v1"}),
            _request(scene={**_scene(), "overlays": []}),
            _request(scene={**_scene(), "output_power": True}),
            _request(scene={**_scene(), "plants": {"effects": {"geometry": {}}}}),
            _request(scene={**_scene(), "plants": {"effects": {"active": ["illuminate"], "strengths": {"illuminate": 2}}}}),
            _request(scene={**_scene(), "background": {**_scene()["background"], "role": "animation"}}),
            _request(scene={**_scene(), "widgets": [_scene()["widgets"][0], _scene()["widgets"][0]]}),
        )
        for candidate in invalid:
            with self.subTest(candidate=candidate):
                with self.assertRaises(SceneContractError):
                    normalize_composer_scene(candidate, _catalog())
        with self.assertRaises(SceneContractError):
            normalize_composer_scene({**request, "scene": {**request["scene"], "vibe": "quiet"}}, _catalog())
        self.assertEqual(request, original)

    def test_plants_store_only_bounded_effect_intent_not_installation_geometry(self) -> None:
        for field in ("calibration", "homography", "mask", "geometry", "globe_coordinates"):
            with self.subTest(field=field), self.assertRaises(SceneContractError):
                normalize_composer_scene(
                    _request(scene=_scene(plants={"effects": {field: {}}})), _catalog()
                )
        canonical = normalize_composer_scene(
            _request(scene=_scene(plants={"effects": {
                "active": ["obstacle", "illuminate"],
                "strengths": {"obstacle": 1.0, "illuminate": 0.25},
            }})), _catalog()
        )
        self.assertEqual(canonical.scene["plants"], {"effects": {
            "version": 1, "active": ["illuminate", "obstacle"],
            "strengths": {"illuminate": 0.25, "obstacle": 1.0},
        }})

    def test_capability_declarations_are_required_and_role_bound(self) -> None:
        with self.assertRaises(TypeError):
            ComponentDescriptor(component_id="missing", version=1)  # type: ignore[call-arg]
        with self.assertRaisesRegex(ValueError, "Background"):
            ComponentDescriptor(
                component_id="bad-background", version=1, provider="python", role="background",
                timing_policy="scaled_context", alpha_behavior="none", palette_policy="semantic",
                plant_capabilities=("none",), fidelity_exceptions=(),
            )
        with self.assertRaisesRegex(ValueError, "fidelity exception"):
            ComponentDescriptor(
                component_id="bad-media", version=1, provider="python", role="animation",
                timing_policy="scaled_context", alpha_behavior="opaque", palette_policy="preserve",
                plant_capabilities=("none",), fidelity_exceptions=(),
            )


if __name__ == "__main__":
    unittest.main()
