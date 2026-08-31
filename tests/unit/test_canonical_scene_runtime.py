"""33x138 golden contracts for the current-only Scene v2 compositor."""

from __future__ import annotations

import unittest

import numpy as np

from animation.core.component_catalog import ComponentCatalog, ComponentDescriptor
from animation.core.base import RenderedFrame
from animation.core.compositing import BaseFrame, OverlayFrame
from animation.core.manager import PreviewLEDController
from animation.core.scene_runtime import CanonicalSceneRuntime, CanonicalSceneRuntimeError
from ipc.scene_contract import SCENE_V2_SCHEMA, normalize_composer_scene


def _catalog() -> ComponentCatalog:
    return ComponentCatalog([
        ComponentDescriptor("native", 1, "receiver_native", "background", "scaled_context", "none", "semantic", ("final_optics",), (), defaults={"bundle_digest": "a" * 64}),
        ComponentDescriptor("alpha", 1, "python", "animation", "scaled_context", "premultiplied_rgba", "semantic", ("simulation_inputs",), (), optional_simulation_inputs=("foliage_density",)),
        ComponentDescriptor("required", 1, "python", "animation", "scaled_context", "premultiplied_rgba", "semantic", ("simulation_inputs",), (), required_simulation_inputs=("globe_proximity",)),
        ComponentDescriptor("opaque", 1, "python", "animation", "scaled_context", "opaque", "preserve", ("none",), ("authored_media_color",)),
        ComponentDescriptor("widget", 1, "python", "widget", "wall_clock", "premultiplied_rgba", "semantic", ("none",), ()),
    ])


def _component(component_id: str, role: str, **parameters: object) -> dict:
    return {"component_id": component_id, "version": 1, "provider": "receiver_native" if role == "background" else "python", "role": role, "parameters": parameters, **({"bundle_digest": "a" * 64} if role == "background" else {})}


def _scene(*, animation: str = "alpha", widgets: list[dict] | None = None, brightness: float = 1.0) -> dict:
    return {
        "schema": SCENE_V2_SCHEMA,
        "background": _component("native", "background"),
        "animation": _component(animation, "animation"),
        "widgets": widgets or [],
        "plants": {"effects": {"version": 1, "active": [], "strengths": {}}},
        "look": {"palette_id": "mist", "pace": 2.0, "presentation_brightness": brightness},
    }


def _widget(widget_id: str, *, visible: bool = True, led: int = 0) -> dict:
    return {"id": widget_id, "component": _component("widget", "widget"), "visible": visible,
            "placement": {"mode": "manual", "strip_translation": 0, "led_translation": led}}


def _canonical(**changes: object):
    scene = _scene(**changes)
    return normalize_composer_scene({"origin": "composer", "scene": scene}, _catalog())


class _Plane:
    def __init__(self, pixels: np.ndarray, *, changed: bool = False):
        self.pixels = pixels
        self.changed = changed
        self.calls: list[float] = []

    def generate_frame(self, elapsed: float, count: int):
        self.calls.append(elapsed)
        return OverlayFrame(self.pixels, revision=1, changed=self.changed, dirty_ranges=())


class _OpaquePlane:
    def __init__(self, pixels: np.ndarray):
        self.pixels = pixels
        self.calls: list[float] = []

    def generate_frame(self, elapsed: float, count: int):
        self.calls.append(elapsed)
        return self.pixels


class _CachedOpaquePlane(_OpaquePlane):
    def generate_frame(self, elapsed: float, count: int):
        self.calls.append(elapsed)
        return RenderedFrame(self.pixels, changed=False, dirty_ranges=())


