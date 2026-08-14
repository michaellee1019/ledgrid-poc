"""Product-boundary policy coverage for the Phase 3B0 hybrid scene."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path
import tempfile
import unittest

from animation.core.feature_flags import AnimationPipelineFeatureFlags
from animation.core.receiver_static_component import (
    COMPILED_RAINBOW_CONTRACT_DIGEST,
    COMPILED_RAINBOW_EXPECTED_PAYLOAD_DIGEST,
    COMPILED_RAINBOW_PLUGIN_ID as DESCRIPTOR_COMPILED_RAINBOW_PLUGIN_ID,
    receiver_static_component_descriptor,
)
from ipc.scene_contract import (
    COMPILED_RAINBOW_PLUGIN_ID,
    DEFAULT_SCENE_PROVIDER_POLICY,
    SceneProviderPolicy,
    SceneValidationError,
    decorate_catalog,
    filter_catalog,
    normalize_scene_payload,
    scene_preview_identity,
)
from web.app import AnimationWebInterface


BUNDLE_DIGEST = COMPILED_RAINBOW_CONTRACT_DIGEST
PAYLOAD_DIGEST = COMPILED_RAINBOW_EXPECTED_PAYLOAD_DIGEST


def enabled_policy() -> SceneProviderPolicy:
    return SceneProviderPolicy(
        receiver_local_background=True,
        receiver_sparse_overlay=True,
    )


def native_ref(plugin_id: str = COMPILED_RAINBOW_PLUGIN_ID) -> dict:
    return {
        "plugin_id": plugin_id,
        "provider": "receiver_native",
        "parameter_overrides": {"preferred_cadence_hz": 30},
        "resolved_parameters": {
            "preferred_cadence_hz": 30,
            "common_seed": 7,
        },
        "bundle_digest": BUNDLE_DIGEST,
        "expected_payload_digest": PAYLOAD_DIGEST,
    }


def python_ref(plugin_id: str = "gradient") -> dict:
    return {
        "plugin_id": plugin_id,
        "provider": "python",
        "parameter_overrides": {},
        "resolved_parameters": {"speed": 0.5},
    }


def native_scene() -> dict:
    return {
        "schema": "ledgrid.scene-state",
        "schema_version": 1,
        "revision": 9,
        "background": native_ref(),
        "overlays": [],
        "known_python_fallback": python_ref(),
    }


def component_catalog() -> list[dict]:
    compiled = receiver_static_component_descriptor({
        "receiver_local_background": True,
        "receiver_sparse_overlay": True,
    })
    assert compiled is not None
    return [
        compiled,
        {
            "plugin_id": "arbitrary_native",
            "name": "Arbitrary native",
            "provider": "receiver_native",
            "role": "background",
        },
        {
            "plugin_id": "gradient",
            "name": "Gradient",
            "provider": "python",
            "role": "background",
            "parameter_schema": {
                "speed": {"type": "float", "min": 0.1, "max": 3.0}
            },
            "defaults": {"speed": 0.5},
        },
        {
            "plugin_id": "clock_overlay",
            "name": "Clock overlay",
            "provider": "python",
            "role": "overlay",
            "parameter_schema": {
                "show_seconds": {"type": "bool"},
                "format_24h": {"type": "bool"},
            },
            "defaults": {"show_seconds": True, "format_24h": False},
        },
    ]


class SceneProviderPolicyTests(unittest.TestCase):
    def test_policy_is_immutable_strictly_typed_and_disabled_by_default(self):
        self.assertEqual(
            COMPILED_RAINBOW_PLUGIN_ID,
            DESCRIPTOR_COMPILED_RAINBOW_PLUGIN_ID,
        )
        self.assertFalse(DEFAULT_SCENE_PROVIDER_POLICY.compiled_rainbow_enabled)
        with self.assertRaises(FrozenInstanceError):
            DEFAULT_SCENE_PROVIDER_POLICY.receiver_local_background = True
        for field in ("receiver_local_background", "receiver_sparse_overlay"):
            with self.subTest(field=field):
                with self.assertRaisesRegex(TypeError, field):
                    SceneProviderPolicy(**{field: 1})

    def test_feature_off_catalog_decoration_remains_phase_2c_compatible(self):
        descriptor = component_catalog()[0]
        expected = {
            **descriptor,
            "scene_compatibility": {
                "selectable": False,
                "slots": [],
                "diagnostic": (
                    "This provider is catalog-visible but not executable in host scenes."
                ),
            },
        }
        self.assertEqual(decorate_catalog([descriptor]), [expected])
        self.assertEqual(
            decorate_catalog(
                [descriptor], provider_policy=SceneProviderPolicy()
            ),
            [expected],
        )

        with self.assertRaisesRegex(
            SceneValidationError,
            r"scene\.background\.provider 'receiver_native' is unsupported; "
            r"Phase 2C supports python",
        ):
            normalize_scene_payload(native_scene())

    def test_both_receiver_features_are_required(self):
        policies = (
            SceneProviderPolicy(),
            SceneProviderPolicy(receiver_local_background=True),
            SceneProviderPolicy(receiver_sparse_overlay=True),
        )
        for policy in policies:
            with self.subTest(policy=policy):
                decorated = decorate_catalog(
                    [component_catalog()[0]], provider_policy=policy
                )[0]
                self.assertFalse(decorated["scene_compatibility"]["selectable"])
                with self.assertRaises(SceneValidationError):
                    normalize_scene_payload(native_scene(), provider_policy=policy)

    def test_enabled_policy_selects_only_exact_compiled_background(self):
        decorated = decorate_catalog(
            component_catalog(), provider_policy=enabled_policy()
        )
        by_id = {item["plugin_id"]: item for item in decorated}
        self.assertEqual(
            by_id[COMPILED_RAINBOW_PLUGIN_ID]["scene_compatibility"],
            {"selectable": True, "slots": ["background"], "diagnostic": None},
        )
        self.assertFalse(
            by_id["arbitrary_native"]["scene_compatibility"]["selectable"]
        )
        self.assertIn(
            "Only the compiled_rainbow",
            by_id["arbitrary_native"]["scene_compatibility"]["diagnostic"],
        )

        wrong_role = deepcopy(component_catalog()[0])
        wrong_role["role"] = "overlay"
        decorated_wrong_role = decorate_catalog(
            [wrong_role], provider_policy=enabled_policy()
        )[0]
        self.assertFalse(
            decorated_wrong_role["scene_compatibility"]["selectable"]
        )
        self.assertIn(
            "must declare the background role",
            decorated_wrong_role["scene_compatibility"]["diagnostic"],
        )

    def test_filters_keep_one_catalog_and_policy_decorations(self):
        native = filter_catalog(
            component_catalog(),
            provider="receiver_native",
            role="background",
            provider_policy=enabled_policy(),
        )
        self.assertEqual(
            [item["plugin_id"] for item in native],
            ["arbitrary_native", COMPILED_RAINBOW_PLUGIN_ID],
        )
        self.assertEqual(
            [
                item["plugin_id"]
                for item in native
                if item["scene_compatibility"]["selectable"]
            ],
            [COMPILED_RAINBOW_PLUGIN_ID],
        )
        with self.assertRaisesRegex(SceneValidationError, "provider filter"):
            filter_catalog(
                component_catalog(),
                provider="native_upload",
                provider_policy=enabled_policy(),
            )


class ReceiverNativeSceneValidationTests(unittest.TestCase):
    def test_exact_native_scene_round_trips_with_v1_digests_and_python_fallback(self):
        normalized = normalize_scene_payload(
            native_scene(),
            catalog=component_catalog(),
            provider_policy=enabled_policy(),
        )
        self.assertEqual(
            normalized["background"]["plugin_id"],
            COMPILED_RAINBOW_PLUGIN_ID,
        )
        self.assertEqual(normalized["background"]["bundle_digest"], BUNDLE_DIGEST)
        self.assertEqual(
            normalized["background"]["expected_payload_digest"], PAYLOAD_DIGEST
        )
        self.assertEqual(
            normalized["known_python_fallback"]["provider"], "python"
        )

    def test_arbitrary_native_remains_rejected_when_policy_is_enabled(self):
        scene = native_scene()
        scene["background"] = native_ref("arbitrary_native")
        with self.assertRaisesRegex(
            SceneValidationError, "limited to 'compiled_rainbow'"
        ):
            normalize_scene_payload(scene, provider_policy=enabled_policy())

    def test_native_ref_requires_both_lowercase_sha256_digests(self):
        invalid = (
            ("bundle_digest", None),
            ("expected_payload_digest", None),
            ("bundle_digest", "A" * 64),
            ("expected_payload_digest", "not-a-digest"),
        )
        for field, value in invalid:
            with self.subTest(field=field, value=value):
                scene = native_scene()
                if value is None:
                    scene["background"].pop(field)
                else:
                    scene["background"][field] = value
                with self.assertRaisesRegex(SceneValidationError, field):
                    normalize_scene_payload(
                        scene, provider_policy=enabled_policy()
                    )

        wrong_binding = native_scene()
        wrong_binding["background"]["bundle_digest"] = "c" * 64
        with self.assertRaisesRegex(
            SceneValidationError, "bundle_digest does not match the catalog binding"
        ):
            normalize_scene_payload(
                wrong_binding,
                catalog=component_catalog(),
                provider_policy=enabled_policy(),
            )

    def test_scene_schema_role_and_python_fallback_remain_strict(self):
        bad_schema = native_scene()
        bad_schema["schema_version"] = 2
        with self.assertRaisesRegex(SceneValidationError, "schema_version"):
            normalize_scene_payload(
                bad_schema, provider_policy=enabled_policy()
            )

        wrong_role_catalog = component_catalog()
        wrong_role_catalog[0] = {**wrong_role_catalog[0], "role": "overlay"}
        with self.assertRaisesRegex(SceneValidationError, "requires role background"):
            normalize_scene_payload(
                native_scene(),
                catalog=wrong_role_catalog,
                provider_policy=enabled_policy(),
            )

        native_fallback = native_scene()
        native_fallback["known_python_fallback"] = native_ref()
        with self.assertRaisesRegex(
            SceneValidationError, "known_python_fallback must use the python provider"
        ):
            normalize_scene_payload(
                native_fallback, provider_policy=enabled_policy()
            )

        missing_fallback = native_scene()
        missing_fallback.pop("known_python_fallback")
        with self.assertRaisesRegex(
            SceneValidationError, "known_python_fallback must use the python provider"
        ):
            normalize_scene_payload(
                missing_fallback, provider_policy=enabled_policy()
            )

    def test_python_components_still_reject_native_digest_fields(self):
        scene = native_scene()
        scene["known_python_fallback"]["bundle_digest"] = BUNDLE_DIGEST
        with self.assertRaisesRegex(
            SceneValidationError,
            "python scene.known_python_fallback must not declare bundle_digest",
        ):
            normalize_scene_payload(scene, provider_policy=enabled_policy())

    def test_preview_identity_covers_native_binding_parameters_and_vibe(self):
        policy = enabled_policy()
        vibe = {"vibe_id": "neutral", "revision": 1}
        plants = {"version": 1, "active": [], "strengths": {}}
        baseline = scene_preview_identity(
            native_scene(), vibe, plants, elapsed=1.0, provider_policy=policy
        )
        self.assertEqual(
            baseline,
            scene_preview_identity(
                native_scene(), vibe, plants, elapsed=1.0, provider_policy=policy
            ),
        )

        variants = []
        changed_bundle = native_scene()
        changed_bundle["background"]["bundle_digest"] = "c" * 64
        variants.append((changed_bundle, vibe, 1.0))
        changed_payload = native_scene()
        changed_payload["background"]["expected_payload_digest"] = "d" * 64
        variants.append((changed_payload, vibe, 1.0))
        changed_parameters = native_scene()
        changed_parameters["background"]["parameter_overrides"][
            "preferred_cadence_hz"
        ] = 60
        variants.append((changed_parameters, vibe, 1.0))
        variants.append((native_scene(), {**vibe, "revision": 2}, 1.0))
        variants.append((native_scene(), vibe, 2.0))

        for scene, variant_vibe, elapsed in variants:
            with self.subTest(scene=scene, vibe=variant_vibe, elapsed=elapsed):
                self.assertNotEqual(
                    baseline,
                    scene_preview_identity(
                        scene,
                        variant_vibe,
                        plants,
                        elapsed=elapsed,
                        provider_policy=policy,
                    ),
                )

        with self.assertRaises(SceneValidationError):
            scene_preview_identity(native_scene(), vibe, plants, elapsed=1.0)


class _WebController:
    strip_count = 2
    leds_per_strip = 3
    total_leds = 6


class _WebHybridPreviewManager:
    controller = _WebController()
    preview_controller = controller
    feature_flags = AnimationPipelineFeatureFlags(
        receiver_local_background=True,
        receiver_sparse_overlay=True,
    )

    def __init__(self, policy: SceneProviderPolicy | None = None):
        self.policy = policy or enabled_policy()
        self.preview_calls: list[tuple] = []

    def scene_provider_policy(self) -> SceneProviderPolicy:
        return self.policy

    def list_animations(self) -> list[dict]:
        return []

    def list_components(self) -> list[dict]:
        return component_catalog()

    def get_animation_info(self, name: str) -> dict | None:
        return next(
            (item for item in self.list_components() if item["plugin_id"] == name),
            None,
        )

    def get_scene_preview(
        self,
        scene: dict,
        *,
        vibe: dict | None = None,
        plant_modifiers: dict | None = None,
        elapsed: float = 0,
    ) -> dict:
        self.preview_calls.append((scene, vibe, plant_modifiers, elapsed))
        return {
            "frame_data": [[1, 2, 3]] * self.controller.total_leds,
            "led_info": {
                "strip_count": self.controller.strip_count,
                "leds_per_strip": self.controller.leds_per_strip,
                "total_leds": self.controller.total_leds,
            },
            # The web boundary owns these safety labels for receiver previews.
            "preview_label": "unsafe upstream label",
            "live_state_mutated": True,
            "framebuffer_readback": True,
        }

    def get_vibe_status(self) -> dict:
        from animation.core.presentation_contracts import resolve_vibe

        return {"state": resolve_vibe("neutral").state.to_dict()}


class _WebChannel:
    def __init__(self):
        self.commands: list[dict] = []
        self.status = {
            "is_running": False,
            "feature_flags": _WebHybridPreviewManager.feature_flags.to_dict(),
            "plant_modifiers": {"version": 1, "active": [], "strengths": {}},
            "led_info": {"strip_count": 2, "leds_per_strip": 3, "total_leds": 6},
            "scene": {
                "provider_mode": "receiver_hybrid",
                "receiver": {
                    "healthy": False,
                    "source_scene_revision": 9,
                    "context_revision": 4,
                    "context_digest": "a" * 64,
                    "fallback_active": True,
                    "publisher": {
                        "lease_ms": 3000,
                        "generation": 7,
                        "last_operation": "renew",
                        "last_error": "receiver agreement lost",
                    },
                },
            },
        }

    def read_status(self) -> dict:
        return deepcopy(self.status)

    def send_command(self, action: str, **data) -> dict:
        command = {
            "command_id": len(self.commands) + 1,
            "action": action,
            "data": data,
        }
        self.commands.append(command)
        return command


def web_native_scene(*, revision: int = 9) -> dict:
    scene = native_scene()
    scene["revision"] = revision
    scene["overlays"] = [{
        "slot_id": "clock_overlay",
        "component": {
            "plugin_id": "clock_overlay",
            "provider": "python",
            "parameter_overrides": {},
            "resolved_parameters": {},
        },
        "enabled": True,
        "opacity": 192,
        "placement": {
            "strip_translation": 1,
            "led_translation": -2,
            "clip_policy": "clip_to_wall",
        },
        "stale_policy": {"policy": "clear_after_lease", "lease_ms": 1000},
    }]
    return scene


class HybridSceneWebProductSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.channel = _WebChannel()
        self.preview = _WebHybridPreviewManager()
        self.interface = AnimationWebInterface(self.channel, self.preview)
        self.interface.animation_presets_dir = Path(self.temporary.name) / "animations"
        self.interface.animation_presets_dir.mkdir()
        self.interface.scene_presets_dir = Path(self.temporary.name) / "scenes"
        self.interface.scene_presets_dir.mkdir()
        self.client = self.interface.app.test_client()

    def tearDown(self):
        self.temporary.cleanup()

    def test_catalog_and_dashboard_expose_only_policy_enabled_receiver_choice(self):
        response = self.client.get(
            "/api/v1/components?provider=receiver_native&role=background"
        )
        self.assertEqual(response.status_code, 200)
        native = response.get_json()["components"]
        self.assertEqual(
            [item["plugin_id"] for item in native],
            ["arbitrary_native", COMPILED_RAINBOW_PLUGIN_ID],
        )
        self.assertEqual(
            [
                item["plugin_id"]
                for item in native
                if item["scene_compatibility"]["selectable"]
            ],
            [COMPILED_RAINBOW_PLUGIN_ID],
        )

        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('data-provider="receiver_native"', html)
        self.assertIn('id="scenePythonFallbackSelect"', html)
        self.assertIn('id="sceneReceiverCadence"', html)
        self.assertIn('id="sceneClockShowSeconds"', html)
        self.assertIn("not receiver framebuffer readback", html)
        for element_id in (
            "receiverAgreementState",
            "receiverForegroundLease",
            "receiverForegroundGeneration",
            "receiverFallbackState",
            "receiverHybridDetail",
        ):
            self.assertIn(f'id="{element_id}"', html)

    def test_explicit_policy_off_rejects_native_even_when_flags_and_catalog_are_on(self):
        preview = _WebHybridPreviewManager(policy=DEFAULT_SCENE_PROVIDER_POLICY)
        interface = AnimationWebInterface(self.channel, preview)
        interface.animation_presets_dir = self.interface.animation_presets_dir
        interface.scene_presets_dir = self.interface.scene_presets_dir
        client = interface.app.test_client()

        catalog = client.get("/api/v1/components").get_json()["components"]
        compiled = next(
            item for item in catalog if item["plugin_id"] == COMPILED_RAINBOW_PLUGIN_ID
        )
        self.assertFalse(compiled["scene_compatibility"]["selectable"])
        before = len(self.channel.commands)
        response = client.put("/api/v1/scene", json=web_native_scene())
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.channel.commands), before)
        html = client.get("/").get_data(as_text=True)
        self.assertNotIn('id="receiverHybridStatus"', html)
        self.assertIn("Python provider · fixed slots", html)

    def test_enabled_policy_still_requires_both_typed_manager_flags(self):
        preview = _WebHybridPreviewManager()
        preview.feature_flags = AnimationPipelineFeatureFlags(
            receiver_local_background=True,
            receiver_sparse_overlay=False,
        )
        interface = AnimationWebInterface(self.channel, preview)
        interface.animation_presets_dir = self.interface.animation_presets_dir
        interface.scene_presets_dir = self.interface.scene_presets_dir
        client = interface.app.test_client()

        response = client.put("/api/v1/scene", json=web_native_scene())
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.channel.commands, [])
        self.assertNotIn(
            'id="receiverHybridStatus"', client.get("/").get_data(as_text=True)
        )

    def test_native_start_updates_toggle_preset_and_cleanup_emit_scoped_commands(self):
        scene = web_native_scene()
        started = self.client.put("/api/v1/scene", json=scene)
        self.assertEqual(started.status_code, 200)
        start_command = self.channel.commands[-1]
        self.assertEqual(start_command["action"], "start_scene")
        self.assertEqual(
            start_command["data"]["scene"]["known_python_fallback"]["provider"],
            "python",
        )
        self.assertEqual(
            start_command["data"]["scene"]["background"]["bundle_digest"],
            BUNDLE_DIGEST,
        )

        self.channel.status.update({
            "is_running": True,
            "current_animation": COMPILED_RAINBOW_PLUGIN_ID,
            "scene_state": scene,
        })
        updated = self.client.patch(
            "/api/v1/scene/components/background",
            json={"params": {"preferred_cadence_hz": 60, "common_seed": 99}},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(self.channel.commands[-1]["action"], "update_scene_component")
        self.assertEqual(
            self.channel.commands[-1]["data"]["update"]["params"]["common_seed"],
            99,
        )

        toggled = self.client.patch(
            "/api/v1/scene/components/clock_overlay", json={"enabled": False}
        )
        self.assertEqual(toggled.status_code, 200)
        self.assertFalse(self.channel.commands[-1]["data"]["update"]["enabled"])

        saved = self.client.post(
            "/api/v1/scene-presets", json={"name": "Hybrid Clock", "scene": scene}
        )
        self.assertEqual(saved.status_code, 200)
        preset = saved.get_json()["preset"]
        self.assertEqual(preset["scene"]["background"]["provider"], "receiver_native")
        applied = self.client.post(
            f'/api/v1/scene-presets/{preset["preset_id"]}/apply'
        )
        self.assertEqual(applied.status_code, 200)
        self.assertEqual(self.channel.commands[-1]["action"], "start_scene")

        stopped = self.client.delete("/api/v1/scene")
        self.assertEqual(stopped.status_code, 200)
        self.assertEqual(self.channel.commands[-1]["action"], "stop_scene")

    def test_native_preview_is_host_simulation_and_never_sends_hardware_command(self):
        before = deepcopy(self.channel.commands)
        response = self.client.post(
            "/api/v1/scene/preview",
            json={
                "scene": web_native_scene(),
                "vibe": "cozy",
                "plant_modifiers": {
                    "version": 1,
                    "active": [],
                    "strengths": {},
                },
                "elapsed": 1.25,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(
            payload["preview_label"],
            "Host simulation preview — not receiver framebuffer readback",
        )
        self.assertEqual(payload["background_provider"], "receiver_native")
        self.assertFalse(payload["live_state_mutated"])
        self.assertFalse(payload["framebuffer_readback"])
        self.assertEqual(self.channel.commands, before)
        self.assertEqual(len(self.preview.preview_calls), 1)
        self.assertEqual(self.preview.preview_calls[0][1]["vibe_id"], "cozy")

    def test_status_contract_and_javascript_surface_degraded_lease_state(self):
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["scene"]["provider_mode"], "receiver_hybrid")
        self.assertTrue(payload["scene"]["receiver"]["fallback_active"])
        self.assertEqual(payload["scene"]["receiver"]["publisher"]["lease_ms"], 3000)

        dashboard_js = (
            Path(__file__).resolve().parents[2] / "web/static/js/dashboard.js"
        ).read_text()
        for contract_field in (
            "provider_mode",
            "fallback_active",
            "source_scene_revision",
            "context_revision",
            "lease_ms",
            "generation",
            "last_operation",
            "last_error",
        ):
            self.assertIn(contract_field, dashboard_js)
        self.assertIn("descriptor.provider", dashboard_js)
        self.assertIn("build.expected_payload_digest", dashboard_js)
        self.assertIn("syncReceiverHybridStatus", dashboard_js)


if __name__ == "__main__":
    unittest.main()
