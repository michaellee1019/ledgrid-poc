"""Focused Scene v2, thermal-physics, and interaction proof for Lava Lamp."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

import numpy as np

from animation.core.manager import AnimationManager, PreviewLEDController
from animation.core.presentation_contracts import (
    ComponentProvider,
    ComponentRef,
    ResolvedScene,
    SceneState,
)
from animation.core.plant_awareness import INSTALLATION_GEOMETRY_CONTACT_INPUT
from animation.plugins.lava_lamp import LavaLampAnimation
from tests.unit.test_composer_slice import _PreviewManager, _WallChannel, _current_scene
from web.app import AnimationWebInterface
from web.composer_final_preview import ComposerFinalPreview, current_component_catalog
from web.local_control import LocalControlChannel

ROOT = Path(__file__).resolve().parents[2]
PRESET_IDS = ["bowl-bumpers", "bowl-emitter", "busy-bubbles", "classic-amber", "cotton-candy", "foliage-refraction", "habitat-pools", "night", "ocean-blue", "quiet", "ruby-vintage", "seven-bowl-portals", "showcase", "slow-giants", "solar-flare", "stormy-wax", "toxic-lime", "violet-glass"]


class LavaLampSceneV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)

    @staticmethod
    def _scene() -> dict:
        scene = _current_scene()
        scene["animation"] = {"component_id": "lava_lamp", "version": 1, "provider": "python", "role": "animation", "parameters": {}}
        return scene

    def _resolved(self, effect: str | None = None, strength: float = 1.0) -> ResolvedScene:
        descriptor = LavaLampAnimation.component_descriptor()
        contact = ComposerFinalPreview(
            current_component_catalog(), ROOT,
        )._plant_inputs({}, descriptor)[INSTALLATION_GEOMETRY_CONTACT_INPUT]
        effects = {"version": 1, "active": [], "strengths": {}}
        if effect is not None:
            effects = {"version": 1, "active": [effect], "strengths": {effect: strength}}
        return ResolvedScene(
            canonical_scene={"plants": {"effects": effects}}, canonical_bytes=b"lava",
            digest="lava", descriptor=descriptor, parameters=dict(LavaLampAnimation.DEFAULTS),
            palette={"palette_id": "neutral"}, phase_time=0.0, plant_inputs={},
            installation_geometry=contact,
        )

    def test_descriptor_and_parameters_are_opaque_semantic_and_local(self) -> None:
        descriptor = LavaLampAnimation.component_descriptor()
        self.assertEqual((descriptor.component_id, descriptor.role.value, descriptor.alpha_behavior.value, descriptor.palette_policy.value), ("lava_lamp", "animation", "opaque", "semantic"))
        self.assertSetEqual(
            set(LavaLampAnimation.PLANT_MODIFIER_SUPPORT),
            {"refract", "bumper", "emitter", "habitat", "portal"},
        )
        self.assertTrue(descriptor.accepts_installation_geometry_contact)
        self.assertNotIn("brightness", LavaLampAnimation.DEFAULTS)
        with self.assertRaisesRegex(ValueError, "non-local parameters"):
            LavaLampAnimation._normalized_parameters({"brightness": .5})

    def test_bounded_thermal_cycle_and_stir_keep_wax_area_stable(self) -> None:
        lamp = LavaLampAnimation(self.controller, {"blob_count": 5, "seed": 77})
        initial_area = lamp.wax_area
        target = int(np.flatnonzero(lamp.active)[0])
        velocity = (float(lamp.vx[target]), float(lamp.vy[target]))
        self.assertTrue(lamp.handle_interaction("primary", float(lamp.x[target]), float(lamp.y[target]), 1.0))
        self.assertFalse(lamp.handle_interaction("secondary", 8.0, 80.0, 1.0))
        for _ in range(900): lamp._step(.01)
        stats = lamp.get_runtime_stats()
        self.assertNotEqual(velocity, (float(lamp.vx[target]), float(lamp.vy[target])))
        self.assertGreaterEqual(stats["interactions_applied"], 1)
        self.assertLessEqual(stats["blob_count"], lamp.MAX_BLOBS)
        self.assertAlmostEqual(lamp.wax_area, initial_area, places=3)

    def test_scene_timing_and_palette_are_deterministic_at_33_by_138(self) -> None:
        left = LavaLampAnimation(self.controller, {"seed": 314, "heat": .85})
        right = LavaLampAnimation(self.controller, {"seed": 314, "heat": .85})
        left_frames = [left.generate_frame(time, index).pixels.copy() for index, time in enumerate((0., .01, .5, 1., 3.))]
        right_frames = [right.generate_frame(time, index).pixels.copy() for index, time in enumerate((0., .01, .5, 1., 3.))]
        for expected, actual in zip(left_frames, right_frames): np.testing.assert_array_equal(expected, actual)
        self.assertEqual(left_frames[-1].shape, (33 * 138, 3))
        self.assertLessEqual(left.cadence_snapshot()["simulation_hz"], 100.0)

    def test_strict_live_manager_accepts_and_renders_lava_lamp(self) -> None:
        manager = AnimationManager(self.controller, auto_start=False)
        manager._launch_animation_loop = lambda: None
        component = ComponentRef(
            plugin_id="lava_lamp",
            provider=ComponentProvider.PYTHON,
            resolved_parameters=dict(LavaLampAnimation.DEFAULTS),
        )
        try:
            self.assertTrue(manager.start_scene(SceneState(
                revision=1,
                background=component,
                overlays=(),
                known_python_fallback=component,
            )))
            self.assertEqual(
                manager.render_composed_scene_frame().pixels.shape,
                (33 * 138, 3),
            )
        finally:
            manager.stop_animation(clear_leds=False)

    def test_all_eighteen_presets_are_tracked_local_and_visually_distinct(self) -> None:
        paths = sorted((ROOT / "animation/plugins/lava_lamp/presets").glob("*.json"))
        self.assertEqual([path.stem for path in paths], PRESET_IDS)
        frames = []
        for path in paths:
            payload = json.loads(path.read_text(encoding="utf-8"))
            parameters = LavaLampAnimation._normalized_parameters(payload["params"])
            self.assertSetEqual(set(parameters), set(LavaLampAnimation.DEFAULTS))
            lamp = LavaLampAnimation(self.controller, parameters)
            for index in range(51): rendered = lamp.generate_frame(index / 100.0, index)
            frames.append(rendered.pixels.copy())
        self.assertEqual(len({frame.tobytes() for frame in frames}), 18)

    def test_catalog_preview_live_and_recovery_keep_the_same_lamp_scene(self) -> None:
        interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
        client = interface.app.test_client()
        self.assertEqual([choice["preset_id"] for choice in interface.composer_presets.choices("lava_lamp")], PRESET_IDS)
        scene = interface.composer_presets.apply(self._scene(), "stormy-wax")
        self.assertNotIn("brightness", scene["animation"]["parameters"])
        self.assertEqual(client.get("/api/composer/components/lava_lamp/presets").status_code, 200)
        self.assertEqual(client.post("/api/composer/preview", json={"origin": "composer", "scene": scene, "preview": {"monotonic_elapsed": 2.0, "wall_time": "2026-08-31T12:00:00+00:00"}}).status_code, 200)
        self.assertEqual(client.post("/api/composer/scene", json={"origin": "composer", "scene": scene, "client_id": "lava", "client_sequence": 1}).get_json()["state"], "live")
        self.assertEqual(client.post("/api/composer/stop", json={"client_id": "lava"}).get_json()["status"]["state"], "stopped")
        edited = copy.deepcopy(scene); edited["animation"]["parameters"]["heat"] = .33
        self.assertTrue(client.post("/api/composer/scene", json={"origin": "composer", "scene": edited, "client_id": "lava", "client_sequence": 2}).get_json()["published"])
        self.assertEqual(client.get("/api/composer/recovery?client_id=lava").get_json()["recovery"]["scene"]["animation"]["parameters"]["heat"], .33)
        self.assertIs(current_component_catalog().require(provider="python", component_id="lava_lamp", version=1), LavaLampAnimation.component_descriptor())

    def test_control_remix_preserves_hidden_parameters_across_preview_live_and_recovery(self) -> None:
        interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
        client = interface.app.test_client()
        scene = interface.composer_presets.apply(self._scene(), "stormy-wax")
        scene["animation"]["parameters"].update({"interaction_radius": 16.0, "interaction_strength": 2.0})
        preset = next(choice for choice in interface.composer_presets.choices("lava_lamp") if choice["preset_id"] == "quiet")
        self.assertEqual(preset["parameters"]["interaction_radius"], 8.0)
        self.assertEqual(preset["parameters"]["interaction_strength"], 1.0)
        selected = copy.deepcopy(scene)
        selected["animation"]["parameters"] = {
            **preset["parameters"],
            **{name: scene["animation"]["parameters"][name] for name in ("interaction_radius", "interaction_strength")},
        }
        self.assertEqual(selected["animation"]["parameters"]["heat"], preset["parameters"]["heat"])
        self.assertEqual(selected["animation"]["parameters"]["interaction_radius"], 16.0)
        self.assertEqual(selected["animation"]["parameters"]["interaction_strength"], 2.0)
        first_preview = client.post("/api/composer/preview", json={"origin": "composer", "scene": selected, "preview": {"monotonic_elapsed": 2.0, "wall_time": "2026-08-31T12:00:00+00:00"}}).get_json()
        first_live = client.post("/api/composer/scene", json={"origin": "composer", "scene": selected, "client_id": "lava-remix", "client_sequence": 1}).get_json()
        remix = copy.deepcopy(selected); remix["animation"]["parameters"] = {**remix["animation"]["parameters"], "heat": .41}
        remixed_preview = client.post("/api/composer/preview", json={"origin": "composer", "scene": remix, "preview": {"monotonic_elapsed": 2.0, "wall_time": "2026-08-31T12:00:00+00:00"}}).get_json()
        remixed_live = client.post("/api/composer/scene", json={"origin": "composer", "scene": remix, "client_id": "lava-remix", "client_sequence": 2}).get_json()
        recovered = client.get("/api/composer/recovery?client_id=lava-remix").get_json()["recovery"]["scene"]
        self.assertEqual(first_preview["basis"]["digest"], first_live["current"]["digest"])
        self.assertEqual(first_live["current"]["digest"], first_live["desired"]["digest"])
        self.assertEqual(first_live["current"]["digest"], first_live["observed"]["digest"])
        self.assertEqual(remixed_preview["basis"]["digest"], remixed_live["current"]["digest"])
        self.assertEqual(remixed_live["current"]["digest"], remixed_live["desired"]["digest"])
        self.assertEqual(remixed_live["current"]["digest"], remixed_live["observed"]["digest"])
        self.assertEqual(recovered["animation"]["parameters"]["heat"], .41)
        self.assertEqual(recovered["animation"]["parameters"]["interaction_radius"], 16.0)
        self.assertEqual(recovered["animation"]["parameters"]["interaction_strength"], 2.0)

    def test_existing_safe_primary_interaction_seam_stirs_the_live_lamp(self) -> None:
        manager = AnimationManager(self.controller, auto_start=False)
        client = AnimationWebInterface(LocalControlChannel(manager), manager, local_mode=True).app.test_client()
        manager.current_animation = LavaLampAnimation(self.controller); manager.current_animation_name = "lava_lamp"
        self.assertEqual(client.post("/api/interaction", json={"kind": "primary", "x": 8.0, "y": 90.0, "strength": 1.0}).status_code, 200)
        self.assertEqual(client.post("/api/interaction", json={"kind": "secondary", "x": 8.0, "y": 90.0, "strength": 1.0}).status_code, 400)
        self.assertEqual(client.post("/api/interaction", json={"kind": "primary", "x": 32.999, "y": 137.999, "strength": 1.0}).status_code, 200)
        self.assertEqual(client.post("/api/interaction", json={"kind": "primary", "x": 33.0, "y": 20.0, "strength": 1.0}).status_code, 400)
        self.assertEqual(client.post("/api/interaction", json={"kind": "primary", "x": 8.0, "y": 138.0, "strength": 1.0}).status_code, 400)
        self.assertEqual(client.post("/api/interaction", json={"kind": "primary", "x": 8.0, "y": 90.0, "strength": 1.01}).status_code, 400)

    def test_provider_contact_drives_each_installation_story_without_local_geometry(self) -> None:
        stories = {
            "foliage-refraction": "refract", "bowl-bumpers": "bumper",
            "bowl-emitter": "emitter", "habitat-pools": "habitat",
            "seven-bowl-portals": "portal",
        }
        for preset_id, effect in stories.items():
            with self.subTest(effect=effect):
                payload = json.loads((ROOT / "animation/plugins/lava_lamp/presets" / f"{preset_id}.json").read_text())
                self.assertIn(effect, payload["tags"])
                self.assertIn("Scene-owned", payload["description"])
                self.assertFalse({"plant_aware", "plant_modifiers", "calibration", "geometry"} & set(payload["params"]))
                selected = AnimationWebInterface(
                    _WallChannel(), _PreviewManager(), local_mode=True,
                ).composer_presets.apply(self._scene(), preset_id)
                self.assertEqual(
                    selected["plants"]["effects"]["active"], [effect],
                )
                self.assertGreater(selected["plants"]["effects"]["strengths"][effect], 0.0)
                self.assertNotIn("installation_effects", selected["animation"]["parameters"])
                lamp = LavaLampAnimation(self.controller, {"seed": 721})
                lamp.set_presentation_context(self._resolved(effect))
                lamp._step(lamp.PHYSICS_DT)
                self.assertGreater(lamp.get_runtime_stats()["plant_regions"], 0)
                self.assertEqual(lamp._active_modifier(effect), 1.0)
                self.assertNotIn("plant_modifiers", lamp.params)

    @staticmethod
    def _place_in_named_bowl(lamp: LavaLampAnimation, index: int, name: str) -> None:
        x, y = lamp._region_centers[name]
        lamp.x[index], lamp.y[index] = x, y
        lamp.previous_x[index], lamp.previous_y[index] = x, y

    def test_bowl_dynamics_are_bounded_and_follow_the_stable_region_order(self) -> None:
        # One contact can affect only the wax body occupying that exact core.
        bumper = LavaLampAnimation(self.controller, {"seed": 724})
        bumper.set_presentation_context(self._resolved("bumper")); bumper._step(.01)
        first = int(np.flatnonzero(bumper.active)[0]); other = int(np.flatnonzero(bumper.active)[1])
        self._place_in_named_bowl(bumper, first, "top_left")
        bumper.vx[first], bumper.vy[first], bumper.temperature[first] = -2.0, 0.0, .45
        untouched = (float(bumper.temperature[other]), float(bumper.vx[other]), float(bumper.vy[other]))
        bumper._apply_plant_dynamics(first, .01)
        self.assertGreater(bumper.temperature[first], .45)
        self.assertEqual(untouched, (float(bumper.temperature[other]), float(bumper.vx[other]), float(bumper.vy[other])))

        emitter = LavaLampAnimation(self.controller, {"seed": 725})
        emitter.set_presentation_context(self._resolved("emitter")); emitter._step(.01)
        emitter._emitter_clock = -10.0
        emitter._emit_from_bowl()
        self.assertEqual(emitter.get_runtime_stats()["emissions"], 1)
        self.assertEqual(emitter._emitter_region, 1)

        habitat = LavaLampAnimation(self.controller, {"seed": 726})
        habitat.set_presentation_context(self._resolved("habitat")); habitat._step(.01)
        self._place_in_named_bowl(habitat, first, "top_left")
        habitat.temperature[first], habitat.vx[first], habitat.vy[first] = .9, 4.0, -3.0
        habitat._apply_plant_dynamics(first, .1)
        self.assertLess(habitat.temperature[first], .9)
        self.assertLess(abs(habitat.vx[first]), 4.0)

        portal = LavaLampAnimation(self.controller, {"seed": 727})
        portal.set_presentation_context(self._resolved("portal")); portal._step(.01)
        self._place_in_named_bowl(portal, first, "top_left")
        portal.cooldown[first] = 0.0
        portal._apply_plant_dynamics(first, .01)
        self.assertEqual(portal.get_runtime_stats()["portal_transfers"], 1)
        expected = portal._region_centers["top_right"]
        self.assertAlmostEqual(float(portal.x[first]), expected[0])
        self.assertAlmostEqual(float(portal.y[first]), expected[1])

    def test_off_zero_and_live_geometry_refresh_preserve_thermal_state_and_rng(self) -> None:
        lamp = LavaLampAnimation(self.controller, {"seed": 722})
        baseline = LavaLampAnimation(self.controller, {"seed": 722})
        off = self._resolved()
        zero = self._resolved("bumper", 0.0)
        lamp.set_presentation_context(off)
        baseline.set_presentation_context(off)
        self.assertTrue(lamp.generate_frame(0.0, 0).changed)
        self.assertTrue(baseline.generate_frame(0.0, 0).changed)
        lamp.set_presentation_context(zero)
        baseline.set_presentation_context(zero)
        self.assertFalse(lamp.generate_frame(0.0, 1).changed)
        self.assertFalse(baseline.generate_frame(0.0, 1).changed)
        lamp._step(lamp.PHYSICS_DT); baseline._step(baseline.PHYSICS_DT)
        for name in ("x", "y", "vx", "vy", "temperature", "cooldown", "radius"):
            np.testing.assert_array_equal(getattr(lamp, name), getattr(baseline, name))
        self.assertEqual(lamp.rng.bit_generator.state, baseline.rng.bit_generator.state)
        before = (lamp.x.copy(), lamp.y.copy(), lamp.vx.copy(), lamp.vy.copy(), lamp.temperature.copy(), lamp.cooldown.copy(), lamp.simulation_time, lamp._steps, lamp.rng.bit_generator.state)
        lamp.set_presentation_context(self._resolved("portal"))
        after = (lamp.x.copy(), lamp.y.copy(), lamp.vx.copy(), lamp.vy.copy(), lamp.temperature.copy(), lamp.cooldown.copy(), lamp.simulation_time, lamp._steps, lamp.rng.bit_generator.state)
        for expected, actual in zip(before[:-1], after[:-1]):
            if isinstance(expected, np.ndarray): np.testing.assert_array_equal(expected, actual)
            else: self.assertEqual(expected, actual)
        self.assertEqual(before[-1], after[-1])

    def test_same_tick_effect_off_or_zero_rerenders_the_unmodified_cache(self) -> None:
        clean = LavaLampAnimation(self.controller, {"seed": 728})
        active = LavaLampAnimation(self.controller, {"seed": 728})
        clean.set_presentation_context(self._resolved())
        active.set_presentation_context(self._resolved("refract", .8))
        clean_frame = clean.generate_frame(0.0, 0)
        active_frame = active.generate_frame(0.0, 0)
        self.assertNotEqual(clean_frame.pixels.tobytes(), active_frame.pixels.tobytes())
        active.set_presentation_context(self._resolved())
        off_frame = active.generate_frame(0.0, 1)
        self.assertTrue(off_frame.changed)
        np.testing.assert_array_equal(off_frame.pixels, clean_frame.pixels)

        active.set_presentation_context(self._resolved("refract", .8))
        active.generate_frame(.01, 2)
        active.set_presentation_context(self._resolved("refract", 0.0))
        zero_frame = active.generate_frame(.01, 3)
        clean.set_presentation_context(self._resolved())
        clean_at_tick = clean.generate_frame(.01, 3)
        self.assertTrue(zero_frame.changed)
        np.testing.assert_array_equal(zero_frame.pixels, clean_at_tick.pixels)

    def test_refraction_is_presentation_only_and_primary_stir_survives_contact(self) -> None:
        plain = LavaLampAnimation(self.controller, {"seed": 723})
        refracted = LavaLampAnimation(self.controller, {"seed": 723})
        plain.set_presentation_context(self._resolved())
        refracted.set_presentation_context(self._resolved("refract", .8))
        plain._step(.01); refracted._step(.01)
        for name in ("x", "y", "vx", "vy", "temperature", "radius"):
            np.testing.assert_array_equal(getattr(plain, name), getattr(refracted, name))
        self.assertNotEqual(
            plain.generate_frame(.02, 2).pixels.tobytes(),
            refracted.generate_frame(.02, 2).pixels.tobytes(),
        )
        target = int(np.flatnonzero(refracted.active)[0])
        self.assertTrue(refracted.handle_interaction("primary", float(refracted.x[target]), float(refracted.y[target])))
        refracted._step(.01)
        self.assertGreaterEqual(refracted.get_runtime_stats()["interactions_applied"], 1)

    def test_composer_canvas_uses_only_bounded_primary_interaction_for_live_instruments(self) -> None:
        script = (ROOT / "web/static/js/composer_slice.js").read_text(encoding="utf-8")
        self.assertIn("const lavaParameters = (existing = {}) => ({...existing", script)
        self.assertIn("lavaParameters(next.animation.parameters)", script)
        self.assertIn("const lavaInteractionParameters = (parameters = {})", script)
        self.assertIn("next.animation.parameters = {...preset.parameters, ...lavaInteractionParameters(next.animation.parameters)}", script)
        start = script.index("const primaryInstruments = Object.freeze")
        end = script.index("function renderLibrary()", start)
        interaction = script[start:end]
        for token in ("lava_lamp:", "flame_burst:", "fluid_tank:", "componentId", "!trigger", "event.button !== 0", "event.isPrimary === false", "status?.running", "status?.armed", "Math.min(32.999", "Math.min(137.999", "kind: 'primary'", "strength: 1", "body.accepted !== true", "fetch('/api/interaction'"):
            self.assertIn(token, interaction)
        for token in ("preset.installation_effects", "next.plants = {...next.plants, effects: preset.installation_effects}"):
            self.assertIn(token, script)
        self.assertNotIn("/api/preview/lava_lamp/interaction", interaction)


if __name__ == "__main__":
    unittest.main()
