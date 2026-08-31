"""Composer Check contracts for the first closed Scene-v1 activation slice."""

from __future__ import annotations

import unittest

from animation.core.component_catalog import ComponentCatalog, ComponentDescriptor
from ipc.scene_contract import SceneContractError, normalize_composer_scene
from web.activation_token_store import ActivationTokenStore


def _catalog() -> ComponentCatalog:
    return ComponentCatalog([ComponentDescriptor(
        component_id="aurora", version=1, provider="python", role="background",
        intensity_parameter="glow_intensity",
    )])


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
            _request(scene={**_request()["scene"], "overlays": []}),
            _request(scene={**_request()["scene"], "background": {
                **_request()["scene"]["background"], "component_id": "unknown",
            }}),
        )
        for request in invalid:
            with self.subTest(request=request):
                with self.assertRaises(SceneContractError):
                    store.check(request, _catalog())
        self.assertEqual(store._records, {})


if __name__ == "__main__":
    unittest.main()