class SceneV2RuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)
        self.total = 33 * 138
        self.instances: dict[str, list[object]] = {"alpha": [], "required": [], "opaque": [], "widget": []}
        self.alpha = np.zeros((self.total, 4), dtype=np.uint8)
        self.alpha[1] = (100, 0, 0, 128)
        self.opaque = np.full((self.total, 3), (7, 8, 9), dtype=np.uint8)
        self.widget_pixels = np.zeros((self.total, 4), dtype=np.uint8)
        self.widget_pixels[1] = (0, 0, 255, 255)

        def factory(descriptor, controller, parameters):
            del controller, parameters
            if descriptor.component_id == "opaque":
                result = _OpaquePlane(self.opaque)
            elif descriptor.component_id == "widget":
                pixels = self.widget_pixels.copy()
                if len(self.instances["widget"]) % 2:
                    pixels[1] = (0, 255, 0, 255)
                result = _Plane(pixels)
            else:
                result = _Plane(self.alpha)
            self.instances[descriptor.component_id].append(result)
            return result

        self.background = np.full((self.total, 3), (10, 20, 30), dtype=np.uint8)
        self.runtime = CanonicalSceneRuntime(
            self.controller, _catalog(), animation_factory=factory, widget_factory=factory,
            background_renderer=lambda context, count: BaseFrame(self.background, changed=False),
            plant_input_resolver=lambda plants, descriptor: {"foliage_density": 0.6},
        )

    def test_schema_sentinel_and_transparent_reveal_at_real_wall_geometry(self) -> None:
        canonical = _canonical()
        self.assertEqual(canonical.scene["schema"], "ledgrid.scene.v2")
        self.runtime.activate(canonical)
        frame = self.runtime.render(1.0)
        self.assertEqual(frame.pixels.shape, (self.total, 3))
        np.testing.assert_array_equal(frame.pixels[0], (10, 20, 30))
        np.testing.assert_array_equal(frame.pixels[1], (105, 10, 15))
        self.assertEqual(self.instances["alpha"][0].calls, [2.0])
        self.assertEqual(frame.foreground.pixels.shape, (self.total, 4))

    def test_opaque_animation_covers_native_background(self) -> None:
        self.runtime.activate(_canonical(animation="opaque"))
        frame = self.runtime.render(1.0)
        np.testing.assert_array_equal(frame.pixels[0], (7, 8, 9))
        self.assertTrue(np.all(frame.foreground.pixels[:, 3] == 255))

    def test_cached_opaque_rendered_frame_does_not_retransmit_foreground(self) -> None:
        runtime = CanonicalSceneRuntime(
            self.controller, _catalog(), animation_factory=lambda *args: _CachedOpaquePlane(self.opaque),
            background_renderer=lambda context, count: BaseFrame(self.background, changed=False),
        )
        runtime.activate(_canonical(animation="opaque"))
        runtime.render(1.0)
        stable = runtime.render(2.0)
        self.assertFalse(stable.changed)
        self.assertFalse(stable.foreground.changed)
        self.assertEqual(stable.foreground.dirty_ranges, ())

    def test_widgets_are_stable_ordered_and_wall_clock_cadenced(self) -> None:
        first = _canonical(widgets=[_widget("one"), _widget("two")])
        self.runtime.activate(first)
        ordered = self.runtime.render(1.0).pixels.copy()
        one, two = self.instances["widget"]
        self.runtime.activate(_canonical(widgets=[_widget("two"), _widget("one")]))
        reversed_frame = self.runtime.render(5.0).pixels.copy()
        self.assertIs(self.runtime._widgets["one"].instance, one)
        self.assertIs(self.runtime._widgets["two"].instance, two)
        self.assertEqual(one.calls, [0.0, 0.0])
        self.assertFalse(np.array_equal(ordered, reversed_frame))

    def test_move_remove_and_sparse_foreground_are_stale_free(self) -> None:
        self.widget_pixels[0] = (255, 0, 0, 255)
        self.runtime.activate(_canonical(widgets=[_widget("clock", led=0)]))
        self.runtime.render(1.0)
        self.runtime.activate(_canonical(widgets=[_widget("clock", led=1)]))
        moved = self.runtime.render(1.0)
        np.testing.assert_array_equal(moved.pixels[:2], ((10, 20, 30), (255, 0, 0)))
        self.assertEqual(moved.dirty_ranges, ((0, 3),))
        self.runtime.activate(_canonical(widgets=[]))
        removed = self.runtime.render(1.0)
        np.testing.assert_array_equal(removed.pixels[0], (10, 20, 30))
        np.testing.assert_array_equal(removed.pixels[1], (105, 10, 15))
        self.assertEqual(removed.foreground.pixels[0].tobytes(), b"\x00\x00\x00\x00")
        self.assertTrue(removed.foreground.changed)
        self.assertEqual(removed.foreground.dirty_ranges, ((1, 3),))
        stable = self.runtime.render(1.0)
        self.assertFalse(stable.changed)
        self.assertFalse(stable.foreground.changed)
        self.assertEqual(stable.foreground.dirty_ranges, ())

    def test_plant_look_and_output_stages_apply_once_each(self) -> None:
        calls: list[tuple[int, int, int]] = []

        def optics(pixels, plants):
            del plants
            calls.append(tuple(int(value) for value in pixels[0]))
            result = pixels.copy()
            result[0] = (100, 100, 100)
            return result

        runtime = CanonicalSceneRuntime(
            self.controller, _catalog(), animation_factory=lambda *args: _OpaquePlane(self.opaque),
            background_renderer=lambda context, count: BaseFrame(self.background, changed=False),
            plant_optics=optics, master_brightness=0.5,
        )
        runtime.activate(_canonical(animation="opaque", brightness=0.5))
        frame = runtime.render(1.0)
        self.assertEqual(calls, [(7, 8, 9)])
        np.testing.assert_array_equal(frame.pixels[0], (25, 25, 25))
        self.assertEqual(frame.stage_trace[-3:], ("plant_optics", "look_presentation", "output_master_brightness"))

    def test_invalid_basis_does_not_replace_last_valid_scene(self) -> None:
        canonical = _canonical()
        self.runtime.activate(canonical)
        bad = type(canonical)(canonical.scene, b"{}", canonical.identity)
        with self.assertRaisesRegex(CanonicalSceneRuntimeError, "bytes"):
            self.runtime.activate(bad)
        self.assertEqual(self.runtime.desired_identity, canonical.identity)

    def test_required_plant_inputs_reject_instead_of_defaulting_neutral(self) -> None:
        self.runtime.activate(_canonical(animation="required"))
        with self.assertRaisesRegex(CanonicalSceneRuntimeError, "required plant simulation input 'globe_proximity' is missing"):
            self.runtime.render(1.0)

    def test_failed_widget_preparation_does_not_mutate_active_animation(self) -> None:
        def factory(descriptor, controller, parameters):
            del controller
            if descriptor.component_id == "widget" and parameters.get("reject"):
                raise RuntimeError("widget factory rejected candidate")
            pixels = np.zeros((self.total, 4), dtype=np.uint8)
            pixels[0] = (int(parameters.get("tone", 20)), 0, 0, 255)
            return _Plane(pixels)

        runtime = CanonicalSceneRuntime(
            self.controller, _catalog(), animation_factory=factory, widget_factory=factory,
            background_renderer=lambda context, count: BaseFrame(self.background, changed=False),
        )
        initial_scene = _scene()
        initial_scene["animation"] = _component("alpha", "animation", tone=20)
        initial = normalize_composer_scene({"origin": "composer", "scene": initial_scene}, _catalog())
        runtime.activate(initial)
        before = runtime.render(1.0).pixels.copy()

        rejected_scene = _scene(widgets=[_widget("bad")])
        rejected_scene["animation"] = _component("alpha", "animation", tone=200)
        rejected_scene["widgets"][0]["component"] = _component("widget", "widget", reject=True)
        rejected = normalize_composer_scene({"origin": "composer", "scene": rejected_scene}, _catalog())
        with self.assertRaisesRegex(RuntimeError, "widget factory rejected"):
            runtime.activate(rejected)
        self.assertEqual(runtime.desired_identity, initial.identity)
        np.testing.assert_array_equal(runtime.render(1.0).pixels, before)


if __name__ == "__main__":
    unittest.main()
