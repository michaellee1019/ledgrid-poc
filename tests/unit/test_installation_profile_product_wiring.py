"""Product-boundary acceptance for managed installation-profile selection."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from animation.core.installation_profile_runtime import (
    EMPTY_INSTALLATION_PROFILE_DIGEST,
)
from animation.core.installation_profile_topology import (
    IDENTITY_INSTALLATION_PROFILE_TOPOLOGY,
    INSTALLED_INSTALLATION_PROFILE_TOPOLOGY,
)
from ipc.runtime_control import restore_display_state
from scripts.start_server import (
    handle_command,
    installation_profile_startup_context,
    installation_profile_topology_for_runtime,
    run_controller_mode,
)
from tools.deployment.preserve_deploy_settings import (
    load_saved_state,
    save_status,
)
from web.app import AnimationWebInterface, create_app


PROFILE_A = "a" * 64
PROFILE_B = "b" * 64


def _scene() -> dict:
    component = {
        "plugin_id": "solid",
        "provider": "python",
        "parameter_overrides": {},
        "resolved_parameters": {"red": 4},
    }
    return {
        "schema": "ledgrid.scene-state",
        "schema_version": 1,
        "revision": 1,
        "background": component,
        "overlays": [],
        "known_python_fallback": component,
    }


def _desired_state(digest: str = EMPTY_INSTALLATION_PROFILE_DIGEST) -> dict:
    return {
        "scene": _scene(),
        "installation_profile_digest": digest,
        "plant_modifiers": {"version": 1, "active": [], "strengths": {}},
        "output": {"power": True, "target_fps": 120},
    }


class _RestoreManager:
    def __init__(self, *, selected: str = PROFILE_A, reject: str | None = None):
        self.selected = selected
        self.reject = reject
        self.profile_calls: list[tuple[str, str]] = []
        self.mutations: list[tuple] = []

    def list_components(self):
        return [{"plugin_id": "solid", "provider": "python", "role": "background"}]

    def validate_output_brightness(self, value):
        return value

    def _validate_tempo_scale(self, value):
        return value

    def preflight_installation_profile(self, digest):
        self.profile_calls.append(("preflight", digest))
        if digest == self.reject:
            raise RuntimeError(f"managed profile {digest} is missing")
        return {"selected_digest": digest}

    def select_installation_profile(self, digest):
        self.profile_calls.append(("select", digest))
        if digest == self.reject:
            raise RuntimeError(f"managed profile {digest} is missing")
        self.selected = digest
        return {"selected_digest": digest}

    def get_current_status(self):
        return {"installation_profile_digest": self.selected}

    def start_scene(self, scene):
        self.mutations.append(("scene", scene))
        return True

    def stop_animation(self):
        self.mutations.append(("stop",))

    def set_plant_modifiers(self, value):
        self.mutations.append(("modifiers", value))

    def set_vibe(self, value):
        self.mutations.append(("vibe", value))

    def set_animation_speed_scale(self, value):
        self.mutations.append(("speed", value))

    def set_target_fps(self, value):
        self.mutations.append(("fps", value))

    def set_output_brightness(self, value):
        self.mutations.append(("brightness", value))


class RestoreProfileSelectionTests(unittest.TestCase):
    def test_restore_preflights_then_selects_exact_digest_before_scene(self):
        manager = _RestoreManager()

        self.assertTrue(restore_display_state(manager, _desired_state(PROFILE_B)))

        self.assertEqual(manager.profile_calls, [
            ("preflight", PROFILE_B),
            ("select", PROFILE_B),
        ])
        self.assertEqual(manager.selected, PROFILE_B)
        self.assertEqual(manager.mutations[0][0], "scene")
        self.assertEqual(manager.mutations[-1], ("fps", 120))

    def test_missing_profile_fails_aggregate_preflight_without_mutation(self):
        manager = _RestoreManager(reject=PROFILE_B)

        with self.assertRaisesRegex(RuntimeError, "is missing"):
            restore_display_state(manager, _desired_state(PROFILE_B))

        self.assertEqual(manager.profile_calls, [("preflight", PROFILE_B)])
        self.assertEqual(manager.selected, PROFILE_A)
        self.assertEqual(manager.mutations, [])

    def test_scene_rejection_rolls_back_profile_selection(self):
        manager = _RestoreManager()
        manager.start_scene = lambda scene: manager.mutations.append(("scene", scene)) or False

        self.assertFalse(restore_display_state(manager, _desired_state(PROFILE_B)))

        self.assertEqual(manager.profile_calls, [
            ("preflight", PROFILE_B),
            ("select", PROFILE_B),
            ("select", PROFILE_A),
        ])
        self.assertEqual(manager.selected, PROFILE_A)

    def test_all_zero_works_with_legacy_manager_without_profile_surface(self):
        manager = _RestoreManager()

        # Methods live on the class, so use a bounded proxy that deliberately
        # models an older manager without either managed-profile API.
        class Legacy:
            list_components = manager.list_components
            validate_output_brightness = manager.validate_output_brightness
            _validate_tempo_scale = manager._validate_tempo_scale
            start_scene = manager.start_scene
            set_plant_modifiers = manager.set_plant_modifiers
            set_target_fps = manager.set_target_fps

        self.assertTrue(restore_display_state(Legacy(), _desired_state()))

    def test_profile_selection_is_not_an_ipc_or_receiver_command(self):
        manager = _RestoreManager()

        self.assertFalse(handle_command(
            manager, "select_installation_profile", {"digest": PROFILE_B}
        ))
        self.assertFalse(handle_command(
            manager, "receiver_installation_profile", {"digest": PROFILE_B}
        ))

        self.assertEqual(manager.profile_calls, [])
        self.assertEqual(manager.mutations, [])


class StartupProfileContextTests(unittest.TestCase):
    def test_all_zero_does_not_create_shared_library_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library, digest, topology = installation_profile_startup_context(
                root,
                {"enabled": False},
                {"installation_profile_digest": EMPTY_INSTALLATION_PROFILE_DIGEST},
            )

            self.assertEqual(digest, EMPTY_INSTALLATION_PROFILE_DIGEST)
            self.assertEqual(topology, IDENTITY_INSTALLATION_PROFILE_TOPOLOGY)
            self.assertEqual(
                library.root,
                (root / "installation_profile_library").resolve(),
            )
            self.assertFalse(library.root.exists())

    def test_nonempty_startup_preflights_exact_digest(self):
        observed = []

        class Library:
            def __init__(self, root):
                self.root = root

            def resolve(self, digest, topology):
                observed.append((digest, topology))

        with tempfile.TemporaryDirectory() as directory, patch(
            "scripts.start_server.InstallationProfileLibrary", Library
        ):
            root = Path(directory)
            library, digest, topology = installation_profile_startup_context(
                root,
                {
                    "enabled": True,
                    "physical_lane_order": (0, 1, 3, 2, 4),
                    "reverse_strips_by_logical_receiver": (
                        True, False, True, False, False,
                    ),
                    "reverse_native_strips_by_logical_receiver": (
                        False, True, False, True, False,
                    ),
                },
                {"installation_profile_digest": PROFILE_B},
            )

        self.assertEqual(library.root, root / "installation_profile_library")
        self.assertEqual(digest, PROFILE_B)
        self.assertEqual(observed, [(PROFILE_B, topology)])

    def test_topology_domains_remain_independently_sourced(self):
        topology = installation_profile_topology_for_runtime({
            "enabled": True,
            "physical_lane_order": (2, 0, 3, 1, 4),
            "reverse_strips_by_logical_receiver": (
                True, False, True, False, False,
            ),
            "reverse_native_strips_by_logical_receiver": (
                False, True, False, True, False,
            ),
        })

        self.assertEqual(
            topology.logical_to_transport_routes,
            INSTALLED_INSTALLATION_PROFILE_TOPOLOGY.logical_to_transport_routes,
        )
        self.assertEqual(topology.physical_lane_order, (2, 0, 3, 1, 4))
        self.assertEqual(
            topology.reverse_host_strips_by_logical_receiver,
            (True, False, True, False, False),
        )
        self.assertEqual(
            topology.reverse_native_strips_by_logical_receiver,
            (False, True, False, True, False),
        )

    def test_disabled_rollout_retains_persisted_topology_domains(self):
        topology = installation_profile_topology_for_runtime({
            "enabled": False,
            "physical_lane_order": (0, 1, 3, 2, 4),
            "reverse_strips_by_logical_receiver": (
                True, True, False, False, False,
            ),
            "reverse_native_strips_by_logical_receiver": (
                False, False, True, True, False,
            ),
        })

        self.assertEqual(
            topology.logical_to_transport_routes,
            INSTALLED_INSTALLATION_PROFILE_TOPOLOGY.logical_to_transport_routes,
        )
        self.assertEqual(topology.physical_lane_order, (0, 1, 3, 2, 4))
        self.assertEqual(
            topology.reverse_host_strips_by_logical_receiver,
            (True, True, False, False, False),
        )
        self.assertEqual(
            topology.reverse_native_strips_by_logical_receiver,
            (False, False, True, True, False),
        )

    @staticmethod
    def _controller_args(config):
        return SimpleNamespace(
            receiver_hybrid_config=config,
            receiver_hybrid_canary=None,
            saved_state_file="unused.json",
            strips=33,
            leds_per_strip=138,
            bus=0,
            device=0,
            spi_speed=20_000_000,
            controller_debug=False,
            animation_speed_scale=1.0,
            brightness=50,
            animations_dir=None,
            target_fps=200,
        )

    def test_startup_resolves_before_controller_and_propagates_exact_context(self):
        events = []
        config = {
            "enabled": True,
            "physical_lane_order": (0, 1, 2, 3, 4),
            "reverse_strips_by_logical_receiver": (
                False, True, False, True, False,
            ),
            "reverse_native_strips_by_logical_receiver": (
                True, False, True, False, False,
            ),
        }
        state = {
            "animation": "solid",
            "params": {},
            "animation_speed_scale": 1.0,
            "brightness": 50,
            "target_fps": 120,
            "installation_profile_digest": PROFILE_A,
        }

        class Library:
            def __init__(self, root):
                self.root = root

            def resolve(self, digest, topology):
                events.append(("resolve", digest, topology))

        class MultiController:
            def __init__(self, **kwargs):
                events.append(("controller", kwargs))

            def with_receiver_hybrid_transport_policy(self, policy, **kwargs):
                events.append(("transport", policy, kwargs))
                return self

        class StopAfterManager(RuntimeError):
            pass

        def manager_factory(controller, **kwargs):
            events.append(("manager", controller, kwargs))
            raise StopAfterManager

        with patch("scripts.start_server.load_saved_state", return_value=state), patch(
            "scripts.start_server.InstallationProfileLibrary", Library
        ), patch("scripts.start_server.LEDController", MultiController), patch(
            "scripts.start_server.AnimationManager", side_effect=manager_factory
        ):
            with self.assertRaises(StopAfterManager):
                run_controller_mode(self._controller_args(config))

        self.assertEqual([event[0] for event in events[:2]], ["resolve", "controller"])
        controller_kwargs = next(
            event[1] for event in events if event[0] == "controller"
        )
        self.assertTrue(controller_kwargs["receiver_geometry_profile"])
        self.assertEqual(
            controller_kwargs["reverse_native_strips_by_logical_receiver"],
            (True, False, True, False, False),
        )
        self.assertEqual(controller_kwargs["num_devices"], 5)
        self.assertEqual(controller_kwargs["receiver_strip_counts"], (8, 8, 8, 8, 1))
        self.assertEqual(
            controller_kwargs["receiver_global_strip_offsets"],
            (0, 8, 16, 24, 32),
        )
        manager_kwargs = next(event[2] for event in events if event[0] == "manager")
        self.assertEqual(manager_kwargs["installation_profile_digest"], PROFILE_A)
        self.assertIsInstance(manager_kwargs["installation_profile_library"], Library)
        self.assertEqual(
            manager_kwargs["installation_profile_topology"], events[0][2]
        )

    def test_invalid_startup_profile_fails_before_controller_construction(self):
        events = []
        state = {
            "animation": "solid",
            "installation_profile_digest": PROFILE_B,
        }

        class Library:
            def __init__(self, root):
                pass

            def resolve(self, digest, topology):
                events.append(("resolve", digest))
                raise RuntimeError("profile bytes are corrupt")

        class MultiController:
            def __init__(self, **kwargs):
                events.append(("controller", kwargs))

        with patch("scripts.start_server.load_saved_state", return_value=state), patch(
            "scripts.start_server.InstallationProfileLibrary", Library
        ), patch("scripts.start_server.LEDController", MultiController):
            with self.assertRaisesRegex(
                RuntimeError, "saved installation profile.*corrupt"
            ):
                run_controller_mode(self._controller_args({"enabled": False}))

        self.assertEqual(events, [("resolve", PROFILE_B)])


class DeployStateProfileRoundTripTests(unittest.TestCase):
    def test_capture_idle_preservation_load_and_restore_keep_exact_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            presets = root / "presets"
            state_path = root / "before_deploy.json"
            status = {
                "is_running": True,
                "current_animation": "solid",
                "scene_state": _scene(),
                "animation_info": {"current_params": {"red": 4}},
                "installation_profile_digest": PROFILE_A,
            }

            save_status(status, presets, state_path)
            first_capture = load_saved_state(state_path)
            self.assertEqual(
                first_capture["installation_profile_digest"], PROFILE_A
            )

            # An idle status may omit the field on an older controller. The
            # already persisted presentation authority must survive unchanged.
            save_status({
                "is_running": False,
                "current_animation": None,
            }, presets, state_path)
            loaded = load_saved_state(state_path)
            self.assertEqual(loaded["installation_profile_digest"], PROFILE_A)

            manager = _RestoreManager(
                selected=EMPTY_INSTALLATION_PROFILE_DIGEST
            )
            self.assertTrue(restore_display_state(manager, loaded))
            self.assertEqual(manager.profile_calls, [
                ("preflight", PROFILE_A),
                ("select", PROFILE_A),
            ])

    def test_legacy_missing_digest_defaults_to_empty_without_filesystem_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            preset_path = root / "before-deploy.json"
            preset_path.write_text(json.dumps({
                "animation": "solid", "params": {"red": 4},
            }))
            state_path = root / "state.json"
            state_path.write_text(json.dumps({
                "animation": "solid",
                "preset_path": str(preset_path),
            }))

            loaded = load_saved_state(state_path)
            self.assertNotIn("installation_profile_digest", loaded)
            library, digest, topology = installation_profile_startup_context(
                root, {"enabled": False}, loaded
            )

            self.assertEqual(digest, EMPTY_INSTALLATION_PROFILE_DIGEST)
            self.assertEqual(topology, IDENTITY_INSTALLATION_PROFILE_TOPOLOGY)
            self.assertFalse(library.root.exists())


class _PreviewController:
    strip_count = 2
    leds_per_strip = 4
    total_leds = 8


class _PreviewManager:
    def __init__(self):
        self.controller = _PreviewController()
        self.preview_controller = _PreviewController()
        self.plugin_loader = None
        self.selected = EMPTY_INSTALLATION_PROFILE_DIGEST
        self.calls = []

    def select_installation_profile(self, digest):
        self.calls.append(digest)
        if digest == PROFILE_B:
            raise RuntimeError("corrupt managed profile")
        self.selected = digest
        return {"selected_digest": digest}


class _StatusChannel:
    def __init__(self, status):
        self.status = status

    def read_status(self):
        return dict(self.status)


class WebPreviewProfileTests(unittest.TestCase):
    def test_live_status_selects_profile_for_preview_without_command(self):
        channel = _StatusChannel({
            "installation_profile_digest": PROFILE_A,
            "led_info": {"strip_count": 2, "leds_per_strip": 4, "total_leds": 8},
        })
        preview = _PreviewManager()
        interface = AnimationWebInterface(channel, preview)

        status = interface._status_payload()

        self.assertEqual(preview.calls, [PROFILE_A])
        self.assertEqual(preview.selected, PROFILE_A)
        self.assertEqual(status["installation_profile_preview"]["state"], "selected")
        self.assertFalse(hasattr(channel, "send_command"))

    def test_failed_preview_switch_is_atomic_and_status_remains_renderable(self):
        channel = _StatusChannel({
            "installation_profile_digest": PROFILE_B,
            "led_info": {"strip_count": 2, "leds_per_strip": 4, "total_leds": 8},
        })
        preview = _PreviewManager()
        preview.selected = PROFILE_A
        interface = AnimationWebInterface(channel, preview)

        status = interface._status_payload()

        self.assertEqual(preview.selected, PROFILE_A)
        self.assertEqual(status["installation_profile_preview"]["state"], "rejected")
        self.assertIn("corrupt managed profile", status["installation_profile_preview"]["error"])

    def test_factory_injects_same_shared_library_root(self):
        captured = {}
        preview = _PreviewManager()

        def manager_factory(controller, **kwargs):
            captured.update(kwargs)
            preview.controller = controller
            return preview

        with tempfile.TemporaryDirectory() as directory, patch(
            "web.app.AnimationManager", side_effect=manager_factory
        ):
            root = Path(directory)
            create_app(project_root=root, strips=2, leds_per_strip=4)

        self.assertEqual(
            captured["installation_profile_library"].root,
            (root / "installation_profile_library").resolve(),
        )
        self.assertEqual(
            captured["installation_profile_topology"],
            IDENTITY_INSTALLATION_PROFILE_TOPOLOGY,
        )
        self.assertFalse((root / "installation_profile_library").exists())

    def test_web_has_no_installation_profile_mutator_route(self):
        preview = _PreviewManager()
        interface = AnimationWebInterface(_StatusChannel({}), preview)
        routes = {rule.rule for rule in interface.app.url_map.iter_rules()}

        self.assertFalse(any(
            "installation-profile" in route or "installation_profile" in route
            for route in routes
        ))
        response = interface.app.test_client().post(
            "/api/v1/installation-profile", json={"digest": PROFILE_A}
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(preview.calls, [])


if __name__ == "__main__":
    unittest.main()
