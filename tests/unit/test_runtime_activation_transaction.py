"""Controller-owned activation CAS, compensation, and observation coverage."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import threading
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

from animation.core.installation_profile_runtime import (
    EMPTY_INSTALLATION_PROFILE_DIGEST,
)
from animation.core.presentation_contracts import resolve_vibe
from ipc.runtime_control import (
    ControllerActivationConflictError,
    ControllerActivationCoordinator,
    ControllerActivationPublicationError,
    manager_controller_runtime_digests,
)
from ipc.scene_contract import (
    BROWSER_SCENE_SCHEMA,
    GLOBAL_SETTINGS_SCHEMA,
    activation_identity_from_basis,
    browser_scene_to_host_scene,
    build_scene_activation_basis,
    decorate_browser_component,
    decorate_catalog,
    normalize_browser_scene_document,
    normalize_scene_activation_status,
    scene_activation_basis_digest,
    validate_scene_activation_status_transition,
)
from ipc.control_channel import FileControlChannel
from web.local_control import LocalControlChannel
from scripts.start_server import process_activation_commands
from scripts.start_server import persist_controller_restart_state
from tools.deployment.preserve_deploy_settings import load_saved_state


PROFILE_A = "a" * 64


def _managed_receiver_scene(*, revision: int) -> dict:
    return {
        "schema": "ledgrid.scene-state",
        "schema_version": 1,
        "revision": revision,
        "background": {
            "plugin_id": "aurora_curtains_native",
            "provider": "receiver_native",
            "parameter_overrides": {},
            "resolved_parameters": {"brightness": 0.42},
            "bundle_digest": "b" * 64,
            "expected_payload_digest": "e" * 64,
        },
        "overlays": [],
        "known_python_fallback": {
            "plugin_id": "gradient",
            "provider": "python",
            "parameter_overrides": {"speed": 0.7},
            "resolved_parameters": {"speed": 0.7},
        },
    }


def _receiver_status(
    *,
    scene_revision: int,
    generation: int = 7,
    scene: dict | None = None,
    profile_digest: str = PROFILE_A,
) -> dict:
    """Correlated receiver evidence fixture used by activation safety tests."""

    background = (scene or _managed_receiver_scene(
        revision=scene_revision
    ))["background"]
    effective_parameters = dict(background["resolved_parameters"])
    effective_parameters.update(background["parameter_overrides"])
    return {
        "healthy": True,
        "telemetry_complete": True,
        "source_scene_revision": scene_revision,
        "context_revision": 4,
        "context_digest": "c" * 64,
        "publisher": {
            "healthy": True,
            "active": True,
            "authority_known": True,
            "repair_required": False,
            "controller_session_id": "d" * 32,
            "generation": generation,
            "binding": {"scene_revision": scene_revision},
            "last_success_at": float(generation),
        },
        "driver": {
            "state": "active",
            "bundle_digest": background["bundle_digest"],
            "payload_digest": background["expected_payload_digest"],
            "parameter_digest": "f" * 64,
            "effective_parameters": effective_parameters,
            "context_digest": "c" * 64,
            "installation_profile_digest": profile_digest,
        },
    }


def _component(component_id: str, *, role: str = "background") -> dict:
    return {
        "plugin_id": component_id,
        "provider": "python",
        "role": role,
        "entrypoint": f"animation.plugins.{component_id}:Fixture",
        "parameter_schema_version": 1,
        "parameter_schema": {
            "speed": {
                "type": "float",
                "min": 0.1,
                "max": 5.0,
                "default": 1.0,
            },
        },
        "defaults": {
            "speed": 1.0,
            "plant_aware": False,
            "plant_modifiers": {"version": 1, "active": [], "strengths": {}},
            "vibe": {"id": "neutral"},
            "output": {"brightness": 50},
        },
        "availability": {"state": "ready"},
        "compatibility": {
            "composable": True,
            "implementation_loaded": True,
        },
        "build": {},
    }


def _catalog() -> list[dict]:
    result = []
    for index, component in enumerate(
        decorate_catalog([
            _component("gradient"),
            _component("clock_overlay", role="overlay"),
        ]),
        start=1,
    ):
        result.append(decorate_browser_component(
            component,
            browser_runtime={
                "kind": "python",
                "supported": True,
                "digest": f"{index:064x}",
            },
        ))
    return result


def _browser_scene(
    catalog: list[dict],
    *,
    revision: int,
    profile_digest: str,
    speed: float,
) -> dict:
    by_id = {item["plugin_id"]: item for item in catalog}

    def binding(component_id: str, value: float) -> dict:
        managed = by_id[component_id]["browser_capabilities"]["managed_identity"]
        return {
            "provider": managed["provider"],
            "component_id": managed["component_id"],
            "component_digest": managed["component_digest"],
            "runtime_digest": managed["runtime_digest"],
            "parameter_schema_version": managed["parameter_schema_version"],
            "parameters": {"speed": value},
        }

    background = binding("gradient", speed)
    return {
        "schema": BROWSER_SCENE_SCHEMA,
        "schema_version": 1,
        "revision": revision,
        "background": background,
        "layers": [{
            "role": "clock",
            "component": binding("clock_overlay", speed + 0.1),
            "enabled": True,
            "opacity": 220,
            "blend_mode": "source_over",
        }],
        "installation_profile": {"digest": profile_digest},
        "fallback": deepcopy(background),
    }


def _globals(
    *,
    revision: int,
    vibe_id: str,
    brightness: int,
    speed: float,
    target_fps: int,
) -> dict:
    vibe = resolve_vibe(vibe_id).state.to_dict()
    return {
        "schema": GLOBAL_SETTINGS_SCHEMA,
        "schema_version": 1,
        "revision": revision,
        "vibe": {
            "vibe_id": vibe["vibe_id"],
            "profile_version": vibe["profile_version"],
            "resolved_profile_digest": vibe["resolved_profile_digest"],
        },
        "plant_modifiers": {
            "version": 1,
            "active": ["illuminate"],
            "strengths": {"illuminate": 0.4},
        },
        "output": {
            "power": True,
            "brightness": brightness,
            "animation_speed_scale": speed,
            "target_fps": target_fps,
        },
    }


class _FakeManager:
    def __init__(self, catalog: list[dict], scene: dict, settings: dict) -> None:
        self.catalog = deepcopy(catalog)
        self.scene = deepcopy(scene)
        self.profile = EMPTY_INSTALLATION_PROFILE_DIGEST
        self.vibe = deepcopy(settings["vibe"])
        self.plant_modifiers = deepcopy(settings["plant_modifiers"])
        self.brightness = settings["output"]["brightness"]
        self.animation_speed_scale = settings["output"]["animation_speed_scale"]
        self.target_fps = settings["output"]["target_fps"]
        self.is_running = settings["output"]["power"]
        self.preflight_error: Exception | None = None
        self.preflight_entered: threading.Event | None = None
        self.preflight_release: threading.Event | None = None
        self.mutation_count = 0

    def list_components(self) -> list[dict]:
        return deepcopy(self.catalog)

    def get_scene_state(self) -> dict | None:
        return deepcopy(self.scene)

    def get_current_status(self) -> dict:
        vibe_state = {
            "schema": "ledgrid.vibe-state",
            "schema_version": 1,
            "revision": 0,
            **deepcopy(self.vibe),
        }
        return {
            "is_running": self.is_running,
            "painter_active": False,
            "scene_state": deepcopy(self.scene),
            "current_animation": (
                self.scene["background"]["plugin_id"]
                if self.is_running and self.scene is not None
                else None
            ),
            "animation_info": (
                {
                    "current_params": {
                        **self.scene["background"].get(
                            "resolved_parameters", {}
                        ),
                        **self.scene["background"].get(
                            "parameter_overrides", {}
                        ),
                    }
                }
                if self.is_running and self.scene is not None
                else None
            ),
            "current_preset": None,
            "brightness": self.brightness,
            "animation_speed_scale": self.animation_speed_scale,
            "target_fps": self.target_fps,
            "vibe": {"state": vibe_state},
            "plant_modifiers": deepcopy(self.plant_modifiers),
            "installation_profile_digest": self.profile,
        }

    def get_current_frame(self) -> dict:
        return {"frame_data": [], "frame_count": 0}

    def preflight_scene(self, _scene: dict) -> None:
        if self.preflight_entered is not None:
            self.preflight_entered.set()
        if self.preflight_release is not None:
            self.preflight_release.wait(timeout=2)
        if self.preflight_error is not None:
            raise self.preflight_error

    def preflight_installation_profile(self, _digest: str) -> None:
        return None

    def select_installation_profile(self, digest: str) -> dict:
        self.mutation_count += 1
        self.profile = digest
        return {"selected_digest": digest}

    def set_plant_modifiers(self, value: dict) -> dict:
        self.mutation_count += 1
        self.plant_modifiers = deepcopy(value)
        return deepcopy(value)

    def set_vibe(self, value: dict) -> dict:
        self.mutation_count += 1
        self.vibe = deepcopy(value)
        return {"state": deepcopy(value)}

    def set_animation_speed_scale(self, value: float) -> float:
        self.mutation_count += 1
        self.animation_speed_scale = float(value)
        return self.animation_speed_scale

    def set_target_fps(self, value: int) -> int:
        self.mutation_count += 1
        self.target_fps = int(value)
        return self.target_fps

    def set_output_brightness(self, value: int) -> int:
        self.mutation_count += 1
        self.brightness = int(value)
        return self.brightness

    def start_scene(self, scene: dict) -> bool:
        self.mutation_count += 1
        self.scene = deepcopy(scene)
        self.is_running = True
        return True

    def stop_animation(self) -> None:
        self.mutation_count += 1
        self.scene = None
        self.is_running = False

    def state(self) -> dict:
        return {
            "scene": deepcopy(self.scene),
            "profile": self.profile,
            "vibe": deepcopy(self.vibe),
            "plant_modifiers": deepcopy(self.plant_modifiers),
            "brightness": self.brightness,
            "animation_speed_scale": self.animation_speed_scale,
            "target_fps": self.target_fps,
            "is_running": self.is_running,
        }


class _DisabledReceiverProfileController:
    _receiver_geometry_profile_enabled = False
    _installation_profile_wall = None

    def __init__(self) -> None:
        self.getter_calls = 0
        self.install_calls = 0

    def installation_profile_wall(self):
        self.getter_calls += 1
        raise RuntimeError("disabled receiver profile getter must not run")

    def install_installation_profile(self, _candidate):
        self.install_calls += 1
        raise RuntimeError("disabled receiver profile installer must not run")


class RuntimeActivationTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = _catalog()
        initial_document = normalize_browser_scene_document(
            _browser_scene(
                self.catalog,
                revision=1,
                profile_digest=EMPTY_INSTALLATION_PROFILE_DIGEST,
                speed=0.5,
            ),
            catalog=self.catalog,
            purpose="activation",
        )
        self.initial_scene = browser_scene_to_host_scene(
            initial_document, catalog=self.catalog
        )
        self.initial_globals = _globals(
            revision=0,
            vibe_id="neutral",
            brightness=200,
            speed=1.0,
            target_fps=120,
        )
        self.desired_globals = _globals(
            revision=8,
            vibe_id="cozy",
            brightness=96,
            speed=0.45,
            target_fps=90,
        )
        desired_document = normalize_browser_scene_document(
            _browser_scene(
                self.catalog,
                revision=17,
                profile_digest=PROFILE_A,
                speed=0.7,
            ),
            catalog=self.catalog,
            purpose="activation",
        )
        self.desired_document = desired_document
        self.desired_scene = browser_scene_to_host_scene(
            desired_document, catalog=self.catalog
        )

    def coordinator(self, **kwargs) -> tuple[_FakeManager, ControllerActivationCoordinator]:
        manager = _FakeManager(
            self.catalog, self.initial_scene, self.initial_globals
        )
        coordinator = ControllerActivationCoordinator(manager, **kwargs)
        return manager, coordinator

    def command(
        self,
        coordinator: ControllerActivationCoordinator,
        *,
        activation_id: str | None = None,
        globals_state: dict | None = None,
        expires_at: int | None = None,
        profile_digest: str = PROFILE_A,
    ) -> dict:
        settings = deepcopy(globals_state or self.desired_globals)
        if profile_digest == PROFILE_A:
            document = self.desired_document
            scene = self.desired_scene
        else:
            document = normalize_browser_scene_document(
                _browser_scene(
                    self.catalog,
                    revision=17,
                    profile_digest=profile_digest,
                    speed=0.7,
                ),
                catalog=self.catalog,
                purpose="activation",
            )
            scene = browser_scene_to_host_scene(document, catalog=self.catalog)
        basis = build_scene_activation_basis(
            browser_scene=document,
            catalog=self.catalog,
            global_settings=settings,
            controller_runtime_digests=manager_controller_runtime_digests(
                coordinator.manager
            ),
            controller_session_id=coordinator.session_id,
            controller_state_revision=coordinator.state_revision,
            current_identity_digest=coordinator.current_identity_digest,
            qualification_version="server-check-v1",
            qualification_record_digest="8" * 64,
            expires_at=(
                int((time.time() + 120) * 1000)
                if expires_at is None
                else expires_at
            ),
        )
        return {
            "schema": "ledgrid.scene-activation-command",
            "schema_version": 1,
            "activation_id": activation_id or str(uuid.uuid4()),
            "check_token_digest": "9" * 64,
            "basis": basis,
            "basis_digest": scene_activation_basis_digest(basis),
            "desired": {
                "scene": deepcopy(scene),
                "global_settings": settings,
                "installation_profile_digest": profile_digest,
            },
        }

    def assert_status_history_valid(self, history: list[dict]) -> None:
        normalized = [normalize_scene_activation_status(item) for item in history]
        for before, after in zip(normalized, normalized[1:]):
            validate_scene_activation_status_transition(before, after)

    def test_success_is_observed_before_active_and_advances_revision(self) -> None:
        history: list[dict] = []
        manager, coordinator = self.coordinator(status_sink=history.append)
        command = self.command(coordinator)

        status = coordinator.activate(command)

        self.assertEqual(status["phase"], "active")
        self.assertEqual(coordinator.state_revision, 1)
        self.assertEqual(status["controller"]["state_revision_after"], 1)
        self.assertEqual(
            status["observed_identity"], activation_identity_from_basis(command["basis"])
        )
        self.assertTrue(status["telemetry"]["complete"])
        self.assertTrue(status["telemetry"]["fresh"])
        self.assertEqual(manager.scene, self.desired_scene)
        self.assertEqual(manager.profile, PROFILE_A)
        self.assert_status_history_valid(history)
        self.assertEqual(
            [item["phase"] for item in history],
            ["queued", "preflighting", "applying", "observing", "active"],
        )

    def test_restart_state_commit_precedes_active_publication(self) -> None:
        events: list[str] = []
        manager, coordinator = self.coordinator(
            status_sink=lambda status: events.append(status["phase"]),
            commit_callback=lambda: events.append("restart_state_committed"),
        )

        status = coordinator.activate(self.command(coordinator))

        self.assertEqual(status["phase"], "active")
        self.assertLess(
            events.index("restart_state_committed"), events.index("active")
        )

    def test_one_shot_desired_commit_failure_persists_compensation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / "saved.json"
            manager = _FakeManager(
                self.catalog, self.initial_scene, self.initial_globals
            )
            holder: list[ControllerActivationCoordinator] = []
            attempts = 0

            def commit() -> None:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise OSError("one-shot desired state commit failure")
                persist_controller_restart_state(
                    manager,
                    holder[0],
                    presets_dir=root / "presets",
                    state_path=state_path,
                )

            coordinator = ControllerActivationCoordinator(
                manager, commit_callback=commit
            )
            holder.append(coordinator)
            before = manager.state()

            status = coordinator.activate(self.command(coordinator))
            saved = load_saved_state(state_path)

            self.assertEqual(status["phase"], "rolled_back")
            self.assertEqual(status["rollback"]["result"], "succeeded")
            self.assertEqual(manager.state(), before)
            self.assertEqual(attempts, 2)
            self.assertEqual(saved["scene"], self.initial_scene)
            self.assertEqual(
                saved["installation_profile_digest"],
                EMPTY_INSTALLATION_PROFILE_DIGEST,
            )

    def test_both_restart_state_commits_failing_reports_failed_rollback(self) -> None:
        phases: list[str] = []

        def fail_commit() -> None:
            raise OSError("restart state disk unavailable")

        manager, coordinator = self.coordinator(
            status_sink=lambda status: phases.append(status["phase"]),
            commit_callback=fail_commit,
        )
        before = manager.state()

        status = coordinator.activate(self.command(coordinator))

        self.assertEqual(status["phase"], "failed")
        self.assertEqual(status["rollback"]["result"], "failed")
        self.assertEqual(manager.state(), before)
        self.assertNotIn("active", phases)
        self.assertIn("restart state disk unavailable", status["error"])

    def test_active_publication_failure_persists_compensated_state_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / "saved.json"
            phases: list[str] = []

            def fail_active(status: dict) -> None:
                if status["phase"] == "active":
                    raise OSError("active receipt disk unavailable")
                phases.append(status["phase"])

            manager = _FakeManager(
                self.catalog, self.initial_scene, self.initial_globals
            )
            holder: list[ControllerActivationCoordinator] = []

            def commit() -> None:
                persist_controller_restart_state(
                    manager,
                    holder[0],
                    presets_dir=root / "presets",
                    state_path=state_path,
                )

            coordinator = ControllerActivationCoordinator(
                manager,
                status_sink=fail_active,
                commit_callback=commit,
            )
            holder.append(coordinator)
            before = manager.state()

            status = coordinator.activate(self.command(coordinator))
            saved = load_saved_state(state_path)

            self.assertEqual(status["phase"], "rolled_back")
            self.assertEqual(status["rollback"]["result"], "succeeded")
            self.assertEqual(manager.state(), before)
            self.assertEqual(saved["scene"], self.initial_scene)
            self.assertEqual(
                saved["installation_profile_digest"],
                EMPTY_INSTALLATION_PROFILE_DIGEST,
            )
            self.assertEqual(saved["brightness"], 200)
            self.assertEqual(saved["target_fps"], 120)
            self.assertNotIn("active", phases)

    def test_powered_off_activation_retains_selected_scene_identity(self) -> None:
        manager, coordinator = self.coordinator()
        powered_off = deepcopy(self.desired_globals)
        powered_off["output"]["power"] = False

        status = coordinator.activate(self.command(
            coordinator, globals_state=powered_off
        ))

        self.assertEqual(status["phase"], "active")
        self.assertFalse(manager.is_running)
        self.assertIsNone(manager.scene)
        self.assertEqual(
            coordinator.controller_status()["scene_state"], self.desired_scene
        )
        self.assertEqual(
            coordinator.controller_status()["active_identity"],
            status["observed_identity"],
        )

    def test_duplicate_id_and_basis_is_idempotent_but_rebinding_conflicts(self) -> None:
        manager, coordinator = self.coordinator()
        activation_id = str(uuid.uuid4())
        command = self.command(coordinator, activation_id=activation_id)
        first = coordinator.activate(command)
        mutation_count = manager.mutation_count

        repeated = coordinator.activate(deepcopy(command))

        self.assertEqual(repeated, first)
        self.assertEqual(manager.mutation_count, mutation_count)
        changed_settings = deepcopy(self.desired_globals)
        changed_settings["revision"] += 1
        changed = self.command(
            coordinator,
            activation_id=activation_id,
            globals_state=changed_settings,
        )
        with self.assertRaises(ControllerActivationConflictError):
            coordinator.queue(changed)

    def test_each_controller_cas_identity_rejects_without_mutation(self) -> None:
        changes = {
            "session_id": "f" * 32,
            "state_revision": 7,
            "current_identity_digest": "f" * 64,
        }
        for field, replacement in changes.items():
            with self.subTest(field=field):
                manager, coordinator = self.coordinator()
                before = manager.state()
                command = self.command(coordinator)
                command["basis"]["controller"][field] = replacement
                command["basis_digest"] = scene_activation_basis_digest(
                    command["basis"]
                )

                status = coordinator.activate(command)

                self.assertEqual(status["phase"], "failed")
                self.assertIn("changed after Check", status["error"])
                self.assertEqual(manager.state(), before)
                self.assertEqual(manager.mutation_count, 0)
                self.assertEqual(coordinator.state_revision, 0)
                normalize_scene_activation_status(status)

    def test_changed_controller_runtime_rejects_before_mutation(self) -> None:
        manager, coordinator = self.coordinator()
        command = self.command(coordinator)
        before = manager.state()
        manager.catalog[0]["component_digest"] = "f" * 64

        status = coordinator.activate(command)

        self.assertEqual(status["phase"], "failed")
        self.assertIn("controller runtime changed after Check", status["error"])
        self.assertEqual(manager.state(), before)
        self.assertEqual(manager.mutation_count, 0)
        self.assertEqual(coordinator.state_revision, 0)

    def test_all_apply_boundaries_compensate_to_the_exact_snapshot(self) -> None:
        boundaries = (
            "installation_profile",
            "plant_modifiers",
            "vibe",
            "animation_speed_scale",
            "target_fps",
            "brightness",
            "scene",
            "complete",
        )
        for failing_boundary in boundaries:
            with self.subTest(boundary=failing_boundary):
                history: list[dict] = []

                def inject(phase: str, boundary: str, _activation_id: str) -> None:
                    if phase == "applying" and boundary == failing_boundary:
                        raise RuntimeError(f"injected {boundary}")

                manager, coordinator = self.coordinator(
                    fault_injector=inject,
                    status_sink=history.append,
                )
                before = manager.state()
                status = coordinator.activate(self.command(coordinator))

                self.assertEqual(status["phase"], "rolled_back")
                self.assertEqual(status["rollback"]["result"], "succeeded")
                self.assertEqual(manager.state(), before)
                self.assertEqual(coordinator.state_revision, 1)
                self.assertEqual(
                    status["controller"]["state_revision_after"], 1
                )
                self.assertTrue(status["telemetry"]["complete"])
                self.assertTrue(status["telemetry"]["fresh"])
                self.assert_status_history_valid(history)

    def test_status_publication_failure_never_blocks_compensation(self) -> None:
        """Every phase write either precedes mutation or retains exact rollback."""

        publication_phases = (
            "queued",
            "preflighting",
            "applying",
            "observing",
            "active",
            "rolling_back",
            "rolled_back",
            "failed",
            "timed_out",
        )
        for failing_phase in publication_phases:
            with self.subTest(phase=failing_phase):
                published: list[dict] = []
                failed_once = False

                def sink(status: dict) -> None:
                    nonlocal failed_once
                    if status["phase"] == failing_phase and not failed_once:
                        failed_once = True
                        raise OSError(f"injected {failing_phase} publication")
                    published.append(deepcopy(status))

                def inject(phase: str, boundary: str, _activation_id: str) -> None:
                    if (
                        failing_phase in {"rolling_back", "rolled_back"}
                        and phase == "applying"
                        and boundary == "vibe"
                    ):
                        raise RuntimeError("injected apply fault")

                wall_time = [1_000]
                manager, coordinator = self.coordinator(
                    status_sink=sink,
                    fault_injector=inject,
                    wall_clock_ms=lambda: wall_time[0],
                )
                if failing_phase == "failed":
                    manager.preflight_error = RuntimeError("preflight rejected")
                expires_at = 1_000 if failing_phase == "timed_out" else None
                command = self.command(coordinator, expires_at=expires_at)
                before = manager.state()

                if failing_phase == "queued":
                    with self.assertRaises(ControllerActivationPublicationError):
                        coordinator.queue(command)
                    self.assertEqual(manager.state(), before)
                    self.assertEqual(manager.mutation_count, 0)
                    # A later controller-loop read drains the retained queued
                    # receipt before the command can be executed.
                    self.assertEqual(
                        coordinator.get(command["activation_id"])["phase"], "queued"
                    )
                    self.assertEqual([item["phase"] for item in published], ["queued"])
                    continue

                status = coordinator.activate(command)

                if failing_phase in {"preflighting", "applying", "failed", "timed_out"}:
                    self.assertIn(status["phase"], {"failed", "timed_out"})
                    self.assertEqual(manager.mutation_count, 0)
                else:
                    self.assertEqual(status["phase"], "rolled_back")
                self.assertEqual(manager.state(), before)
                # A transient failure is replayed in legal phase order. This is
                # also the retry path for a terminal receipt that initially
                # could not be written.
                coordinator.get(command["activation_id"])
                self.assertTrue(failed_once)
                self.assertEqual(published[-1]["phase"], status["phase"])
                self.assert_status_history_valid(published)

    def test_persistent_rollback_publication_failure_still_restores_state(self) -> None:
        recovered = False
        published: list[dict] = []

        def sink(status: dict) -> None:
            if status["phase"] == "rolling_back" and not recovered:
                raise OSError("status disk unavailable")
            published.append(deepcopy(status))

        def inject(phase: str, boundary: str, _activation_id: str) -> None:
            if phase == "applying" and boundary == "vibe":
                raise RuntimeError("injected apply fault")

        manager, coordinator = self.coordinator(
            status_sink=sink, fault_injector=inject
        )
        command = self.command(coordinator)
        before = manager.state()

        status = coordinator.activate(command)

        self.assertEqual(status["phase"], "rolled_back")
        self.assertEqual(status["rollback"]["result"], "succeeded")
        self.assertEqual(manager.state(), before)
        self.assertEqual(published[-1]["phase"], "applying")

        recovered = True
        coordinator.get(command["activation_id"])
        self.assertEqual(
            [item["phase"] for item in published[-2:]],
            ["rolling_back", "rolled_back"],
        )
        self.assert_status_history_valid(published)

    def test_preflight_failure_never_mutates_and_has_no_rollback_snapshot(self) -> None:
        manager, coordinator = self.coordinator()
        manager.preflight_error = RuntimeError("preflight rejected")
        before = manager.state()

        status = coordinator.activate(self.command(coordinator))

        self.assertEqual(status["phase"], "failed")
        self.assertFalse(status["rollback"]["available"])
        self.assertIsNone(status["rollback"]["result"])
        self.assertEqual(manager.state(), before)
        self.assertEqual(manager.mutation_count, 0)
        self.assertEqual(coordinator.state_revision, 0)
        normalize_scene_activation_status(status)

    def test_legacy_or_painter_state_without_scene_fails_before_mutation(self) -> None:
        manager = _FakeManager(
            self.catalog, self.initial_scene, self.initial_globals
        )
        manager.scene = None
        manager.is_running = True
        coordinator = ControllerActivationCoordinator(manager)
        before = manager.state()

        status = coordinator.activate(self.command(coordinator))

        self.assertEqual(status["phase"], "failed")
        self.assertIn("cannot be restored exactly", status["error"])
        self.assertEqual(manager.state(), before)
        self.assertEqual(manager.mutation_count, 0)
        self.assertEqual(coordinator.state_revision, 0)

    def test_durable_command_expiring_in_queue_becomes_correlated_timeout(self) -> None:
        wall_time = [1_000]
        manager, coordinator = self.coordinator(
            wall_clock_ms=lambda: wall_time[0]
        )
        command = self.command(coordinator, expires_at=1_001)
        queued = coordinator.queue(command)
        wall_time[0] = 1_001

        status = coordinator.execute(queued["activation_id"])

        self.assertEqual(status["phase"], "timed_out")
        self.assertIn("expired", status["error"])
        self.assertEqual(manager.mutation_count, 0)
        self.assertEqual(coordinator.state_revision, 0)
        normalize_scene_activation_status(status)

    def test_receiver_snapshot_failure_aborts_before_first_mutation(self) -> None:
        class BrokenReceiverController:
            @staticmethod
            def installation_profile_wall():
                raise OSError("receiver snapshot unavailable")

            @staticmethod
            def install_installation_profile(_candidate):
                raise AssertionError("install must not run")

        manager, coordinator = self.coordinator()
        manager.controller = BrokenReceiverController()
        before = manager.state()

        status = coordinator.activate(self.command(coordinator))

        self.assertEqual(status["phase"], "failed")
        self.assertIn("receiver profile authority", status["error"])
        self.assertEqual(manager.state(), before)
        self.assertEqual(manager.mutation_count, 0)
        self.assertEqual(coordinator.state_revision, 0)

    def test_explicit_disabled_empty_profile_noop_never_touches_receivers(self) -> None:
        manager, coordinator = self.coordinator()
        controller = _DisabledReceiverProfileController()
        manager.controller = controller
        command = self.command(
            coordinator,
            profile_digest=EMPTY_INSTALLATION_PROFILE_DIGEST,
        )

        status = coordinator.activate(command)

        self.assertEqual(status["phase"], "active")
        self.assertEqual(
            status["observed_identity"],
            activation_identity_from_basis(command["basis"]),
        )
        self.assertTrue(status["rollback"]["available"])
        self.assertEqual(manager.profile, EMPTY_INSTALLATION_PROFILE_DIGEST)
        self.assertEqual(controller.getter_calls, 0)
        self.assertEqual(controller.install_calls, 0)

    def test_host_python_activation_skips_disabled_receiver_profile_transaction(self) -> None:
        manager, coordinator = self.coordinator()
        controller = _DisabledReceiverProfileController()
        manager.controller = controller

        status = coordinator.activate(
            self.command(coordinator, profile_digest=PROFILE_A)
        )

        self.assertEqual(status["phase"], "active")
        self.assertEqual(manager.profile, PROFILE_A)
        self.assertEqual(controller.getter_calls, 0)
        self.assertEqual(controller.install_calls, 0)

    def test_empty_profile_noop_rejects_every_nonexact_authority_case(self) -> None:
        cases = (
            ("enabled gate", True, EMPTY_INSTALLATION_PROFILE_DIGEST, False),
            ("unknown gate", None, EMPTY_INSTALLATION_PROFILE_DIGEST, False),
            ("nonempty current", False, PROFILE_A, False),
            ("receiver native", False, EMPTY_INSTALLATION_PROFILE_DIGEST, False),
        )
        for label, gate, current_profile, nonempty_desired in cases:
            with self.subTest(case=label):
                manager, coordinator = self.coordinator()
                controller = _DisabledReceiverProfileController()
                if gate is None:
                    controller._receiver_geometry_profile_enabled = None
                else:
                    controller._receiver_geometry_profile_enabled = gate
                manager.controller = controller
                manager.profile = current_profile
                desired_profile = (
                    PROFILE_A
                    if nonempty_desired
                    else EMPTY_INSTALLATION_PROFILE_DIGEST
                )
                command = self.command(
                    coordinator,
                    profile_digest=desired_profile,
                )
                before = manager.state()
                receiver_runtime = label == "receiver native"

                with patch.object(
                    ControllerActivationCoordinator,
                    "_uses_receiver_runtime",
                    return_value=receiver_runtime,
                ):
                    status = coordinator.activate(command)

                self.assertEqual(status["phase"], "failed")
                self.assertIsNone(status["observed_identity"])
                self.assertEqual(manager.state(), before)
                self.assertEqual(manager.mutation_count, 0)
                self.assertEqual(coordinator.state_revision, 0)
                self.assertGreaterEqual(controller.getter_calls, 1)
                self.assertEqual(controller.install_calls, 0)

    def test_disabled_gate_with_cached_active_binding_cannot_use_empty_noop(self) -> None:
        class BoundReceiver:
            @staticmethod
            def transaction_snapshot():
                return SimpleNamespace(active_binding=object())

        class Controller(_DisabledReceiverProfileController):
            def __init__(self) -> None:
                super().__init__()
                self._installation_profile_wall = SimpleNamespace(
                    receivers=(BoundReceiver(),)
                )

            def installation_profile_wall(self):
                self.getter_calls += 1
                return self._installation_profile_wall

        manager, coordinator = self.coordinator()
        controller = Controller()
        manager.controller = controller
        command = self.command(
            coordinator,
            profile_digest=EMPTY_INSTALLATION_PROFILE_DIGEST,
        )
        before = manager.state()

        status = coordinator.activate(command)

        self.assertEqual(status["phase"], "failed")
        self.assertIn("no exact clear transaction", status["error"])
        self.assertEqual(manager.state(), before)
        self.assertEqual(manager.mutation_count, 0)
        self.assertGreaterEqual(controller.getter_calls, 1)
        self.assertEqual(controller.install_calls, 0)

    def test_empty_profile_noop_authority_is_rechecked_before_snapshot(self) -> None:
        controller = _DisabledReceiverProfileController()

        def drift(phase: str, boundary: str, _activation_id: str) -> None:
            if phase == "preflighting" and boundary == "complete":
                controller._receiver_geometry_profile_enabled = True

        manager, coordinator = self.coordinator(fault_injector=drift)
        manager.controller = controller
        before = manager.state()

        status = coordinator.activate(self.command(
            coordinator,
            profile_digest=EMPTY_INSTALLATION_PROFILE_DIGEST,
        ))

        self.assertEqual(status["phase"], "failed")
        self.assertIn("changed before snapshot", status["error"])
        self.assertEqual(manager.state(), before)
        self.assertEqual(manager.mutation_count, 0)
        self.assertEqual(controller.getter_calls, 0)
        self.assertEqual(controller.install_calls, 0)

    def test_empty_profile_noop_requires_explicit_valid_current_profile(self) -> None:
        cases = (
            ("missing", object()),
            ("null", None),
            ("malformed", "not-a-sha256"),
        )
        for label, replacement in cases:
            with self.subTest(case=label):
                manager, coordinator = self.coordinator()
                controller = _DisabledReceiverProfileController()
                manager.controller = controller
                command = self.command(
                    coordinator,
                    profile_digest=EMPTY_INSTALLATION_PROFILE_DIGEST,
                )
                original_status = manager.get_current_status

                def invalid_status():
                    status = original_status()
                    if label == "missing":
                        status.pop("installation_profile_digest")
                    else:
                        status["installation_profile_digest"] = replacement
                    return status

                manager.get_current_status = invalid_status
                before = manager.state()

                status = coordinator.activate(command)

                self.assertEqual(status["phase"], "failed")
                self.assertIsNone(status["observed_identity"])
                self.assertIn("installation_profile_digest", status["error"])
                self.assertEqual(manager.state(), before)
                self.assertEqual(manager.mutation_count, 0)
                self.assertEqual(coordinator.state_revision, 0)
                self.assertEqual(controller.getter_calls, 0)
                self.assertEqual(controller.install_calls, 0)

    def test_host_only_missing_profile_constructs_activates_and_reconciles(self) -> None:
        def omit_profile(manager: _FakeManager) -> None:
            original_status = manager.get_current_status

            def host_only_status():
                status = original_status()
                status.pop("installation_profile_digest")
                return status

            manager.get_current_status = host_only_status

        manager = _FakeManager(
            self.catalog, self.initial_scene, self.initial_globals
        )
        omit_profile(manager)
        coordinator = ControllerActivationCoordinator(manager)
        command = self.command(
            coordinator,
            profile_digest=EMPTY_INSTALLATION_PROFILE_DIGEST,
        )

        active = coordinator.activate(command)

        self.assertEqual(active["phase"], "active")
        self.assertEqual(
            active["observed_identity"],
            activation_identity_from_basis(command["basis"]),
        )

        restarted_manager = _FakeManager(
            self.catalog, manager.scene, self.desired_globals
        )
        omit_profile(restarted_manager)
        restarted = ControllerActivationCoordinator(restarted_manager)

        reconciled = restarted.reconcile_durable_active(command, active)

        self.assertEqual(reconciled["phase"], "active")
        self.assertEqual(
            reconciled["observed_identity"],
            activation_identity_from_basis(command["basis"]),
        )
        self.assertFalse(reconciled["rollback"]["available"])
        self.assertEqual(restarted_manager.mutation_count, 0)

    def test_host_only_fallback_requires_every_profile_seam_absent(self) -> None:
        cases = (
            ("controller absent", None, True),
            ("plain controller", SimpleNamespace(), True),
            (
                "getter none",
                SimpleNamespace(installation_profile_wall=None),
                False,
            ),
            (
                "installer none",
                SimpleNamespace(install_installation_profile=None),
                False,
            ),
            (
                "getter only",
                SimpleNamespace(installation_profile_wall=lambda: None),
                False,
            ),
            (
                "installer only",
                SimpleNamespace(install_installation_profile=lambda _item: None),
                False,
            ),
            (
                "rollout marker",
                SimpleNamespace(_receiver_geometry_profile_enabled=False),
                False,
            ),
            (
                "cached-wall marker",
                SimpleNamespace(_installation_profile_wall=None),
                False,
            ),
        )
        for label, controller, host_only in cases:
            with self.subTest(case=label):
                manager = _FakeManager(
                    self.catalog, self.initial_scene, self.initial_globals
                )
                if controller is not None:
                    manager.controller = controller
                original_status = manager.get_current_status

                def missing_profile_status():
                    status = original_status()
                    status.pop("installation_profile_digest")
                    return status

                manager.get_current_status = missing_profile_status

                if host_only:
                    coordinator = ControllerActivationCoordinator(manager)
                    self.assertEqual(
                        coordinator.controller_status()["active_identity"][
                            "installation_profile_digest"
                        ],
                        EMPTY_INSTALLATION_PROFILE_DIGEST,
                    )
                else:
                    with self.assertRaisesRegex(
                        ValueError, "installation_profile_digest is missing"
                    ):
                        ControllerActivationCoordinator(manager)

    def test_empty_profile_noop_rollback_revalidates_profile_authority(self) -> None:
        cases = (
            ("missing", object()),
            ("null", None),
            ("malformed", "not-a-sha256"),
        )
        for label, replacement in cases:
            with self.subTest(case=label):
                invalid = [False]

                def inject(phase: str, boundary: str, _activation_id: str) -> None:
                    if phase == "applying" and boundary == "brightness":
                        invalid[0] = True
                        raise RuntimeError("injected authority loss after mutation")

                manager, coordinator = self.coordinator(fault_injector=inject)
                controller = _DisabledReceiverProfileController()
                manager.controller = controller
                original_status = manager.get_current_status

                def invalid_status():
                    status = original_status()
                    if invalid[0]:
                        if label == "missing":
                            status.pop("installation_profile_digest")
                        else:
                            status["installation_profile_digest"] = replacement
                    return status

                manager.get_current_status = invalid_status

                status = coordinator.activate(self.command(
                    coordinator,
                    profile_digest=EMPTY_INSTALLATION_PROFILE_DIGEST,
                ))

                self.assertEqual(status["phase"], "failed")
                self.assertEqual(status["rollback"]["result"], "failed")
                self.assertIn(
                    "installation_profile_digest", status["rollback"]["error"]
                )
                self.assertIsNone(status["observed_identity"])
                self.assertEqual(coordinator.state_revision, 1)
                self.assertEqual(controller.getter_calls, 0)
                self.assertEqual(controller.install_calls, 0)

    def test_empty_profile_noop_snapshot_compensates_late_host_failure(self) -> None:
        def inject(phase: str, boundary: str, _activation_id: str) -> None:
            if phase == "applying" and boundary == "brightness":
                raise RuntimeError("injected late host mutation failure")

        manager, coordinator = self.coordinator(fault_injector=inject)
        controller = _DisabledReceiverProfileController()
        manager.controller = controller
        before = manager.state()

        status = coordinator.activate(self.command(
            coordinator,
            profile_digest=EMPTY_INSTALLATION_PROFILE_DIGEST,
        ))

        self.assertEqual(status["phase"], "rolled_back")
        self.assertEqual(status["rollback"]["result"], "succeeded")
        self.assertTrue(status["telemetry"]["complete"])
        self.assertTrue(status["telemetry"]["fresh"])
        self.assertEqual(manager.state(), before)
        self.assertEqual(coordinator.state_revision, 1)
        self.assertEqual(controller.getter_calls, 0)
        self.assertEqual(controller.install_calls, 0)

    def test_receiver_activation_rejects_stale_preexisting_evidence(self) -> None:
        desired_revision = self.desired_scene["revision"]

        class ReceiverEvidenceManager(_FakeManager):
            def get_current_status(self) -> dict:
                status = super().get_current_status()
                status["receiver_hybrid"] = _receiver_status(
                    scene_revision=desired_revision
                )
                return status

        manager = ReceiverEvidenceManager(
            self.catalog, self.initial_scene, self.initial_globals
        )
        coordinator = ControllerActivationCoordinator(
            manager,
            observation_timeout=0,
            observation_interval=0,
        )
        command = self.command(coordinator)
        before = manager.state()

        # Exercise the receiver-native observation branch with an otherwise
        # valid command. The receiver sample was already correlated to the
        # desired revision before mutation, and its publication generation and
        # success time remain unchanged after apply.
        with patch.object(
            ControllerActivationCoordinator,
            "_uses_receiver_runtime",
            return_value=True,
        ):
            status = coordinator.activate(command)

        self.assertEqual(status["phase"], "timed_out")
        self.assertEqual(status["rollback"]["result"], "succeeded")
        self.assertEqual(manager.state(), before)
        self.assertIn("not freshly observed", status["error"])

    def test_receiver_restart_reconciliation_rejects_cached_health(self) -> None:
        manager, prior = self.coordinator()
        command = self.command(prior)
        durable_active = prior.activate(command)
        restarted_manager = _FakeManager(
            self.catalog, self.desired_scene, self.desired_globals
        )
        restarted_manager.profile = PROFILE_A
        original_get_status = restarted_manager.get_current_status

        def cached_health_status() -> dict:
            status = original_get_status()
            status["receiver_hybrid"] = {"healthy": True}
            return status

        restarted_manager.get_current_status = cached_health_status
        restarted = ControllerActivationCoordinator(restarted_manager)

        with patch.object(
            ControllerActivationCoordinator,
            "_uses_receiver_runtime",
            return_value=True,
        ):
            reconciled = restarted.reconcile_durable_active(
                command, durable_active
            )

        self.assertEqual(reconciled["phase"], "failed")
        self.assertIn("does not match", reconciled["error"])
        self.assertFalse(reconciled["rollback"]["available"])
        self.assertEqual(restarted_manager.mutation_count, 0)

    def test_restart_reconciliation_never_defaults_missing_profile_to_empty(self) -> None:
        _manager, prior = self.coordinator()
        command = self.command(prior)
        durable_active = prior.activate(command)
        restarted_manager = _FakeManager(
            self.catalog, self.desired_scene, self.desired_globals
        )
        restarted_manager.profile = PROFILE_A
        restarted_manager.controller = _DisabledReceiverProfileController()
        restarted = ControllerActivationCoordinator(restarted_manager)
        original_status = restarted_manager.get_current_status

        def missing_profile_status():
            status = original_status()
            status.pop("installation_profile_digest")
            return status

        restarted_manager.get_current_status = missing_profile_status

        with self.assertRaisesRegex(
            ValueError, "installation_profile_digest is missing"
        ):
            restarted.reconcile_durable_active(command, durable_active)

        self.assertEqual(restarted_manager.mutation_count, 0)

    def test_receiver_evidence_requires_exact_correlation_and_advancement(self) -> None:
        receiver_scene = _managed_receiver_scene(
            revision=self.desired_scene["revision"]
        )
        evidence_status = {
            "receiver_hybrid": _receiver_status(
                scene_revision=receiver_scene["revision"],
                scene=receiver_scene,
            )
        }
        evidence = ControllerActivationCoordinator._receiver_activation_evidence(
            evidence_status, receiver_scene, PROFILE_A
        )
        self.assertIsNotNone(evidence)
        self.assertFalse(
            ControllerActivationCoordinator._receiver_evidence_advanced(
                evidence, evidence
            )
        )

        advanced_status = deepcopy(evidence_status)
        advanced_status["receiver_hybrid"]["publisher"]["generation"] += 1
        advanced = ControllerActivationCoordinator._receiver_activation_evidence(
            advanced_status, receiver_scene, PROFILE_A
        )
        self.assertTrue(
            ControllerActivationCoordinator._receiver_evidence_advanced(
                evidence, advanced
            )
        )

        cached_health_only = {"receiver_hybrid": {"healthy": True}}
        self.assertIsNone(
            ControllerActivationCoordinator._receiver_activation_evidence(
                cached_health_only, receiver_scene, PROFILE_A
            )
        )
        mismatches = {
            "source scene revision": (
                "source_scene_revision", receiver_scene["revision"] - 1
            ),
            "publisher binding revision": (
                "publisher.binding.scene_revision", receiver_scene["revision"] - 1
            ),
            "bundle": ("driver.bundle_digest", "0" * 64),
            "payload": ("driver.payload_digest", "1" * 64),
            "parameters": (
                "driver.effective_parameters", {"brightness": 0.41}
            ),
            "context": ("driver.context_digest", "2" * 64),
            "profile": ("driver.installation_profile_digest", "3" * 64),
            "parameter digest": ("driver.parameter_digest", None),
        }
        for label, (path, value) in mismatches.items():
            with self.subTest(label=label):
                mismatched = deepcopy(evidence_status)
                target = mismatched["receiver_hybrid"]
                parts = path.split(".")
                for part in parts[:-1]:
                    target = target[part]
                target[parts[-1]] = value
                self.assertIsNone(
                    ControllerActivationCoordinator._receiver_activation_evidence(
                        mismatched, receiver_scene, PROFILE_A
                    )
                )

        self.assertIsNone(
            ControllerActivationCoordinator._receiver_activation_evidence(
                evidence_status, receiver_scene, "4" * 64
            )
        )

    def test_missing_receiver_transaction_authority_fails_preflight(self) -> None:
        class PartialController:
            @staticmethod
            def installation_profile_wall():
                return SimpleNamespace(receivers=())

        manager, coordinator = self.coordinator()
        manager.controller = PartialController()
        before = manager.state()

        status = coordinator.activate(self.command(coordinator))

        self.assertEqual(status["phase"], "failed")
        self.assertIn("lacks exact transaction authority", status["error"])
        self.assertEqual(manager.state(), before)
        self.assertEqual(manager.mutation_count, 0)

    def test_host_selected_but_receiver_stale_never_reports_active(self) -> None:
        stale_binding = SimpleNamespace(
            profile_id="b" * 64,
            payload_digest="c" * 64,
        )

        class Candidate:
            profile_id = PROFILE_A

            @staticmethod
            def binding_for(receiver_id):
                return SimpleNamespace(
                    profile_id=PROFILE_A,
                    payload_digest=f"{receiver_id + 1:064x}",
                )

        class Receiver:
            def __init__(self):
                self.binding = stale_binding

            def transaction_snapshot(self):
                return SimpleNamespace(active_binding=self.binding)

            def compensate_profile(self, snapshot):
                self.binding = snapshot.active_binding

        class Wall:
            def __init__(self):
                self.receivers = tuple(Receiver() for _ in range(5))

            @staticmethod
            def status():
                return SimpleNamespace(
                    healthy=True,
                    active_profile_id=stale_binding.profile_id,
                )

        class StaleController:
            def __init__(self):
                self.wall = Wall()
                self.install_calls = 0

            def installation_profile_wall(self):
                return self.wall

            def install_installation_profile(self, candidate):
                self.install_calls += 1
                return SimpleNamespace(
                    success=True,
                    profile_id=candidate.profile_id,
                    error=None,
                )

        manager = _FakeManager(
            self.catalog, self.initial_scene, self.initial_globals
        )
        manager.profile = PROFILE_A
        manager.controller = StaleController()
        manager.resolve_installation_profile_candidate = lambda _digest: Candidate()
        coordinator = ControllerActivationCoordinator(manager)
        before = manager.state()

        status = coordinator.activate(self.command(coordinator))

        self.assertEqual(status["phase"], "failed")
        self.assertIn("identity proof is stale", status["error"])
        self.assertNotEqual(status["phase"], "active")
        self.assertEqual(manager.state(), before)
        self.assertGreaterEqual(manager.controller.install_calls, 1)

    def test_nonempty_receiver_binding_cannot_transition_to_empty(self) -> None:
        class BoundReceiver:
            @staticmethod
            def transaction_snapshot():
                return SimpleNamespace(active_binding=object())

        class BoundReceiverController:
            @staticmethod
            def installation_profile_wall():
                return SimpleNamespace(receivers=(BoundReceiver(),))

        manager = _FakeManager(
            self.catalog, self.initial_scene, self.initial_globals
        )
        manager.profile = PROFILE_A
        coordinator = ControllerActivationCoordinator(manager)
        manager.controller = BoundReceiverController()
        command = self.command(
            coordinator,
            profile_digest=EMPTY_INSTALLATION_PROFILE_DIGEST,
        )
        before = manager.state()

        status = coordinator.activate(command)

        self.assertEqual(status["phase"], "failed")
        self.assertIn("no exact clear transaction", status["error"])
        self.assertEqual(manager.state(), before)
        self.assertEqual(manager.mutation_count, 0)
        self.assertEqual(coordinator.state_revision, 0)

    def test_durable_terminal_receipt_is_not_replayed_after_restart(self) -> None:
        class Channel:
            def __init__(self, command: dict) -> None:
                self.command = command
                self.status = {
                    "activation_id": command["activation_id"],
                    "phase": "failed",
                }

            def list_activation_commands(self):
                return [self.command]

            def read_activation_status(self, _activation_id):
                return self.status

            @staticmethod
            def read_activation_cancel(_activation_id):
                return None

            @staticmethod
            def read_activation_rollback(_activation_id):
                return None

        manager, coordinator = self.coordinator()
        channel = Channel(self.command(coordinator))

        processed = process_activation_commands(channel, coordinator)

        self.assertEqual(processed, 0)
        self.assertEqual(manager.mutation_count, 0)
        self.assertIsNone(coordinator.get(channel.command["activation_id"]))

    def test_current_session_web_queued_receipt_executes_once(self) -> None:
        """The web-owned queued receipt is a handoff, not restart evidence."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            channel = FileControlChannel(
                str(root / "control.json"),
                str(root / "status.json"),
                str(root / "activations"),
            )
            manager, coordinator = self.coordinator(
                status_sink=channel.write_activation_status
            )
            command = self.command(coordinator)
            channel.write_activation_status(coordinator._new_status(command))
            channel.enqueue_activation(command)

            self.assertEqual(process_activation_commands(channel, coordinator), 1)
            terminal = channel.read_activation_status(command["activation_id"])
            self.assertEqual(terminal["phase"], "active")
            self.assertEqual(terminal["controller"]["state_revision_after"], 1)
            mutations_after_first = manager.mutation_count
            self.assertGreater(mutations_after_first, 0)
            self.assertEqual(process_activation_commands(channel, coordinator), 0)
            self.assertEqual(manager.mutation_count, mutations_after_first)

    def test_prior_session_web_queued_receipt_is_never_replayed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            channel = FileControlChannel(
                str(root / "control.json"),
                str(root / "status.json"),
                str(root / "activations"),
            )
            manager, prior = self.coordinator(
                status_sink=channel.write_activation_status
            )
            command = self.command(prior)
            channel.write_activation_status(prior._new_status(command))
            channel.enqueue_activation(command)
            restarted = ControllerActivationCoordinator(
                manager, status_sink=channel.write_activation_status
            )

            self.assertEqual(process_activation_commands(channel, restarted), 0)
            terminal = channel.read_activation_status(command["activation_id"])
            self.assertEqual(terminal["phase"], "failed")
            self.assertIn("current controller state", terminal["error"])
            self.assertEqual(manager.mutation_count, 0)

    def test_queued_handoff_rejects_drift_and_any_mutation_evidence(self) -> None:
        cases = (
            "basis_mismatch",
            "state_revision_after",
            "observed_identity",
            "telemetry",
            "rollback_authority",
            "prior_error",
            "controller_revision_drift",
            "controller_identity_drift",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                channel = FileControlChannel(
                    str(root / "control.json"),
                    str(root / "status.json"),
                    str(root / "activations"),
                )
                manager, coordinator = self.coordinator(
                    status_sink=channel.write_activation_status
                )
                command = self.command(coordinator)
                queued = coordinator._new_status(command)
                if case == "basis_mismatch":
                    queued["basis_digest"] = "f" * 64
                elif case == "state_revision_after":
                    queued["controller"]["state_revision_after"] = 1
                elif case == "observed_identity":
                    queued["observed_identity"] = deepcopy(
                        queued["normalized_identity"]
                    )
                elif case == "telemetry":
                    queued["telemetry"]["complete"] = True
                elif case == "rollback_authority":
                    queued["rollback"].update(
                        available=True, snapshot_id="unexpected-snapshot"
                    )
                elif case == "prior_error":
                    queued["error"] = "unexpected prior work"
                channel.write_activation_status(queued)
                channel.enqueue_activation(command)
                if case == "controller_revision_drift":
                    coordinator._state_revision += 1
                elif case == "controller_identity_drift":
                    coordinator._active_identity[
                        "installation_profile_digest"
                    ] = "f" * 64

                self.assertEqual(
                    process_activation_commands(channel, coordinator), 0
                )
                terminal = channel.read_activation_status(
                    command["activation_id"]
                )
                self.assertEqual(terminal["phase"], "failed")
                self.assertIn("rejected before mutation", terminal["error"])
                self.assertFalse(terminal["rollback"]["available"])
                self.assertEqual(
                    terminal["rollback"]["error"],
                    "no rollback authority was acquired before rejection",
                )
                self.assertEqual(manager.mutation_count, 0)

    def test_advanced_durable_phase_is_restart_closed_not_handed_off(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            channel = FileControlChannel(
                str(root / "control.json"),
                str(root / "status.json"),
                str(root / "activations"),
            )
            manager, coordinator = self.coordinator(
                status_sink=channel.write_activation_status
            )
            command = self.command(coordinator)
            advanced = coordinator._new_status(command)
            advanced["phase"] = "preflighting"
            channel.write_activation_status(advanced)
            channel.enqueue_activation(command)

            self.assertEqual(process_activation_commands(channel, coordinator), 0)
            terminal = channel.read_activation_status(command["activation_id"])
            self.assertEqual(terminal["phase"], "failed")
            self.assertIn("controller restarted", terminal["error"])
            self.assertFalse(terminal["rollback"]["available"])
            self.assertEqual(manager.mutation_count, 0)

    def test_missing_queued_session_is_invalid_and_never_mutates(self) -> None:
        manager, coordinator = self.coordinator()
        command = self.command(coordinator)
        queued = coordinator._new_status(command)
        del queued["controller"]["session_id"]

        with self.assertRaisesRegex(ValueError, "session_id"):
            coordinator.queue_durable_handoff(command, queued)
        self.assertEqual(manager.mutation_count, 0)
        self.assertIsNone(coordinator.get(command["activation_id"]))

    def test_stale_session_replay_fails_once_then_new_restart_skips_it(self) -> None:
        class Channel:
            def __init__(self, command: dict) -> None:
                self.command = command
                self.status = None
                self.writes = 0

            def list_activation_commands(self):
                return [self.command]

            def read_activation_status(self, _activation_id):
                return self.status

            def write_activation_status(self, status):
                self.status = deepcopy(status)
                self.writes += 1

            @staticmethod
            def read_activation_cancel(_activation_id):
                return None

            @staticmethod
            def read_activation_rollback(_activation_id):
                return None

        old_manager, old = self.coordinator()
        command = self.command(old)
        channel = Channel(command)
        new_manager = _FakeManager(
            self.catalog, self.initial_scene, self.initial_globals
        )
        restarted = ControllerActivationCoordinator(
            new_manager, status_sink=channel.write_activation_status
        )

        self.assertEqual(process_activation_commands(channel, restarted), 0)
        self.assertEqual(channel.status["phase"], "failed")
        self.assertIn("session changed", channel.status["error"])
        self.assertEqual(new_manager.mutation_count, 0)
        first_writes = channel.writes

        second_manager = _FakeManager(
            self.catalog, self.initial_scene, self.initial_globals
        )
        second_restart = ControllerActivationCoordinator(
            second_manager, status_sink=channel.write_activation_status
        )
        self.assertEqual(process_activation_commands(channel, second_restart), 0)
        self.assertEqual(channel.writes, first_writes)
        self.assertEqual(second_manager.mutation_count, 0)

    def test_restart_closes_nonterminal_receipt_without_replaying_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            channel = FileControlChannel(
                str(root / "control.json"),
                str(root / "status.json"),
                str(root / "activations"),
            )

            def crash_after_apply(
                phase: str, boundary: str, _activation_id: str
            ) -> None:
                if phase == "observing" and boundary == "poll":
                    raise KeyboardInterrupt("simulated controller crash")

            manager, prior = self.coordinator(
                status_sink=channel.write_activation_status,
                fault_injector=crash_after_apply,
            )
            command = self.command(prior)
            channel.enqueue_activation(command)
            with self.assertRaises(KeyboardInterrupt):
                prior.activate(command)
            durable = channel.read_activation_status(command["activation_id"])
            self.assertEqual(durable["phase"], "observing")
            mutations_after_crash = manager.mutation_count
            self.assertEqual(manager.scene, self.desired_scene)

            restarted = ControllerActivationCoordinator(
                manager, status_sink=channel.write_activation_status
            )
            processed = process_activation_commands(channel, restarted)

            self.assertEqual(processed, 0)
            self.assertEqual(manager.mutation_count, mutations_after_crash)
            terminal = channel.read_activation_status(command["activation_id"])
            self.assertEqual(terminal["phase"], "failed")
            self.assertFalse(terminal["rollback"]["available"])
            self.assertIn("controller restarted", terminal["error"])
            self.assertIn("matches", terminal["error"])

    def test_restart_recovers_persistent_postmutation_publication_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            channel = FileControlChannel(
                str(root / "control.json"),
                str(root / "status.json"),
                str(root / "activations"),
            )
            publication_failed = False

            def fail_after_applying(status: dict) -> None:
                nonlocal publication_failed
                if status["phase"] in {
                    "observing",
                    "rolling_back",
                    "rolled_back",
                    "failed",
                    "timed_out",
                }:
                    publication_failed = True
                    raise OSError("persistent status disk failure")
                channel.write_activation_status(status)

            manager, prior = self.coordinator(status_sink=fail_after_applying)
            command = self.command(prior)
            before = manager.state()
            channel.enqueue_activation(command)

            in_memory = prior.activate(command)

            self.assertTrue(publication_failed)
            self.assertEqual(in_memory["phase"], "rolled_back")
            self.assertEqual(in_memory["rollback"]["result"], "succeeded")
            self.assertEqual(manager.state(), before)
            durable = channel.read_activation_status(command["activation_id"])
            self.assertEqual(durable["phase"], "applying")
            mutations_after_compensation = manager.mutation_count

            restarted = ControllerActivationCoordinator(
                manager, status_sink=channel.write_activation_status
            )
            processed = process_activation_commands(channel, restarted)

            self.assertEqual(processed, 0)
            self.assertEqual(manager.mutation_count, mutations_after_compensation)
            terminal = channel.read_activation_status(command["activation_id"])
            self.assertEqual(terminal["phase"], "failed")
            self.assertFalse(terminal["rollback"]["available"])
            self.assertIn("controller restarted", terminal["error"])

    def test_active_state_persists_and_reconciles_without_stale_rollback(self) -> None:
        class Channel:
            def __init__(self, command: dict, status: dict) -> None:
                self.command = command
                self.status = deepcopy(status)

            def list_activation_commands(self):
                return [self.command]

            def read_activation_status(self, _activation_id):
                return self.status

            def write_activation_status(self, status):
                self.status = deepcopy(status)

            @staticmethod
            def read_activation_cancel(_activation_id):
                return None

            @staticmethod
            def read_activation_rollback(_activation_id):
                return None

        manager, coordinator = self.coordinator()
        command = self.command(coordinator)
        active = coordinator.activate(command)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            persist_controller_restart_state(
                manager,
                coordinator,
                presets_dir=root / "presets",
                state_path=root / "saved.json",
            )
            saved = load_saved_state(root / "saved.json")

        self.assertEqual(saved["scene"], self.desired_scene)
        self.assertEqual(saved["installation_profile_digest"], PROFILE_A)
        self.assertEqual(saved["brightness"], 96)
        self.assertEqual(saved["target_fps"], 90)

        restarted_manager = _FakeManager(
            self.catalog, saved["scene"], self.desired_globals
        )
        restarted_manager.profile = saved["installation_profile_digest"]
        channel = Channel(command, active)
        restarted = ControllerActivationCoordinator(
            restarted_manager, status_sink=channel.write_activation_status
        )

        self.assertEqual(process_activation_commands(channel, restarted), 0)
        self.assertEqual(channel.status["phase"], "active")
        self.assertEqual(
            channel.status["controller"]["session_id"], restarted.session_id
        )
        self.assertFalse(channel.status["rollback"]["available"])
        self.assertEqual(restarted_manager.mutation_count, 0)

    def test_file_channel_accepts_exact_active_restart_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            channel = FileControlChannel(
                str(root / "control.json"),
                str(root / "status.json"),
                str(root / "activations"),
            )
            prior_manager, prior = self.coordinator(
                status_sink=channel.write_activation_status
            )
            command = self.command(prior)
            channel.enqueue_activation(command)
            active = prior.activate(command)

            restarted_manager = _FakeManager(
                self.catalog, self.desired_scene, self.desired_globals
            )
            restarted_manager.profile = PROFILE_A
            restarted = ControllerActivationCoordinator(
                restarted_manager, status_sink=channel.write_activation_status
            )

            self.assertEqual(process_activation_commands(channel, restarted), 0)
            reconciled = channel.read_activation_status(command["activation_id"])
            self.assertEqual(reconciled["phase"], "active")
            self.assertEqual(
                reconciled["controller"]["session_id"], restarted.session_id
            )
            self.assertNotEqual(
                reconciled["controller"]["session_id"],
                active["controller"]["session_id"],
            )
            self.assertFalse(reconciled["rollback"]["available"])
            self.assertIsNone(reconciled["rollback"]["snapshot_id"])
            self.assertEqual(restarted_manager.mutation_count, 0)

    def test_file_channel_rejects_stale_active_restart_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            channel = FileControlChannel(
                str(root / "control.json"),
                str(root / "status.json"),
                str(root / "activations"),
            )
            _prior_manager, prior = self.coordinator(
                status_sink=channel.write_activation_status
            )
            command = self.command(prior)
            channel.enqueue_activation(command)
            active = prior.activate(command)

            restarted_manager = _FakeManager(
                self.catalog, self.initial_scene, self.initial_globals
            )
            restarted = ControllerActivationCoordinator(
                restarted_manager, status_sink=channel.write_activation_status
            )

            self.assertEqual(process_activation_commands(channel, restarted), 0)
            reconciled = channel.read_activation_status(command["activation_id"])
            self.assertEqual(reconciled["phase"], "failed")
            self.assertEqual(
                reconciled["controller"]["session_id"], restarted.session_id
            )
            self.assertNotEqual(
                reconciled["controller"]["session_id"],
                active["controller"]["session_id"],
            )
            self.assertIn("does not match", reconciled["error"])
            self.assertFalse(reconciled["rollback"]["available"])
            self.assertEqual(restarted_manager.mutation_count, 0)

    def test_powered_off_active_state_reconciles_with_restored_selected_scene(self) -> None:
        class Channel:
            def __init__(self, command: dict, status: dict) -> None:
                self.command = command
                self.status = deepcopy(status)

            def list_activation_commands(self):
                return [self.command]

            def read_activation_status(self, _activation_id):
                return self.status

            def write_activation_status(self, status):
                self.status = deepcopy(status)

            @staticmethod
            def read_activation_cancel(_activation_id):
                return None

            @staticmethod
            def read_activation_rollback(_activation_id):
                return None

        manager, coordinator = self.coordinator()
        powered_off = deepcopy(self.desired_globals)
        powered_off["output"]["power"] = False
        command = self.command(coordinator, globals_state=powered_off)
        active = coordinator.activate(command)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            persist_controller_restart_state(
                manager,
                coordinator,
                presets_dir=root / "presets",
                state_path=root / "saved.json",
            )
            saved = load_saved_state(root / "saved.json")

        self.assertFalse(saved["power"])
        self.assertEqual(saved["scene"], self.desired_scene)
        restarted_manager = _FakeManager(
            self.catalog, None, powered_off
        )
        restarted_manager.profile = saved["installation_profile_digest"]
        channel = Channel(command, active)
        restarted = ControllerActivationCoordinator(
            restarted_manager,
            status_sink=channel.write_activation_status,
            restored_selected_scene=saved["scene"],
        )

        self.assertEqual(process_activation_commands(channel, restarted), 0)
        self.assertEqual(channel.status["phase"], "active")
        self.assertEqual(
            restarted.controller_status()["scene_state"], self.desired_scene
        )
        self.assertFalse(channel.status["rollback"]["available"])
        self.assertFalse(restarted_manager.is_running)
        self.assertEqual(restarted_manager.mutation_count, 0)

    def test_stale_active_receipt_fails_if_restart_state_did_not_restore(self) -> None:
        class Channel:
            def __init__(self, command: dict, status: dict) -> None:
                self.command = command
                self.status = deepcopy(status)

            def list_activation_commands(self):
                return [self.command]

            def read_activation_status(self, _activation_id):
                return self.status

            def write_activation_status(self, status):
                self.status = deepcopy(status)

            @staticmethod
            def read_activation_cancel(_activation_id):
                return None

            @staticmethod
            def read_activation_rollback(_activation_id):
                return None

        prior_manager, prior = self.coordinator()
        command = self.command(prior)
        active = prior.activate(command)
        restarted_manager = _FakeManager(
            self.catalog, self.initial_scene, self.initial_globals
        )
        channel = Channel(command, active)
        restarted = ControllerActivationCoordinator(
            restarted_manager, status_sink=channel.write_activation_status
        )

        self.assertEqual(process_activation_commands(channel, restarted), 0)
        self.assertEqual(channel.status["phase"], "failed")
        self.assertIn("does not match", channel.status["error"])
        self.assertEqual(restarted_manager.mutation_count, 0)

    def test_observation_timeout_compensates_and_reports_timeout(self) -> None:
        manager, coordinator = self.coordinator(
            observation_timeout=0,
            observer=lambda command: {
                "identity": activation_identity_from_basis(command["basis"]),
                "complete": False,
                "fresh": False,
            },
        )
        before = manager.state()

        status = coordinator.activate(self.command(coordinator))

        self.assertEqual(status["phase"], "timed_out")
        self.assertEqual(status["rollback"]["result"], "succeeded")
        self.assertEqual(manager.state(), before)
        self.assertEqual(coordinator.state_revision, 1)
        normalize_scene_activation_status(status)

    def test_cancel_is_effective_while_queued_or_preflighting(self) -> None:
        manager, coordinator = self.coordinator()
        queued = self.command(coordinator)
        coordinator.queue(queued)
        cancelled = coordinator.cancel(queued["activation_id"])
        self.assertEqual(cancelled["phase"], "failed")
        self.assertEqual(manager.mutation_count, 0)
        normalize_scene_activation_status(cancelled)

        manager, coordinator = self.coordinator()
        manager.preflight_entered = threading.Event()
        manager.preflight_release = threading.Event()
        command = self.command(coordinator)
        coordinator.queue(command)
        result: list[dict] = []
        worker = threading.Thread(
            target=lambda: result.append(coordinator.execute(command["activation_id"]))
        )
        worker.start()
        self.assertTrue(manager.preflight_entered.wait(timeout=1))
        coordinator.cancel(command["activation_id"])
        manager.preflight_release.set()
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0]["phase"], "failed")
        self.assertEqual(manager.mutation_count, 0)
        normalize_scene_activation_status(result[0])

    def test_file_channel_cancel_interrupts_inflight_preflight_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            channel = FileControlChannel(
                str(root / "control.json"),
                str(root / "status.json"),
                str(root / "activations"),
            )
            manager = _FakeManager(
                self.catalog, self.initial_scene, self.initial_globals
            )
            manager.preflight_entered = threading.Event()
            manager.preflight_release = threading.Event()
            coordinator = ControllerActivationCoordinator(
                manager,
                status_sink=channel.write_activation_status,
                cancel_probe=lambda activation_id: (
                    channel.read_activation_cancel(activation_id) is not None
                ),
            )
            command = self.command(coordinator)
            channel.enqueue_activation(command)
            result: list[int] = []
            worker = threading.Thread(
                target=lambda: result.append(
                    process_activation_commands(channel, coordinator)
                )
            )
            worker.start()
            self.assertTrue(manager.preflight_entered.wait(timeout=1))
            channel.request_activation_cancel(command["activation_id"])
            manager.preflight_release.set()
            worker.join(timeout=2)

            self.assertFalse(worker.is_alive())
            self.assertEqual(result, [0])
            status = channel.read_activation_status(command["activation_id"])
            self.assertEqual(status["phase"], "failed")
            self.assertIn("cancelled before mutation", status["error"])
            self.assertEqual(manager.mutation_count, 0)
            cancel_result = channel.read_activation_cancel_result(
                command["activation_id"]
            )
            self.assertEqual(cancel_result["outcome"], "succeeded")

            self.assertEqual(process_activation_commands(channel, coordinator), 0)
            self.assertEqual(
                channel.read_activation_cancel_result(command["activation_id"]),
                cancel_result,
            )

    def test_guarded_manual_rollback_restores_and_advances_again(self) -> None:
        history: list[dict] = []
        manager, coordinator = self.coordinator(status_sink=history.append)
        before = manager.state()
        active = coordinator.activate(self.command(coordinator))

        restored = coordinator.rollback(
            active["activation_id"],
            snapshot_id=active["rollback"]["snapshot_id"],
            expected_session_id=coordinator.session_id,
            expected_state_revision=coordinator.state_revision,
        )

        self.assertEqual(restored["phase"], "rolled_back")
        self.assertEqual(restored["rollback"]["result"], "succeeded")
        self.assertEqual(manager.state(), before)
        self.assertEqual(coordinator.state_revision, 2)
        self.assertEqual(restored["controller"]["state_revision_after"], 2)
        self.assert_status_history_valid(history)

    def test_manual_rollback_persists_restored_state_before_return(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / "saved.json"
            manager = _FakeManager(
                self.catalog, self.initial_scene, self.initial_globals
            )
            holder: list[ControllerActivationCoordinator] = []

            def commit() -> None:
                persist_controller_restart_state(
                    manager,
                    holder[0],
                    presets_dir=root / "presets",
                    state_path=state_path,
                )

            coordinator = ControllerActivationCoordinator(
                manager, commit_callback=commit
            )
            holder.append(coordinator)
            active = coordinator.activate(self.command(coordinator))
            self.assertEqual(load_saved_state(state_path)["scene"], self.desired_scene)

            restored = coordinator.rollback(
                active["activation_id"],
                snapshot_id=active["rollback"]["snapshot_id"],
                expected_session_id=coordinator.session_id,
                expected_state_revision=coordinator.state_revision,
            )
            saved = load_saved_state(state_path)

            self.assertEqual(restored["phase"], "rolled_back")
            self.assertEqual(saved["scene"], self.initial_scene)
            self.assertEqual(
                saved["installation_profile_digest"],
                EMPTY_INSTALLATION_PROFILE_DIGEST,
            )
            self.assertEqual(saved["brightness"], 200)
            self.assertEqual(saved["target_fps"], 120)

    def test_durable_rollback_race_is_rejected_once_and_never_replayed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            channel = FileControlChannel(
                str(root / "control.json"),
                str(root / "status.json"),
                str(root / "activations"),
            )
            manager, coordinator = self.coordinator(
                status_sink=channel.write_activation_status
            )
            command = self.command(coordinator)
            channel.enqueue_activation(command)
            self.assertEqual(process_activation_commands(channel, coordinator), 1)
            active = channel.read_activation_status(command["activation_id"])
            rollback = channel.request_activation_rollback(
                command["activation_id"],
                snapshot_id=active["rollback"]["snapshot_id"],
                expected_controller_session_id=coordinator.session_id,
                expected_controller_state_revision=coordinator.state_revision,
            )
            coordinator.note_legacy_mutation()

            self.assertEqual(process_activation_commands(channel, coordinator), 0)
            result = channel.read_activation_rollback_result(
                command["activation_id"]
            )
            self.assertEqual(result["request_id"], rollback["request_id"])
            self.assertEqual(result["outcome"], "rejected")
            self.assertIn("revision", result["error"])
            mutation_count = manager.mutation_count

            self.assertEqual(process_activation_commands(channel, coordinator), 0)
            self.assertEqual(manager.mutation_count, mutation_count)
            self.assertEqual(
                channel.read_activation_rollback_result(command["activation_id"]),
                result,
            )

    def test_restart_repairs_result_after_rollback_status_was_committed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            channel = FileControlChannel(
                str(root / "control.json"),
                str(root / "status.json"),
                str(root / "activations"),
            )
            manager, coordinator = self.coordinator(
                status_sink=channel.write_activation_status
            )
            command = self.command(coordinator)
            channel.enqueue_activation(command)
            self.assertEqual(process_activation_commands(channel, coordinator), 1)
            active = channel.read_activation_status(command["activation_id"])
            rollback = channel.request_activation_rollback(
                command["activation_id"],
                snapshot_id=active["rollback"]["snapshot_id"],
                expected_controller_session_id=coordinator.session_id,
                expected_controller_state_revision=coordinator.state_revision,
            )
            original_writer = channel.write_activation_rollback_result
            channel.write_activation_rollback_result = (
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    OSError("injected result publication crash")
                )
            )
            with self.assertRaisesRegex(OSError, "publication crash"):
                process_activation_commands(channel, coordinator)
            self.assertEqual(
                channel.read_activation_status(command["activation_id"])["phase"],
                "rolled_back",
            )
            self.assertIsNone(
                channel.read_activation_rollback_result(command["activation_id"])
            )

            channel.write_activation_rollback_result = original_writer
            self.assertEqual(process_activation_commands(channel, coordinator), 0)
            repaired = channel.read_activation_rollback_result(
                command["activation_id"]
            )
            self.assertEqual(repaired["outcome"], "succeeded")
            restarted_manager, restarted = self.coordinator()
            self.assertEqual(process_activation_commands(channel, restarted), 0)
            result = channel.read_activation_rollback_result(
                command["activation_id"]
            )
            self.assertEqual(result["request_id"], rollback["request_id"])
            self.assertEqual(result["outcome"], "succeeded")
            self.assertEqual(result, repaired)
            self.assertEqual(restarted_manager.mutation_count, 0)

    def test_later_activation_invalidates_older_rollback_authority(self) -> None:
        manager, coordinator = self.coordinator()
        first = coordinator.activate(self.command(coordinator))
        first_snapshot_id = first["rollback"]["snapshot_id"]

        second_globals = deepcopy(self.desired_globals)
        second_globals["revision"] = 9
        second_globals["output"]["brightness"] = 64
        second = coordinator.activate(self.command(
            coordinator, globals_state=second_globals
        ))
        historical = coordinator.get(first["activation_id"])

        self.assertEqual(second["phase"], "active")
        self.assertFalse(historical["rollback"]["available"])
        self.assertEqual(
            historical["rollback"]["snapshot_id"], first_snapshot_id
        )
        self.assertIn("superseded", historical["rollback"]["error"])
        with self.assertRaises(ControllerActivationConflictError):
            coordinator.rollback(
                first["activation_id"],
                snapshot_id=first_snapshot_id,
                expected_session_id=coordinator.session_id,
                expected_state_revision=coordinator.state_revision,
            )
        self.assertEqual(manager.brightness, 64)

    def test_legacy_mutation_invalidates_active_rollback_authority(self) -> None:
        manager, coordinator = self.coordinator()
        active = coordinator.activate(self.command(coordinator))

        with coordinator.legacy_mutation_guard():
            manager.set_output_brightness(33)
        historical = coordinator.get(active["activation_id"])

        self.assertFalse(historical["rollback"]["available"])
        self.assertIn("controller mutation", historical["rollback"]["error"])
        self.assertEqual(manager.brightness, 33)
        with self.assertRaises(ControllerActivationConflictError):
            coordinator.rollback(
                active["activation_id"],
                snapshot_id=active["rollback"]["snapshot_id"],
                expected_session_id=coordinator.session_id,
                expected_state_revision=coordinator.state_revision,
            )

    def test_historical_active_receipts_are_bounded(self) -> None:
        manager, coordinator = self.coordinator(max_records=2)
        latest = None
        for revision, brightness in ((8, 96), (9, 80), (10, 64), (11, 48)):
            globals_state = deepcopy(self.desired_globals)
            globals_state["revision"] = revision
            globals_state["output"]["brightness"] = brightness
            latest = coordinator.activate(self.command(
                coordinator, globals_state=globals_state
            ))
            self.assertEqual(latest["phase"], "active")

        records = coordinator.controller_status()["activations"]
        self.assertLessEqual(len(records), 2)
        self.assertEqual(records[-1]["activation_id"], latest["activation_id"])
        self.assertTrue(records[-1]["rollback"]["available"])
        self.assertTrue(all(
            not record["rollback"]["available"] for record in records[:-1]
        ))
        self.assertEqual(manager.brightness, 48)

    def test_restart_renews_only_the_current_active_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            channel = FileControlChannel(
                str(root / "control.json"),
                str(root / "status.json"),
                str(root / "activations"),
            )
            manager, coordinator = self.coordinator(
                status_sink=channel.write_activation_status
            )
            first_command = self.command(
                coordinator, activation_id="00000000-0000-0000-0000-000000000001"
            )
            channel.enqueue_activation(first_command)
            first = coordinator.activate(first_command)

            second_globals = deepcopy(self.desired_globals)
            second_globals["revision"] = 9
            second_globals["output"]["brightness"] = 64
            second_command = self.command(
                coordinator,
                activation_id="00000000-0000-0000-0000-000000000002",
                globals_state=second_globals,
            )
            channel.enqueue_activation(second_command)
            second = coordinator.activate(second_command)
            first_historical = channel.read_activation_status(
                first_command["activation_id"]
            )
            self.assertIn("superseded", first_historical["rollback"]["error"])

            restarted_manager = _FakeManager(
                self.catalog, self.desired_scene, second_globals
            )
            restarted_manager.profile = PROFILE_A
            restarted = ControllerActivationCoordinator(
                restarted_manager, status_sink=channel.write_activation_status
            )

            self.assertEqual(process_activation_commands(channel, restarted), 0)
            retained_first = channel.read_activation_status(
                first_command["activation_id"]
            )
            renewed_second = channel.read_activation_status(
                second_command["activation_id"]
            )
            self.assertEqual(
                retained_first["controller"]["session_id"],
                first["controller"]["session_id"],
            )
            self.assertIn("superseded", retained_first["rollback"]["error"])
            self.assertEqual(
                renewed_second["controller"]["session_id"], restarted.session_id
            )
            self.assertNotEqual(
                renewed_second["controller"]["session_id"],
                second["controller"]["session_id"],
            )
            self.assertEqual(restarted_manager.mutation_count, 0)

    def test_rollback_failure_is_correlated_and_invalidates_prior_checks(self) -> None:
        def inject(phase: str, boundary: str, _activation_id: str) -> None:
            if phase == "applying" and boundary == "vibe":
                raise RuntimeError("apply fault")
            if phase == "rolling_back" and boundary == "before_restore":
                raise RuntimeError("rollback fault")

        _manager, coordinator = self.coordinator(fault_injector=inject)

        status = coordinator.activate(self.command(coordinator))

        self.assertEqual(status["phase"], "failed")
        self.assertEqual(status["rollback"]["result"], "failed")
        self.assertIn("rollback fault", status["rollback"]["error"])
        self.assertEqual(coordinator.state_revision, 1)
        normalize_scene_activation_status(status)

    def test_local_dispatch_exposes_the_same_guarded_status(self) -> None:
        manager = _FakeManager(
            self.catalog, self.initial_scene, self.initial_globals
        )
        channel = LocalControlChannel(manager)
        command = self.command(channel.activation_coordinator)

        result = channel.send_command("activate_scene", activation=command)
        status = channel.read_activation_status(command["activation_id"])
        controller = channel.read_status()

        self.assertEqual(result["activation_status"]["phase"], "active")
        self.assertEqual(status["phase"], "active")
        self.assertEqual(
            controller["controller_session_id"],
            channel.activation_coordinator.session_id,
        )
        self.assertEqual(controller["controller_state_revision"], 1)
        self.assertEqual(
            controller["active_identity"], status["observed_identity"]
        )

    def test_local_channel_exposes_correlated_cancel_and_rollback_results(self) -> None:
        manager = _FakeManager(
            self.catalog, self.initial_scene, self.initial_globals
        )
        channel = LocalControlChannel(manager)
        command = self.command(channel.activation_coordinator)
        active = channel.activation_coordinator.activate(command)
        rollback = channel.request_activation_rollback(
            command["activation_id"],
            snapshot_id=active["rollback"]["snapshot_id"],
            expected_controller_session_id=(
                channel.activation_coordinator.session_id
            ),
            expected_controller_state_revision=(
                channel.activation_coordinator.state_revision
            ),
        )
        self.assertEqual(
            channel.read_activation_rollback(command["activation_id"]), rollback
        )
        self.assertEqual(
            channel.read_activation_rollback_result(command["activation_id"])[
                "outcome"
            ],
            "succeeded",
        )

        manager = _FakeManager(
            self.catalog, self.initial_scene, self.initial_globals
        )
        channel = LocalControlChannel(manager)
        command = self.command(channel.activation_coordinator)
        channel.activation_coordinator.queue(command)
        cancel = channel.request_activation_cancel(command["activation_id"])
        self.assertEqual(
            channel.read_activation_cancel(command["activation_id"]), cancel
        )
        self.assertEqual(
            channel.read_activation_cancel_result(command["activation_id"])[
                "outcome"
            ],
            "succeeded",
        )

    def test_legacy_mutation_waits_behind_activation_and_advances_once(self) -> None:
        manager = _FakeManager(
            self.catalog, self.initial_scene, self.initial_globals
        )
        manager.preflight_entered = threading.Event()
        manager.preflight_release = threading.Event()
        channel = LocalControlChannel(manager)
        command = self.command(channel.activation_coordinator)
        activation_result: list[dict] = []
        legacy_finished = threading.Event()

        activation_worker = threading.Thread(target=lambda: activation_result.append(
            channel.send_command("activate_scene", activation=command)
        ))
        legacy_worker = threading.Thread(target=lambda: (
            channel.send_command("set_output_brightness", brightness=33),
            legacy_finished.set(),
        ))
        activation_worker.start()
        self.assertTrue(manager.preflight_entered.wait(timeout=1))
        legacy_worker.start()

        self.assertFalse(legacy_finished.wait(timeout=0.05))
        self.assertEqual(manager.brightness, self.initial_globals["output"]["brightness"])
        manager.preflight_release.set()
        activation_worker.join(timeout=2)
        legacy_worker.join(timeout=2)

        self.assertFalse(activation_worker.is_alive())
        self.assertFalse(legacy_worker.is_alive())
        self.assertEqual(
            activation_result[0]["activation_status"]["phase"], "active"
        )
        self.assertEqual(manager.brightness, 33)
        self.assertEqual(channel.activation_coordinator.state_revision, 2)


if __name__ == "__main__":
    unittest.main()
