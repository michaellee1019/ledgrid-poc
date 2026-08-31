"""Composer Check contracts for the first closed Scene-v1 activation slice."""

from __future__ import annotations

import unittest

from animation.core.component_catalog import ComponentCatalog, ComponentDescriptor
from ipc.scene_contract import SceneContractError, normalize_composer_scene
from web.activation_token_store import ActivationTokenStore


def _catalog() -> ComponentCatalog:
    return ComponentCatalog([
        ComponentDescriptor(
            component_id="aurora", version=1, provider="python", role="background",
            intensity_parameter="glow_intensity",
        ),
        ComponentDescriptor(
            component_id="native-aurora", version=2, provider="receiver_native",
            role="background", defaults={"bundle_digest": "a" * 64, "gain": 0.5},
        ),
        ComponentDescriptor(
            component_id="clock", version=1, provider="python", role="overlay",
            defaults={"show_seconds": True},
        ),
        ComponentDescriptor(
            component_id="alert", version=1, provider="python", role="overlay",
        ),
    ])


def _request(**changes: object) -> dict:
    request = {
        "origin": "composer",
        "scene": {
            "schema": "ledgrid.scene.v1",
            "background": {
                "component_id": "aurora", "version": 1, "provider": "python",
                "role": "background", "parameters": {"glow_intensity": 0.75},
            },
            "vibe": "quiet",
            "master_brightness": 0.5,
        },
    }
    request.update(changes)
    return request


def _overlay(slot_id: str, component_id: str = "clock", **changes: object) -> dict:
    overlay = {
        "slot_id": slot_id,
        "component": {
            "component_id": component_id, "version": 1, "provider": "python",
            "role": "overlay", "parameters": {},
        },
        "enabled": True,
        "opacity": 192,
        "placement": {
            "strip_translation": 1, "led_translation": -2,
            "clip_policy": "clip_to_wall",
        },
        "stale_policy": {"policy": "clear_after_lease", "lease_ms": 1200},
    }
    overlay.update(changes)
    return overlay


def _full_request(**changes: object) -> dict:
    scene = {**_request()["scene"], "overlays": [_overlay("clock"), _overlay("alert", "alert", enabled=False)]}
    scene.update(changes)
    return _request(scene=scene)


class SceneActivationContractTests(unittest.TestCase):
    def test_sole_composer_request_has_closed_canonical_bytes_digest_and_revision(self) -> None:
        canonical = normalize_composer_scene(_request(), _catalog())
        reordered = _request(scene={
            "master_brightness": 0.5,
            "background": _request()["scene"]["background"],
            "schema": "ledgrid.scene.v1", "vibe": "quiet",
        })
        equivalent = normalize_composer_scene(reordered, _catalog())

        self.assertEqual(canonical.canonical_bytes, equivalent.canonical_bytes)
        self.assertEqual(canonical.identity, equivalent.identity)
        self.assertEqual(canonical.identity.revision, 1)
        self.assertRegex(canonical.identity.digest, r"^[0-9a-f]{64}$")
        self.assertEqual(canonical.scene["background"]["provider"], "python")

    def test_malformed_unknown_and_non_composer_inputs_fail_before_check_storage(self) -> None:
        store = ActivationTokenStore()
        invalid = (
            _request(origin="dashboard"),
            _request(unexpected=True),
            _request(scene={**_request()["scene"], "background": {
                **_request()["scene"]["background"], "component_id": "unknown",
            }}),
        )
        for request in invalid:
            with self.subTest(request=request):
                with self.assertRaises(SceneContractError):
                    store.check(request, _catalog())
        self.assertEqual(store._records, {})

    def test_full_scene_canonical_identity_includes_ordered_slots_and_resolved_values(self) -> None:
        first = normalize_composer_scene(_full_request(), _catalog())
        reversed_order = _full_request(overlays=[_overlay("alert", "alert", enabled=False), _overlay("clock")])
        second = normalize_composer_scene(reversed_order, _catalog())
        equivalent = normalize_composer_scene(_full_request(), _catalog())

        self.assertEqual(first.canonical_bytes, equivalent.canonical_bytes)
        self.assertEqual(first.identity, equivalent.identity)
        self.assertNotEqual(first.canonical_bytes, second.canonical_bytes)
        self.assertNotEqual(first.identity.digest, second.identity.digest)
        self.assertEqual(first.scene["background"]["slot_id"], "background")
        self.assertEqual([item["slot_id"] for item in first.scene["overlays"]], ["clock", "alert"])
        self.assertEqual(first.scene["overlays"][0]["component"]["parameters"], {"show_seconds": True})
        self.assertEqual(first.scene["overlays"][0]["placement"]["led_translation"], -2)
        self.assertEqual(first.scene["overlays"][0]["stale_policy"], {"policy": "clear_after_lease", "lease_ms": 1200})

    def test_full_scene_accepts_catalog_bound_native_background_and_rejects_malformed_inputs_before_storage(self) -> None:
        native_background = {
            "component_id": "native-aurora", "version": 2, "provider": "receiver_native",
            "role": "background", "parameters": {"gain": 0.75}, "bundle_digest": "a" * 64,
        }
        native = normalize_composer_scene(_full_request(background=native_background, overlays=[]), _catalog())
        self.assertEqual(native.scene["background"]["bundle_digest"], "a" * 64)
        self.assertEqual(native.scene["background"]["parameters"], {"gain": 0.75})

        store = ActivationTokenStore()
        invalid = (
            _full_request(overlays=[_overlay("clock"), _overlay("clock", "alert")]),
            _full_request(overlays=[_overlay("clock"), _overlay("alert", "alert"), _overlay("third", "clock")]),
            _full_request(overlays=[_overlay("clock", component={
                "component_id": "clock", "version": 1, "provider": "receiver_native",
                "role": "overlay", "parameters": {},
            })]),
            _full_request(overlays=[_overlay("clock", placement={
                "strip_translation": 0, "led_translation": 0, "clip_policy": "wrap",
            })]),
            _full_request(overlays=[_overlay("clock", opacity=True)]),
            _full_request(overlays=[_overlay("clock", stale_policy={"policy": "hold", "lease_ms": 1})]),
            _full_request(background={**native_background, "bundle_digest": "b" * 64}, overlays=[]),
            _full_request(background={**_request()["scene"]["background"], "role": "overlay"}),
        )
        for request in invalid:
            with self.subTest(request=request):
                with self.assertRaises(SceneContractError):
                    store.check(request, _catalog())
        self.assertEqual(store._records, {})


if __name__ == "__main__":
    unittest.main()
