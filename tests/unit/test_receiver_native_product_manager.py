"""Product acceptance for managed receiver-native scene ownership."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
import contextlib
import io
import unittest

import numpy as np
from PIL import Image

from animation.core.feature_flags import AnimationPipelineFeatureFlags
from animation.core.manager import AnimationManager


BUNDLE = "a" * 64
PAYLOAD = "b" * 64
PLUGIN = "aurora_curtains_native"
PARAMETERS = {
    "brightness": 0.42,
    "curtain_width": 7,
    "layers": 3,
    "motion": 0.34,
    "shimmer": True,
}
FLAGS = AnimationPipelineFeatureFlags(
    receiver_local_background=True,
    receiver_sparse_overlay=True,
    receiver_geometry_profile=True,
    receiver_native_modules=True,
)


def _preview() -> bytes:
    frames = [
        Image.new("RGB", (33, 138), color)
        for color in ((12, 34, 56), (78, 90, 123))
    ]
    output = BytesIO()
    frames[0].save(
        output,
        format="WEBP",
        save_all=True,
        append_images=frames[1:],
        duration=[17, 17],
        loop=0,
        lossless=True,
        exact=True,
    )
    return output.getvalue()


class _Library:
    def __init__(self):
        self.resolved = SimpleNamespace(
            bundle_digest=BUNDLE,
            payload_digest=PAYLOAD,
            receipt=SimpleNamespace(
                package_id=PLUGIN,
                bundle_digest=BUNDLE,
                payload_digest=PAYLOAD,
                published_at="2026-08-25T00:00:00Z",
            ),
            verified=SimpleNamespace(
                preview=_preview(),
                manifest={"defaults": PARAMETERS, "preview": {"duration_ms": 17}},
            ),
        )

    def resolve_package(self, package_id, *, bundle_digest=None):
        if package_id != PLUGIN or bundle_digest not in (None, BUNDLE):
            raise ValueError("not published")
        return self.resolved

    def resolve(self, bundle_digest):
        if bundle_digest != BUNDLE:
            raise ValueError("not published")
        return self.resolved


class _Controller:
    debug = False
    inline_show = True

    def __init__(self, *, adopt=True, recover_outcomes=()):
        self.strip_count = 33
        self.leds_per_strip = 138
        self.total_leds = self.strip_count * self.leds_per_strip
        self.current_brightness = 255
        self.num_devices = 5
        self.receiver_strip_counts = (8, 8, 8, 8, 1)
        self.receiver_global_strip_offsets = (0, 8, 16, 24, 32)
        self.operations = []
        self.context = None
        self.session = None
        self.generation = 0
        self.adopt_result = adopt
        self.recover_outcomes = list(recover_outcomes)
        self.native_status = {
            "state": "idle",
            "operation": "initialize",
            "rollout_enabled": True,
        }

    def _active_status(self, operation, resolved):
        return {
            "state": "active",
            "operation": operation,
            "rollout_enabled": True,
            "bundle_digest": resolved.bundle_digest,
            "payload_digest": resolved.payload_digest,
            "capability_report": {
                "devices": [
                    {
                        "logical_device": receiver_id,
                        "local_strip_count": self.receiver_strip_counts[receiver_id],
                        "global_strip_offset": self.receiver_global_strip_offsets[receiver_id],
                    }
                    for receiver_id in range(self.num_devices)
                ]
            },
        }

    def install_native_background(self, resolved, *, retries=3):
        self.operations.append(("install", resolved.bundle_digest, retries))
        return {"state": "installed", "error": None}

    def probe_native_background(self, resolved):
        self.operations.append(("probe", resolved.bundle_digest))
        return {"state": "ready", "bundle_digest": resolved.bundle_digest}

    def clear_native_background_quarantine(self, resolved):
        self.operations.append(("clear_quarantine", resolved.bundle_digest))
        return {
            "state": "ready",
            "operation": "clear_quarantine",
            "bundle_digest": resolved.bundle_digest,
            "payload_digest": resolved.payload_digest,
            "error": None,
        }

    def activate_native_background(self, resolved, *, context, parameters=None,
                                   installation_profile_digest=None,
                                   deterministic_seed=0):
        self.operations.append((
            "activate", resolved.bundle_digest, dict(parameters or {}), context,
            installation_profile_digest, deterministic_seed,
        ))
        self.context = context
        self.session = None
        self.generation = 0
        self.native_status = self._active_status("activate", resolved)
        return True

    def adopt_native_background(self, resolved, *, context, parameters=None,
                                installation_profile_digest=None):
        self.operations.append(("adopt", resolved.bundle_digest, context))
        if not self.adopt_result:
            return False
        self.context = context
        self.native_status = self._active_status("adopt", resolved)
        return True

    def update_native_background_parameters(self, parameters):
        self.operations.append(("update_native", dict(parameters)))
        return True

    def publish_sparse_overlay(self, pixels, **fields):
        self.operations.append(("publish", dict(fields)))
        if self.context is None:
            raise AssertionError("background context must precede foreground")
        self.session = fields["controller_session_id"]
        self.generation = fields["generation"]
        return True

    def renew_sparse_overlay(self, **fields):
        self.operations.append(("renew", dict(fields)))
        return True

    def clear_sparse_overlay(self, **fields):
        self.operations.append(("clear_sparse", dict(fields)))
        return True

    def recover_native_background_to_host(self, colors):
        frame = np.asarray(colors, dtype=np.uint8)
        self.operations.append(("recover", frame.copy()))
        outcome = self.recover_outcomes.pop(0) if self.recover_outcomes else True
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is False:
            return False
        self.context = None
        self.native_status = {
            "state": "host_full_scene",
            "operation": "recover",
            "rollout_enabled": True,
        }
        return True

    def set_all_pixels(self, colors):
        self.operations.append(("set_all", np.asarray(colors).copy()))
        return True

    def configure(self):
        return None

    def clear(self):
        self.operations.append(("clear",))

    def get_stats(self):
        return {"aggregate": {"native_background": dict(self.native_status)}}


def _scene():
    fallback = {
        "plugin_id": "gradient",
        "provider": "python",
        "parameter_overrides": {},
        "resolved_parameters": {},
    }
    return {
        "schema": "ledgrid.scene-state",
        "schema_version": 1,
        "revision": 41,
        "background": {
            "plugin_id": PLUGIN,
            "provider": "receiver_native",
            "parameter_overrides": {},
            "resolved_parameters": dict(PARAMETERS),
            "bundle_digest": BUNDLE,
            "expected_payload_digest": PAYLOAD,
        },
        "overlays": [],
        "known_python_fallback": fallback,
    }


class ReceiverNativeProductManagerTests(unittest.TestCase):
    def make_manager(self, *, flags=FLAGS, adopt=True, recover_outcomes=()):
        controller = _Controller(
            adopt=adopt, recover_outcomes=recover_outcomes
        )
        with contextlib.redirect_stdout(io.StringIO()):
            manager = AnimationManager(
                controller,
                feature_flags=flags,
                native_background_library=_Library(),
                auto_start=False,
            )
        manager._launch_animation_loop = lambda: None
        return controller, manager

    def test_catalog_binds_only_the_trusted_managed_artifact(self):
        _controller, manager = self.make_manager()
        descriptor = next(
            item for item in manager.list_components()
            if item.get("plugin_id") == PLUGIN
        )
        self.assertEqual(descriptor["availability"]["state"], "ready")
        self.assertEqual(descriptor["build"]["bundle_digest"], BUNDLE)
        self.assertEqual(descriptor["build"]["expected_payload_digest"], PAYLOAD)
        self.assertEqual(descriptor["build"]["identity_authority"], "managed_library")

        _controller, gated = self.make_manager(
            flags=AnimationPipelineFeatureFlags(
                receiver_local_background=True,
                receiver_sparse_overlay=True,
            )
        )
        gated_descriptor = next(
            item for item in gated.list_components()
            if item.get("plugin_id") == PLUGIN
        )
        self.assertEqual(gated_descriptor["availability"]["state"], "gated")
        self.assertFalse(gated.scene_provider_policy().managed_native_enabled)

    def test_quarantine_clear_resolves_only_the_managed_exact_bundle(self):
        controller, manager = self.make_manager()
        result = manager.clear_native_background_quarantine(BUNDLE)
        self.assertEqual(result["operation"], "clear_quarantine")
        self.assertEqual(
            controller.operations[-1], ("clear_quarantine", BUNDLE)
        )
        with self.assertRaisesRegex(ValueError, "not published"):
            manager.clear_native_background_quarantine("0" * 64)

    def test_start_update_preview_and_complete_host_recovery(self):
        controller, manager = self.make_manager()
        self.assertTrue(manager.start_scene(_scene()))
        names = [item[0] for item in controller.operations]
        self.assertLess(names.index("install"), names.index("activate"))
        self.assertLess(names.index("activate"), names.index("publish"))
        self.assertNotIn("set_all", names)
        np.testing.assert_array_equal(manager.current_frame_data[0], (12, 34, 56))
        status = manager.get_current_status()["receiver_hybrid"]
        self.assertTrue(status["healthy"])
        self.assertEqual(status["readable_devices"], [0, 1, 2, 3, 4])

        self.assertTrue(manager.update_scene_component(
            "background", params={"brightness": 0.5}
        ))
        self.assertIn("update_native", [item[0] for item in controller.operations])
        self.assertTrue(manager.stop_scene(clear_leds=False))
        names = [item[0] for item in controller.operations]
        self.assertEqual(names.count("recover"), 1)
        self.assertNotIn("set_all", names)

    def test_restart_adoption_is_read_only_before_foreground_repair(self):
        controller, manager = self.make_manager()
        self.assertTrue(manager.adopt_scene(_scene()))
        names = [item[0] for item in controller.operations]
        self.assertEqual(names[:2], ["adopt", "publish"])
        self.assertNotIn("install", names)
        self.assertNotIn("activate", names)
        self.assertNotIn("recover", names)

    def test_adoption_rejects_a_manager_that_already_owns_presentation(self):
        controller, manager = self.make_manager()
        manager.is_running = True

        self.assertFalse(manager.adopt_scene(_scene()))
        self.assertEqual(controller.operations, [])

    def test_failed_adoption_recovers_to_recorded_python_fallback(self):
        controller, manager = self.make_manager(adopt=False)
        self.assertTrue(manager.adopt_scene(_scene()))
        names = [item[0] for item in controller.operations]
        self.assertEqual(names[0], "adopt")
        self.assertEqual(names.count("recover"), 1)
        self.assertNotIn("set_all", names)
        self.assertEqual(manager.get_scene_state()["background"]["provider"], "python")

    def test_rejected_takeover_retains_native_authority_until_recovery_retries(self):
        controller, manager = self.make_manager(recover_outcomes=(False, True))
        self.assertTrue(manager.start_scene(_scene()))
        preview = np.asarray(manager.current_frame_data).copy()

        self.assertFalse(manager.stop_scene(clear_leds=False))
        self.assertEqual(
            manager.get_scene_state()["background"]["provider"],
            "receiver_native",
        )
        status = manager.get_current_status()["receiver_hybrid"]
        self.assertFalse(status["healthy"])
        self.assertIn("complete host takeover failed", status["error"])
        self.assertNotIn("clear", [item[0] for item in controller.operations])
        np.testing.assert_array_equal(manager.current_frame_data, preview)

        self.assertTrue(manager.recover_receiver_native())
        self.assertEqual(
            manager.get_scene_state()["background"]["provider"], "python"
        )
        self.assertEqual(
            [item[0] for item in controller.operations].count("recover"), 2
        )

    def test_takeover_exception_retains_native_authority_until_explicit_recovery(self):
        controller, manager = self.make_manager(
            recover_outcomes=(RuntimeError("injected takeover failure"), True)
        )
        self.assertTrue(manager.start_scene(_scene()))

        self.assertFalse(manager.recover_receiver_native())
        scene = manager.get_scene_state()
        self.assertEqual(scene["background"]["provider"], "receiver_native")
        status = manager.get_current_status()["receiver_hybrid"]
        self.assertIn("injected takeover failure", status["error"])
        self.assertEqual(status["driver"]["state"], "active")

        self.assertTrue(manager.recover_receiver_native())
        self.assertEqual(
            manager.get_scene_state()["background"]["provider"], "python"
        )


if __name__ == "__main__":
    unittest.main()
