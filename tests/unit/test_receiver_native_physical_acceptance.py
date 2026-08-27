"""Focused H2/H4 physical-evidence runner coverage without hardware access."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from tools.benchmarks import receiver_native_physical_acceptance as acceptance


BUNDLE = "a" * 64
PAYLOAD = "b" * 64
PROFILE = "c" * 64
CONTEXT = "d" * 64
VIBE = "f" * 64
PLANT = "1" * 64
CAPABILITIES = 0x1FF
NATIVE_SCHEMA = {
    "layers": {"type": "int", "default": 3, "min": 1, "max": 5},
    "motion": {"type": "float", "default": 0.4, "min": 0.1, "max": 1.0},
    "shimmer": {"type": "bool", "default": False},
}
NATIVE_DEFAULTS = {"layers": 3, "motion": 0.4, "shimmer": False}
PARAMETERS = acceptance.encode_native_parameters(
    NATIVE_SCHEMA, NATIVE_DEFAULTS
).digest


class _Clock:
    def __init__(self) -> None:
        self.value = 1_800_000_000.0

    def monotonic(self) -> float:
        return self.value - 1_800_000_000.0

    def time(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def _catalog() -> list[dict]:
    return [
        {
            "plugin_id": acceptance.DEFAULT_PLUGIN,
            "provider": "receiver_native",
            "role": "background",
            "availability": {"state": "ready", "selectable": True},
            "build": {
                "bundle_digest": BUNDLE,
                "expected_payload_digest": PAYLOAD,
            },
            "cadence": {"mode": "fixed_fps", "preferred_fps": 60},
            "defaults": NATIVE_DEFAULTS,
            "parameter_schema": NATIVE_SCHEMA,
        },
        {
            "plugin_id": acceptance.DEFAULT_FALLBACK,
            "provider": "python",
            "role": "background",
            "defaults": {"speed": 0.4},
        },
        {
            "plugin_id": acceptance.DEFAULT_CLOCK_OVERLAY,
            "provider": "python",
            "role": "overlay",
            "defaults": {"show_seconds": True},
            "parameter_schema": {
                "show_seconds": {"type": "bool", "default": True},
            },
        },
    ]


class _API:
    def __init__(
        self, clock: _Clock, *, fail_activate: bool = False,
        fail_recover: bool = False,
    ) -> None:
        self.clock = clock
        self.fail_activate = fail_activate
        self.fail_recover = fail_recover
        self.mode = "idle"
        self.command_id = None
        self.native_started = None
        self.active_scene = None
        self.calls: list[tuple[str, str, object]] = []

    def request(self, path, *, method="GET", payload=None, timeout=10.0):
        self.calls.append((method, path, deepcopy(payload)))
        if path == "/api/v1/components":
            return {"components": _catalog()}
        if path.endswith("/install"):
            self.command_id = 1
            self.mode = "ready"
            return {"command_id": self.command_id}
        if path == "/api/v1/scene" and method == "PUT":
            background = payload["background"]
            self.active_scene = deepcopy(payload)
            self.command_id = 4 if background["provider"] == "python" else 2
            if background["provider"] == "python":
                self.mode = "python"
            elif not self.fail_activate:
                self.mode = "native"
                self.native_started = self.clock.monotonic()
            return {"command_id": self.command_id}
        if path == "/api/v1/receiver-native/recover":
            if self.fail_recover:
                raise acceptance.PhysicalAcceptanceError("injected recovery failure")
            self.command_id = 3
            self.mode = "python"
            fallback = deepcopy(self.active_scene["known_python_fallback"])
            self.active_scene = {
                "schema": "ledgrid.scene-state",
                "schema_version": 1,
                "revision": self.active_scene["revision"] + 1,
                "background": fallback,
                "overlays": [],
                "known_python_fallback": fallback,
            }
            return {"command_id": self.command_id}
        if path == "/api/status":
            return self.status()
        raise AssertionError((method, path, payload, timeout))

    def _native_driver(self):
        state = "active" if self.mode == "native" else self.mode
        if self.mode == "python":
            state = "host_full_scene"
        result = {
            "state": state,
            "operation": state,
            "error": None,
            "bundle_digest": BUNDLE,
            "payload_digest": PAYLOAD,
            "parameter_digest": PARAMETERS,
        }
        if self.mode == "native":
            result.update({
                "agreement": {
                    "exact_roster": True,
                    "verified_receiver_ids": [0, 1, 2, 3, 4],
                },
                "capability_report": {
                    "required_capabilities": CAPABILITIES,
                    "devices": [
                        {**view, "capabilities": CAPABILITIES}
                        for view in acceptance.EXPECTED_TOPOLOGY
                    ],
                },
            })
        return result

    def _device(self, receiver_id: int):
        topology = acceptance.EXPECTED_TOPOLOGY_BY_ID[receiver_id]
        elapsed = max(0.0, self.clock.monotonic() - (self.native_started or 0.0))
        frames = int(elapsed * 60)
        return {
            "errors": 0,
            "receiver_status_seen": True,
            "receiver_status_version": 6,
            "receiver_capabilities": CAPABILITIES,
            "receiver_logical_device": receiver_id,
            "receiver_base_mode": 4,
            "receiver_last_result": 1,
            "receiver_active_context_digest": CONTEXT,
            "receiver_vibe_revision": 7,
            "receiver_vibe_digest": VIBE,
            "receiver_plant_modifier_revision": 8,
            "receiver_plant_modifier_digest": PLANT,
            "receiver_active_session_id": "12" * 16,
            "receiver_operation_sequence": 10,
            "spi_transfers": 100 + frames,
            "bytes_sent": 1_000 + frames * 100,
            "crc_bytes_sent": 200 + frames * 2,
            "receiver_crc_errors": 0,
            "receiver_packets": 100 + frames,
            "receiver_crc_ok_packets": 100 + frames,
            "receiver_frames_accepted": frames,
            "receiver_frames_displayed": frames,
            "receiver_frames_superseded": 0,
            "receiver_publish_drops": 0,
            "receiver_spi_queue_errors": 0,
            "receiver_display_errors": 0,
            "receiver_status_misses": 0,
            "receiver_last_crc_us": 30,
            "receiver_last_copy_us": 40,
            "receiver_last_encode_us": 450 + receiver_id,
            "receiver_last_show_us": 4_400 + receiver_id,
            "receiver_declared_cadence_hz": 60,
            "receiver_local_frames_rendered": frames,
            "receiver_local_cadence_deadlines": frames,
            "receiver_local_missed_deadlines": 0,
            "receiver_last_local_render_us": 900,
            "receiver_max_local_render_us": 1_000,
            "receiver_last_frame_scene_time_us": frames * 16_666 + receiver_id * 100,
            "receiver_overlay_composite_frames": frames,
            "receiver_overlay_last_composite_us": 80,
            "receiver_overlay_max_composite_us": 100,
            "receiver_profile_active_global_digest": PROFILE,
            "receiver_profile_active_payload_digest": str(receiver_id) * 64,
            "receiver_native_result_name": "ok",
            "receiver_native_watchdog_phase_name": "none",
            "receiver_native_ready": True,
            "receiver_native_cache_integrity_ok": True,
            "receiver_native_executing": self.mode == "native",
            "receiver_native_capacity_bytes": 3_000_000,
            "receiver_native_used_bytes": 100_000,
            "receiver_native_free_bytes": 2_900_000,
            "receiver_native_reserve_bytes": 200_000,
            "receiver_native_reclaimable_bytes": 0,
            "receiver_native_state_generation": 20,
            "receiver_native_active_bundle_digest": BUNDLE,
            "receiver_native_active_payload_digest": PAYLOAD,
            "receiver_native_rollback_bundle_digest": "9" * 64,
            "receiver_native_rollback_payload_digest": "8" * 64,
            "receiver_native_quarantine_payload_digest": None,
            "receiver_native_active_cadence_hz": 60,
            "receiver_native_active_local_strips": topology["local_strip_count"],
            "receiver_native_active_global_strips": 33,
            "receiver_native_active_leds_per_strip": 138,
            "receiver_native_active_global_strip_offset": topology["global_strip_offset"],
            "receiver_native_active_parameter_digest": PARAMETERS,
            "receiver_native_last_load_us": 200,
            "receiver_native_last_initialize_us": 50,
            "receiver_native_last_context_us": 25,
            "receiver_native_last_render_us": 900,
            "receiver_native_max_phase_us": 1_000,
            "receiver_native_watchdog_events": 0,
            "receiver_native_quarantines": 0,
        }

    def status(self):
        native = self._native_driver()
        python_mode = self.mode == "python"
        device_map = [dict(view) for view in acceptance.EXPECTED_TOPOLOGY]
        scene_state = deepcopy(self.active_scene) if self.active_scene else {
            "schema": "ledgrid.scene-state",
            "schema_version": 1,
            "revision": 100,
            "background": {
                "plugin_id": acceptance.DEFAULT_PLUGIN,
                "provider": "receiver_native",
                "parameter_overrides": {},
                "resolved_parameters": NATIVE_DEFAULTS,
                "bundle_digest": BUNDLE,
                "expected_payload_digest": PAYLOAD,
            },
            "overlays": [{
                "slot_id": acceptance.DEFAULT_CLOCK_OVERLAY,
                "component": {
                    "plugin_id": acceptance.DEFAULT_CLOCK_OVERLAY,
                    "provider": "python",
                    "parameter_overrides": {},
                    "resolved_parameters": {"show_seconds": True},
                },
                "enabled": True,
                "opacity": 255,
                "placement": {
                    "strip_translation": 0,
                    "led_translation": 0,
                    "clip_policy": "clip_to_wall",
                },
                "stale_policy": {"policy": "clear_after_lease", "lease_ms": 3_000},
            }],
        }
        return {
            "updated_at": self.clock.time(),
            "last_command_id": self.command_id,
            "controller_release_id": "release-1",
            "release_consistent": True,
            "installation_profile_digest": PROFILE,
            "scene": {"provider_mode": "python_host" if python_mode else "receiver_native"},
            "scene_state": scene_state,
            "receiver_hybrid": {
                "healthy": self.mode == "native",
                "operational": self.mode == "native",
                "telemetry_complete": self.mode == "native",
                "release_acceptance": self.mode == "native",
                "fallback_active": False,
                "error": None,
                "source_scene_revision": scene_state.get("revision"),
                "context_digest": CONTEXT,
                "driver": native,
            },
            "driver_stats": {
                "devices": [self._device(index) for index in range(5)],
                "aggregate": {
                    "num_devices": 5,
                    "strip_count": 33,
                    "total_leds": 33 * 138,
                    "errors": 0,
                    "receiver_crc_errors": 0,
                    "receiver_spi_queue_errors": 0,
                    "receiver_display_errors": 0,
                    "receiver_status_misses": 0,
                    "receiver_local_missed_deadlines": 0,
                    "device_map": device_map,
                    "native_background": native,
                },
            },
        }


class ReceiverNativePhysicalAcceptanceTests(unittest.TestCase):
    @staticmethod
    def h2_companion():
        subgates = (
            "h2.transaction-compensation",
            "h2.clock-boundary-lease-restart-repair",
            "h2.dense-streamed-canary",
            "h2.python-animation-sweep",
        )
        return {
            "schema": acceptance.COMPANION_SCHEMA,
            "schema_version": 1,
            "gate": "H2",
            "controller_release_id": "release-1",
            "artifact": {
                "bundle_digest": BUNDLE,
                "payload_digest": PAYLOAD,
            },
            "subgate_results": {
                subgate: {
                    "passed": True,
                    "evidence": [{"receipt": f"receipt-{index}"}],
                }
                for index, subgate in enumerate(subgates)
            },
        }

    def test_real_release_default_is_thirty_minutes_but_short_runs_are_valid(self):
        self.assertEqual(
            acceptance.AcceptanceConfig().duration_seconds,
            30 * 60,
        )
        short = acceptance.AcceptanceConfig(
            duration_seconds=0.05, sample_interval_seconds=0.01
        )
        self.assertEqual(short.workload, "default")
        with self.assertRaisesRegex(ValueError, "at least 1800"):
            acceptance.AcceptanceConfig(
                duration_seconds=1_799,
                require_complete_gate=True,
            )
        complete = acceptance.AcceptanceConfig(
            duration_seconds=1_800,
            require_complete_gate=True,
        )
        self.assertEqual(complete.duration_seconds, 1_800)
        for field in (
            {"duration_seconds": 0},
            {"sample_interval_seconds": float("nan")},
            {"timeout_seconds": True},
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                acceptance.AcceptanceConfig(**field)

    def test_maximum_workload_uses_schema_maxima_and_keeps_defaults(self):
        descriptor = _catalog()[0]
        self.assertEqual(
            acceptance.maximum_work_parameters(descriptor),
            {"layers": 5, "motion": 1.0, "shimmer": True},
        )

    def test_happy_h2_run_proves_exact_roster_deltas_skew_and_restoration(self):
        clock = _Clock()
        api = _API(clock)
        report = acceptance.run_acceptance(
            acceptance.AcceptanceConfig(
                duration_seconds=0.2,
                sample_interval_seconds=0.1,
                timeout_seconds=0.2,
            ),
            api,
            monotonic=clock.monotonic,
            wall_time=clock.time,
            sleep=clock.sleep,
        )
        self.assertTrue(report["passed"], report)
        self.assertTrue(report["slice_passed"])
        self.assertFalse(report["full_gate_passed"])
        self.assertIn(
            "h2.transaction-compensation",
            report["gate_coverage"]["outstanding_subgates"],
        )
        self.assertEqual(report["evaluation"]["sample_count"], 3)
        self.assertEqual(
            report["evaluation"]["reset_detection"]["reset_delta"], 0
        )
        self.assertLess(report["evaluation"]["timing"]["maximum_skew_us"], 16_667)
        self.assertEqual(report["restoration"]["method"], "receiver-native-recover")
        self.assertEqual(api.mode, "python")
        native_scene = next(
            payload for method, path, payload in api.calls
            if method == "PUT" and path == "/api/v1/scene"
        )
        self.assertEqual(native_scene["overlays"][0]["slot_id"], "clock_overlay")
        timing = report["evaluation"]["sampled_timing_percentiles"]
        self.assertIn("not receiver event histograms", timing["method"])
        self.assertEqual(timing["receivers"]["4"]["display"]["max_us"], 4_404)
        self.assertEqual(len(report["evaluation"]["spi_series"]), 3)
        self.assertEqual(len(report["evaluation"]["memory_cache_series"]), 3)

    def test_exact_binding_context_profile_and_tail_topology_fail_closed(self):
        clock = _Clock()
        api = _API(clock)
        api.mode = "native"
        api.native_started = clock.monotonic()
        scene, identity = acceptance.resolve_acceptance_scene(
            api, acceptance.AcceptanceConfig()
        )
        api.active_scene = scene
        good = acceptance.normalize_sample(
            api.status(), elapsed_seconds=0.0, sampled_at=clock.time()
        )
        self.assertEqual(acceptance.evaluate_sample(good, identity), [])
        mutations = (
            (lambda sample: sample["aggregate"]["device_map"][2].update(
                bus=0
            ), "aggregate device map"),
            (lambda sample: sample["aggregate"]["device_map"][4].update(
                physical_output_lane_mask=0x01
            ), "aggregate device map"),
            (lambda sample: sample["aggregate"]["device_map"][3].update(
                reverse_host_strip_order=False
            ), "aggregate device map"),
            (lambda sample: sample["native_background"]["capability_report"]
             ["devices"][2].update(chip_select=0), "capability report"),
            (lambda sample: sample["devices"][4].update(
                receiver_native_active_local_strips=8
            ), "local width"),
            (lambda sample: sample["devices"][2].update(
                receiver_native_active_payload_digest="0" * 64
            ), "payload"),
            (lambda sample: sample["devices"][3].update(
                receiver_active_context_digest="0" * 64
            ), "context"),
            (lambda sample: sample["devices"][1].update(
                receiver_profile_active_global_digest="0" * 64
            ), "profile"),
        )
        for mutate, expected in mutations:
            sample = deepcopy(good)
            mutate(sample)
            with self.subTest(expected=expected):
                self.assertTrue(any(
                    expected in failure
                    for failure in acceptance.evaluate_sample(sample, identity)
                ))

    def test_stale_unanimous_parameters_and_missing_clock_fail_closed(self):
        clock = _Clock()
        api = _API(clock)
        scene, identity = acceptance.resolve_acceptance_scene(
            api, acceptance.AcceptanceConfig()
        )
        api.active_scene = scene
        api.mode = "native"
        api.native_started = clock.monotonic()
        good = acceptance.normalize_sample(
            api.status(), elapsed_seconds=0.0, sampled_at=clock.time()
        )
        self.assertEqual(acceptance.evaluate_sample(good, identity), [])

        stale = deepcopy(good)
        stale["native_background"]["parameter_digest"] = "0" * 64
        for device in stale["devices"]:
            device["receiver_native_active_parameter_digest"] = "0" * 64
        failures = acceptance.evaluate_sample(stale, identity)
        self.assertTrue(any("parameter binding" in item for item in failures))
        self.assertTrue(any(
            "receiver_native_active_parameter_digest" in item
            for item in failures
        ))

        for mutate in (
            lambda sample: sample["scene_state"].update(overlays=[]),
            lambda sample: sample["scene_state"]["overlays"][0].update(enabled=False),
            lambda sample: sample["scene_state"]["overlays"][0].update(
                stale_policy={"policy": "hold"}
            ),
        ):
            candidate = deepcopy(good)
            mutate(candidate)
            with self.subTest(scene=candidate["scene_state"]):
                self.assertTrue(any(
                    "clock overlay" in item
                    for item in acceptance.evaluate_sample(candidate, identity)
                ))

    def test_missing_or_changed_controller_release_identity_fails_closed(self):
        clock = _Clock()
        api = _API(clock)
        scene, identity = acceptance.resolve_acceptance_scene(
            api, acceptance.AcceptanceConfig()
        )
        api.active_scene = scene
        api.mode = "native"
        api.native_started = clock.monotonic()
        first = acceptance.normalize_sample(
            api.status(), elapsed_seconds=0.0, sampled_at=clock.time()
        )
        missing = deepcopy(first)
        missing["controller_release_id"] = None
        self.assertTrue(any(
            "release identity is unavailable" in failure
            for failure in acceptance.evaluate_sample(missing, identity)
        ))

        clock.sleep(1.0)
        final = acceptance.normalize_sample(
            api.status(), elapsed_seconds=1.0, sampled_at=clock.time()
        )
        final["controller_release_id"] = "release-2"
        series = acceptance.evaluate_series([first, final], identity)
        self.assertFalse(series["passed"])
        self.assertIn(
            "controller release identity changed during the soak",
            series["failures"],
        )

    def test_series_reports_error_watchdog_cadence_and_reset_deltas(self):
        clock = _Clock()
        api = _API(clock)
        api.mode = "native"
        api.native_started = clock.monotonic()
        scene, identity = acceptance.resolve_acceptance_scene(
            api, acceptance.AcceptanceConfig()
        )
        api.active_scene = scene
        first = acceptance.normalize_sample(
            api.status(), elapsed_seconds=0.0, sampled_at=clock.time()
        )
        clock.sleep(1.0)
        final = acceptance.normalize_sample(
            api.status(), elapsed_seconds=1.0, sampled_at=clock.time()
        )
        final["devices"][0]["receiver_crc_errors"] = 1
        final["devices"][1]["receiver_native_watchdog_events"] = 1
        final["devices"][2]["receiver_local_missed_deadlines"] = 1
        final["devices"][3]["receiver_operation_sequence"] = 0
        result = acceptance.evaluate_series(
            [first, final], identity
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["deltas_by_receiver"]["0"]["receiver_crc_errors"], 1)
        self.assertEqual(
            result["deltas_by_receiver"]["1"]["receiver_native_watchdog_events"], 1
        )
        self.assertEqual(result["reset_detection"]["reset_delta"], 1)

    def test_explicit_reset_and_boot_deltas_fail_with_counter_fallback(self):
        clock = _Clock()
        api = _API(clock)
        api.mode = "native"
        api.native_started = clock.monotonic()
        scene, identity = acceptance.resolve_acceptance_scene(
            api, acceptance.AcceptanceConfig()
        )
        api.active_scene = scene
        first = acceptance.normalize_sample(
            api.status(), elapsed_seconds=0.0, sampled_at=clock.time()
        )
        clock.sleep(1.0)
        final = acceptance.normalize_sample(
            api.status(), elapsed_seconds=1.0, sampled_at=clock.time()
        )
        for device in first["devices"]:
            device["receiver_reset_count"] = 4
            device["receiver_boot_count"] = 9
        for device in final["devices"]:
            device["receiver_reset_count"] = 4
            device["receiver_boot_count"] = 9
        final["devices"][0]["receiver_reset_count"] = 5
        final["devices"][1]["receiver_boot_count"] = 10
        result = acceptance.evaluate_series(
            [first, final], identity
        )
        self.assertFalse(result["passed"])
        self.assertEqual(
            result["reset_detection"]["explicit_reset_counter_deltas"]["0"]
            ["receiver_reset_count"],
            1,
        )
        self.assertEqual(
            result["reset_detection"]["explicit_reset_counter_deltas"]["1"]
            ["receiver_boot_count"],
            1,
        )

        for sample in (first, final):
            for device in sample["devices"]:
                device["receiver_reset_count"] = None
                device["receiver_boot_count"] = None
        fallback = acceptance.evaluate_series(
            [first, final], identity
        )
        self.assertTrue(fallback["passed"], fallback)
        self.assertTrue(fallback["reset_detection"]["continuity_counters"])

    def test_missing_exposed_status_field_fails_closed(self):
        clock = _Clock()
        api = _API(clock)
        api.mode = "native"
        api.native_started = clock.monotonic()
        sample = acceptance.normalize_sample(
            api.status(), elapsed_seconds=0.0, sampled_at=clock.time()
        )
        sample["devices"][4]["receiver_last_encode_us"] = None
        failures = acceptance.evaluate_sample(
            sample, {"bundle_digest": BUNDLE, "payload_digest": PAYLOAD}
        )
        self.assertTrue(any(
            "required status field receiver_last_encode_us" in failure
            for failure in failures
        ))

    def test_h4_series_subgates_require_all_spi_and_memory_fields(self):
        clock = _Clock()
        api = _API(clock)
        api.mode = "native"
        api.native_started = clock.monotonic()
        first = acceptance.normalize_sample(
            api.status(), elapsed_seconds=0.0, sampled_at=clock.time()
        )
        clock.sleep(1_800.0)
        final = acceptance.normalize_sample(
            api.status(), elapsed_seconds=1_800.0, sampled_at=clock.time()
        )
        final["devices"][4]["bytes_sent"] = None
        final["devices"][2]["receiver_native_free_bytes"] = None
        evaluation = acceptance.evaluate_series(
            [first, final], {"bundle_digest": BUNDLE, "payload_digest": PAYLOAD}
        )
        self.assertFalse(evaluation["passed"])
        self.assertFalse(evaluation["series_fields_complete"])
        self.assertFalse(
            evaluation["series_field_availability"]["spi"]["4"]["bytes_sent"]
        )
        self.assertFalse(
            evaluation["series_field_availability"]["memory_cache"]["2"]
            ["receiver_native_free_bytes"]
        )
        coverage = acceptance.gate_coverage(
            "H4-default",
            slice_passed=True,
            evaluation=evaluation,
            restoration={"passed": True},
            identity={"bundle_digest": BUNDLE, "payload_digest": PAYLOAD},
            controller_release_id="release-1",
        )
        self.assertFalse(
            coverage["subgate_results"]["h4.streamed-spi-series"]["passed"]
        )
        self.assertFalse(
            coverage["subgate_results"]["h4.memory-cache-series"]["passed"]
        )

    def test_full_h2_claim_requires_valid_companion_evidence(self):
        clock = _Clock()
        api = _API(clock)
        config = acceptance.AcceptanceConfig(
            duration_seconds=1_800,
            sample_interval_seconds=1_800,
            timeout_seconds=0.2,
            require_complete_gate=True,
        )
        incomplete = acceptance.run_acceptance(
            config,
            api,
            monotonic=clock.monotonic,
            wall_time=clock.time,
            sleep=clock.sleep,
        )
        self.assertTrue(incomplete["slice_passed"])
        self.assertFalse(incomplete["passed"])
        self.assertFalse(incomplete["full_gate_passed"])

        clock = _Clock()
        api = _API(clock)
        complete = acceptance.run_acceptance(
            config,
            api,
            companion_reports=[self.h2_companion()],
            monotonic=clock.monotonic,
            wall_time=clock.time,
            sleep=clock.sleep,
        )
        self.assertTrue(complete["passed"], complete)
        self.assertTrue(complete["full_gate_passed"])

        wrong = self.h2_companion()
        wrong["artifact"]["payload_digest"] = "0" * 64
        coverage = acceptance.gate_coverage(
            "H2",
            slice_passed=True,
            evaluation={"passed": True},
            restoration={"passed": True},
            identity={"bundle_digest": BUNDLE, "payload_digest": PAYLOAD},
            controller_release_id="release-1",
            companion_reports=[wrong],
        )
        self.assertFalse(coverage["full_gate_passed"])
        self.assertIn("different native binding", coverage["companion_failures"][0])

    def test_companion_soak_requires_1800_observed_seconds(self):
        companion = {
            "schema": acceptance.COMPANION_SCHEMA,
            "schema_version": 1,
            "gate": "H4-maximum",
            "controller_release_id": "release-1",
            "artifact": {
                "bundle_digest": BUNDLE,
                "payload_digest": PAYLOAD,
            },
            "subgate_results": {
                "h4.maximum-native-clock-soak": {
                    "passed": True,
                    "observed_seconds": 1_799,
                    "evidence": [{"receipt": "short"}],
                },
            },
        }
        coverage = acceptance.gate_coverage(
            "H4-default",
            slice_passed=True,
            evaluation={
                "observed_seconds": 1_800,
                "series_fields_complete": True,
                "spi_series": [{}],
                "memory_cache_series": [{}],
            },
            restoration={"passed": True},
            identity={"bundle_digest": BUNDLE, "payload_digest": PAYLOAD},
            controller_release_id="release-1",
            companion_reports=[companion],
        )
        self.assertFalse(coverage["full_gate_passed"])
        self.assertIn("no valid 1800-second soak", coverage["companion_failures"][0])
        companion["subgate_results"]["h4.maximum-native-clock-soak"][
            "observed_seconds"
        ] = 1_800
        coverage = acceptance.gate_coverage(
            "H4-default",
            slice_passed=True,
            evaluation={
                "observed_seconds": 1_800,
                "series_fields_complete": True,
                "spi_series": [{}],
                "memory_cache_series": [{}],
            },
            restoration={"passed": True},
            identity={"bundle_digest": BUNDLE, "payload_digest": PAYLOAD},
            controller_release_id="release-1",
            companion_reports=[companion],
        )
        self.assertTrue(
            coverage["subgate_results"]["h4.maximum-native-clock-soak"]["passed"]
        )

    def test_companion_loader_rejects_direct_and_parent_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir).resolve()
            evidence = root / "evidence.json"
            evidence.write_text(json.dumps({"schema": acceptance.COMPANION_SCHEMA}))
            self.assertEqual(
                acceptance.load_companion_reports([evidence])[0]["schema"],
                acceptance.COMPANION_SCHEMA,
            )

            direct = root / "direct.json"
            direct.symlink_to(evidence)
            with self.assertRaisesRegex(
                acceptance.PhysicalAcceptanceError, "symbolic link"
            ):
                acceptance.load_companion_reports([direct])

            real_parent = root / "real-parent"
            real_parent.mkdir()
            nested = real_parent / "nested.json"
            nested.write_text(evidence.read_text())
            linked_parent = root / "linked-parent"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            with self.assertRaisesRegex(
                acceptance.PhysicalAcceptanceError, "symbolic link"
            ):
                acceptance.load_companion_reports([linked_parent / "nested.json"])

    def test_activation_failure_still_restores_python_and_direct_fallback_is_backup(self):
        clock = _Clock()
        api = _API(clock, fail_activate=True)
        report = acceptance.run_acceptance(
            acceptance.AcceptanceConfig(
                duration_seconds=0.1,
                sample_interval_seconds=0.05,
                timeout_seconds=0.2,
            ),
            api,
            monotonic=clock.monotonic,
            wall_time=clock.time,
            sleep=clock.sleep,
        )
        self.assertFalse(report["passed"])
        self.assertTrue(report["restoration"]["passed"])
        self.assertEqual(api.mode, "python")

        backup_api = _API(clock, fail_recover=True)
        backup = acceptance.restore_python_fallback(
            backup_api,
            acceptance.AcceptanceConfig(timeout_seconds=0.2),
            {
                "plugin_id": acceptance.DEFAULT_FALLBACK,
                "provider": "python",
                "parameter_overrides": {},
                "resolved_parameters": {"speed": 0.4},
            },
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        self.assertTrue(backup["passed"])
        self.assertEqual(backup["method"], "direct-python-scene")


if __name__ == "__main__":
    unittest.main()
