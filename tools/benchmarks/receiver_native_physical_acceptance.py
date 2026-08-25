#!/usr/bin/env python3
"""Collect fail-closed H2/H4 evidence for a managed receiver-native scene.

The runner uses only the public HTTP API.  It installs and starts one managed
native background with the Python clock overlay, samples the exact five-board
status throughout the requested window, and always returns the wall to the
recorded Python fallback before it exits.  It never flashes firmware, restarts
services, or edits durable receiver configuration.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from animation.core.native_background_operation import (  # noqa: E402
    encode_native_parameters,
)


SCHEMA = "ledgrid.receiver-native-physical-acceptance"
SCHEMA_VERSION = 1
COMPANION_SCHEMA = "ledgrid.physical-acceptance-companion"
DEFAULT_SOAK_SECONDS = 30 * 60
MIN_COMPLETE_GATE_SOAK_SECONDS = 30 * 60
DEFAULT_SAMPLE_INTERVAL_SECONDS = 5.0
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_TARGET = "ledgridwall.local"
DEFAULT_PLUGIN = "aurora_curtains_native"
DEFAULT_FALLBACK = "aurora_curtains"
DEFAULT_CLOCK_OVERLAY = "clock_overlay"

DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
EXPECTED_TOPOLOGY = (
    {
        "logical_device": 0,
        "local_strip_count": 8,
        "global_strip_offset": 0,
        "bus": 0,
        "chip_select": 0,
        "physical_output_lane_mask": 0xFF,
        "reverse_host_strip_order": False,
        "reverse_native_strip_order": False,
    },
    {
        "logical_device": 1,
        "local_strip_count": 8,
        "global_strip_offset": 8,
        "bus": 0,
        "chip_select": 1,
        "physical_output_lane_mask": 0xFF,
        "reverse_host_strip_order": False,
        "reverse_native_strip_order": False,
    },
    {
        "logical_device": 2,
        "local_strip_count": 8,
        "global_strip_offset": 24,
        "bus": 1,
        "chip_select": 1,
        "physical_output_lane_mask": 0xFF,
        "reverse_host_strip_order": True,
        "reverse_native_strip_order": True,
    },
    {
        "logical_device": 3,
        "local_strip_count": 8,
        "global_strip_offset": 16,
        "bus": 1,
        "chip_select": 0,
        "physical_output_lane_mask": 0xFF,
        "reverse_host_strip_order": True,
        "reverse_native_strip_order": True,
    },
    {
        "logical_device": 4,
        "local_strip_count": 1,
        "global_strip_offset": 32,
        "bus": 1,
        "chip_select": 2,
        "physical_output_lane_mask": 0x01,
        "reverse_host_strip_order": False,
        "reverse_native_strip_order": False,
    },
)
EXPECTED_TOPOLOGY_BY_ID = {
    item["logical_device"]: item for item in EXPECTED_TOPOLOGY
}

ERROR_COUNTERS = (
    "errors",
    "receiver_crc_errors",
    "receiver_spi_queue_errors",
    "receiver_display_errors",
    "receiver_status_misses",
)
CADENCE_COUNTERS = (
    "receiver_local_frames_rendered",
    "receiver_local_cadence_deadlines",
    "receiver_local_missed_deadlines",
)
RESET_CONTINUITY_COUNTERS = (
    "receiver_operation_sequence",
    "receiver_native_state_generation",
    "spi_transfers",
    "bytes_sent",
    "crc_bytes_sent",
    "receiver_packets",
    "receiver_crc_ok_packets",
    "receiver_frames_accepted",
    "receiver_frames_displayed",
    "receiver_frames_superseded",
    "receiver_publish_drops",
    *ERROR_COUNTERS,
    *CADENCE_COUNTERS,
    "receiver_native_watchdog_events",
)

SPI_SERIES_FIELDS = (
    "spi_transfers",
    "bytes_sent",
    "crc_bytes_sent",
    "receiver_packets",
    "receiver_crc_ok_packets",
    "receiver_crc_errors",
    "receiver_spi_queue_errors",
    "receiver_status_misses",
)
MEMORY_CACHE_SERIES_FIELDS = (
    "receiver_native_capacity_bytes",
    "receiver_native_used_bytes",
    "receiver_native_free_bytes",
    "receiver_native_reserve_bytes",
    "receiver_native_reclaimable_bytes",
    "receiver_native_state_generation",
    "receiver_native_quarantines",
)
SOAK_SUBGATES = frozenset({
    "h2.native-skew-drift-soak",
    "h4.default-native-clock-soak",
    "h4.maximum-native-clock-soak",
})

# This runner deliberately covers only the non-destructive, API-observable
# slices below. A complete phase gate also needs explicit failure injection,
# restart/lease/boundary exercises, and retained external benchmark evidence.
GATE_SUBGATES = {
    "H2": (
        ("h2.exact-five-state", "runner"),
        ("h2.native-skew-drift-soak", "runner"),
        ("h2.transaction-compensation", "companion"),
        ("h2.clock-boundary-lease-restart-repair", "companion"),
        ("h2.dense-streamed-canary", "companion"),
        ("h2.python-animation-sweep", "companion"),
    ),
    "H4-default": (
        ("h4.default-native-clock-soak", "runner"),
        ("h4.maximum-native-clock-soak", "companion"),
        ("h4.receiver-timing-distributions", "companion"),
        ("h4.streamed-spi-series", "runner"),
        ("h4.memory-cache-series", "runner"),
        ("h4.python-fallback-restoration", "runner"),
        ("h4.retained-rollback-assets", "companion"),
    ),
    "H4-maximum": (
        ("h4.default-native-clock-soak", "companion"),
        ("h4.maximum-native-clock-soak", "runner"),
        ("h4.receiver-timing-distributions", "companion"),
        ("h4.streamed-spi-series", "runner"),
        ("h4.memory-cache-series", "runner"),
        ("h4.python-fallback-restoration", "runner"),
        ("h4.retained-rollback-assets", "companion"),
    ),
}

REQUIRED_DEVICE_STATUS_FIELDS = (
    "errors",
    "receiver_status_version",
    "receiver_capabilities",
    "receiver_logical_device",
    "receiver_operation_sequence",
    "receiver_crc_errors",
    "receiver_spi_queue_errors",
    "receiver_display_errors",
    "receiver_status_misses",
    "receiver_last_encode_us",
    "receiver_last_show_us",
    "receiver_declared_cadence_hz",
    "receiver_local_frames_rendered",
    "receiver_local_cadence_deadlines",
    "receiver_local_missed_deadlines",
    "receiver_last_frame_scene_time_us",
    "receiver_overlay_composite_frames",
    "receiver_overlay_last_composite_us",
    "receiver_overlay_max_composite_us",
    "receiver_native_capacity_bytes",
    "receiver_native_used_bytes",
    "receiver_native_free_bytes",
    "receiver_native_reserve_bytes",
    "receiver_native_reclaimable_bytes",
    "receiver_native_state_generation",
    "receiver_native_active_cadence_hz",
    "receiver_native_last_render_us",
    "receiver_native_max_phase_us",
    "receiver_native_watchdog_events",
)


class PhysicalAcceptanceError(RuntimeError):
    """The target cannot produce trustworthy native acceptance evidence."""


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, (list, tuple)) else ()


def _utc_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


@dataclass(frozen=True)
class AcceptanceConfig:
    target: str = DEFAULT_TARGET
    selector: str = DEFAULT_PLUGIN
    gate: str = "H2"
    duration_seconds: float = DEFAULT_SOAK_SECONDS
    sample_interval_seconds: float = DEFAULT_SAMPLE_INTERVAL_SECONDS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    fallback_plugin: str = DEFAULT_FALLBACK
    clock_overlay_plugin: str = DEFAULT_CLOCK_OVERLAY
    require_complete_gate: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError("target must be a non-empty host name")
        for name in ("selector", "fallback_plugin", "clock_overlay_plugin"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty component ID")
        if self.gate not in {"H2", "H4-default", "H4-maximum"}:
            raise ValueError("gate must be H2, H4-default, or H4-maximum")
        if type(self.require_complete_gate) is not bool:
            raise TypeError("require_complete_gate must be boolean")
        for name in (
            "duration_seconds", "sample_interval_seconds", "timeout_seconds"
        ):
            value = _finite_number(getattr(self, name))
            if value is None or value <= 0:
                raise ValueError(f"{name} must be finite and greater than zero")
        if (
            self.require_complete_gate
            and self.duration_seconds < MIN_COMPLETE_GATE_SOAK_SECONDS
        ):
            raise ValueError(
                "complete H2/H4 claims require at least 1800 requested soak seconds"
            )

    @property
    def workload(self) -> str:
        return "maximum" if self.gate == "H4-maximum" else "default"


class HTTPAPI:
    """Small JSON client for the public wall API."""

    def __init__(self, target: str) -> None:
        self.host = target.rsplit("@", 1)[-1].strip().rstrip("/")
        if not self.host:
            raise ValueError("target must contain a host name")

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: Mapping[str, Any] | None = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            f"http://{self.host}:5000{path}",
            data=body,
            method=method,
            headers={"Content-Type": "application/json"} if body else {},
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read())
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise PhysicalAcceptanceError(
                f"wall API {method} {path} failed: {exc}"
            ) from exc
        if not isinstance(result, dict):
            raise PhysicalAcceptanceError(
                f"wall API {method} {path} returned no JSON object"
            )
        return result


def _component(
    components: Sequence[Any], component_id: str, *, provider: str, role: str
) -> dict[str, Any]:
    matches = [
        item for item in components
        if isinstance(item, dict)
        and item.get("plugin_id") == component_id
        and item.get("provider", "python") == provider
        and item.get("role") == role
    ]
    if len(matches) != 1:
        raise PhysicalAcceptanceError(
            f"catalog must contain exactly one {provider} {role} {component_id!r}"
        )
    return dict(matches[0])


def maximum_work_parameters(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve a deterministic schema-valid upper-work parameter set."""

    resolved = dict(_mapping(descriptor.get("defaults")))
    for name, raw_rule in _mapping(descriptor.get("parameter_schema")).items():
        rule = _mapping(raw_rule)
        kind = rule.get("type")
        if kind in {"int", "float"} and _finite_number(rule.get("max")) is not None:
            resolved[name] = rule["max"]
        elif kind == "bool":
            resolved[name] = True
        elif kind in {"str", "string"} and _sequence(rule.get("options")):
            resolved[name] = _sequence(rule["options"])[-1]
        elif name not in resolved and "default" in rule:
            resolved[name] = rule["default"]
    return resolved


def _resolved_parameters(
    descriptor: Mapping[str, Any], *, workload: str
) -> dict[str, Any]:
    if workload == "maximum":
        return maximum_work_parameters(descriptor)
    return dict(_mapping(descriptor.get("defaults")))


def resolve_acceptance_scene(
    api: Any, config: AcceptanceConfig
) -> tuple[dict[str, Any], dict[str, Any]]:
    catalog = api.request(
        "/api/v1/components", timeout=config.timeout_seconds
    ).get("components")
    if not isinstance(catalog, list):
        raise PhysicalAcceptanceError("target returned no unified component catalog")
    native = _component(
        catalog, config.selector, provider="receiver_native", role="background"
    )
    fallback = _component(
        catalog, config.fallback_plugin, provider="python", role="background"
    )
    clock = _component(
        catalog, config.clock_overlay_plugin, provider="python", role="overlay"
    )
    build = _mapping(native.get("build"))
    bundle_digest = build.get("bundle_digest")
    payload_digest = build.get("expected_payload_digest")
    if (
        not isinstance(bundle_digest, str)
        or DIGEST_PATTERN.fullmatch(bundle_digest) is None
        or not isinstance(payload_digest, str)
        or DIGEST_PATTERN.fullmatch(payload_digest) is None
    ):
        raise PhysicalAcceptanceError(
            "native catalog entry has no exact managed bundle/payload binding"
        )
    availability = _mapping(native.get("availability"))
    if availability.get("selectable") is False or availability.get("state") == "gated":
        raise PhysicalAcceptanceError("native catalog entry is not selectable")

    fallback_ref = {
        "plugin_id": config.fallback_plugin,
        "provider": "python",
        "parameter_overrides": {},
        "resolved_parameters": dict(_mapping(fallback.get("defaults"))),
    }
    native_parameters = _resolved_parameters(native, workload=config.workload)
    clock_parameters = _resolved_parameters(clock, workload=config.workload)
    try:
        parameter_digest = encode_native_parameters(
            _mapping(native.get("parameter_schema")), native_parameters
        ).digest
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise PhysicalAcceptanceError(
            f"native catalog parameters cannot be bound to an exact digest: {exc}"
        ) from exc
    scene = {
        "schema": "ledgrid.scene-state",
        "schema_version": 1,
        "revision": time.time_ns() & ((1 << 64) - 1),
        "background": {
            "plugin_id": native["plugin_id"],
            "provider": "receiver_native",
            "parameter_overrides": {},
            "resolved_parameters": native_parameters,
            "bundle_digest": bundle_digest,
            "expected_payload_digest": payload_digest,
        },
        "overlays": [{
            "slot_id": config.clock_overlay_plugin,
            "component": {
                "plugin_id": config.clock_overlay_plugin,
                "provider": "python",
                "parameter_overrides": {},
                "resolved_parameters": clock_parameters,
            },
            "enabled": True,
            "opacity": 255,
            "placement": {
                "strip_translation": 0,
                "led_translation": 0,
                "clip_policy": "clip_to_wall",
            },
            "stale_policy": {
                "policy": "clear_after_lease",
                "lease_ms": 3_000,
            },
        }],
        "known_python_fallback": fallback_ref,
    }
    identity = {
        "plugin_id": native["plugin_id"],
        "clock_plugin_id": clock["plugin_id"],
        "bundle_digest": bundle_digest,
        "payload_digest": payload_digest,
        "declared_cadence_hz": _mapping(native.get("cadence")).get(
            "preferred_fps"
        ),
        "native_parameters": native_parameters,
        "parameter_digest": parameter_digest,
        "clock_parameters": clock_parameters,
        "fallback": fallback_ref,
    }
    return scene, identity


def _native_driver(status: Mapping[str, Any]) -> Mapping[str, Any]:
    receiver = _mapping(status.get("receiver_hybrid"))
    candidate = _mapping(receiver.get("driver"))
    if candidate:
        return candidate
    aggregate = _mapping(_mapping(status.get("driver_stats")).get("aggregate"))
    return _mapping(aggregate.get("native_background"))


def _normalized_device(raw: Any, index: int) -> dict[str, Any]:
    status = _mapping(raw)
    keys = (
        "errors",
        "receiver_status_seen",
        "receiver_status_version",
        "receiver_capabilities",
        "receiver_logical_device",
        "receiver_base_mode",
        "receiver_last_result",
        "receiver_active_context_digest",
        "receiver_vibe_revision",
        "receiver_vibe_digest",
        "receiver_plant_modifier_revision",
        "receiver_plant_modifier_digest",
        "receiver_active_session_id",
        "receiver_operation_sequence",
        "spi_transfers",
        "bytes_sent",
        "crc_bytes_sent",
        "receiver_crc_errors",
        "receiver_packets",
        "receiver_crc_ok_packets",
        "receiver_frames_accepted",
        "receiver_frames_displayed",
        "receiver_frames_superseded",
        "receiver_publish_drops",
        "receiver_spi_queue_errors",
        "receiver_display_errors",
        "receiver_status_misses",
        "receiver_last_crc_us",
        "receiver_last_copy_us",
        "receiver_last_encode_us",
        "receiver_last_show_us",
        "receiver_declared_cadence_hz",
        "receiver_local_frames_rendered",
        "receiver_local_cadence_deadlines",
        "receiver_local_missed_deadlines",
        "receiver_last_local_render_us",
        "receiver_max_local_render_us",
        "receiver_last_frame_scene_time_us",
        "receiver_overlay_composite_frames",
        "receiver_overlay_last_composite_us",
        "receiver_overlay_max_composite_us",
        "receiver_profile_active_global_digest",
        "receiver_profile_active_payload_digest",
        "receiver_native_result_name",
        "receiver_native_watchdog_phase_name",
        "receiver_native_ready",
        "receiver_native_cache_integrity_ok",
        "receiver_native_executing",
        "receiver_native_capacity_bytes",
        "receiver_native_used_bytes",
        "receiver_native_free_bytes",
        "receiver_native_reserve_bytes",
        "receiver_native_reclaimable_bytes",
        "receiver_native_state_generation",
        "receiver_native_active_bundle_digest",
        "receiver_native_active_payload_digest",
        "receiver_native_rollback_bundle_digest",
        "receiver_native_rollback_payload_digest",
        "receiver_native_quarantine_payload_digest",
        "receiver_native_active_cadence_hz",
        "receiver_native_active_local_strips",
        "receiver_native_active_global_strips",
        "receiver_native_active_leds_per_strip",
        "receiver_native_active_global_strip_offset",
        "receiver_native_active_parameter_digest",
        "receiver_native_last_load_us",
        "receiver_native_last_initialize_us",
        "receiver_native_last_context_us",
        "receiver_native_last_render_us",
        "receiver_native_max_phase_us",
        "receiver_native_watchdog_events",
        "receiver_native_quarantines",
        "receiver_reset_count",
        "receiver_boot_count",
        "receiver_reset_reason",
    )
    return {"logical_device": index, **{key: status.get(key) for key in keys}}


def normalize_sample(
    status: Mapping[str, Any], *, elapsed_seconds: float, sampled_at: float
) -> dict[str, Any]:
    driver_stats = _mapping(status.get("driver_stats"))
    aggregate = _mapping(driver_stats.get("aggregate"))
    receiver = _mapping(status.get("receiver_hybrid"))
    scene = _mapping(status.get("scene"))
    scene_state = _mapping(status.get("scene_state"))
    devices = [
        _normalized_device(raw, index)
        for index, raw in enumerate(_sequence(driver_stats.get("devices")))
    ]
    return {
        "sampled_at": _utc_timestamp(sampled_at),
        "sampled_at_unix": sampled_at,
        "elapsed_seconds": elapsed_seconds,
        "status_updated_at": status.get("updated_at"),
        "controller_release_id": status.get("controller_release_id"),
        "release_consistent": status.get("release_consistent"),
        "provider_mode": scene.get("provider_mode"),
        "scene_state": dict(scene_state),
        "scene_revision": receiver.get("source_scene_revision"),
        "installation_profile_digest": status.get("installation_profile_digest"),
        "context_digest": receiver.get("context_digest"),
        "receiver_healthy": receiver.get("healthy"),
        "receiver_operational": receiver.get("operational"),
        "receiver_telemetry_complete": receiver.get("telemetry_complete"),
        "receiver_release_acceptance": receiver.get("release_acceptance"),
        "receiver_fallback_active": receiver.get("fallback_active"),
        "receiver_error": receiver.get("error"),
        "native_background": dict(_native_driver(status)),
        "aggregate": {
            "num_devices": aggregate.get("num_devices"),
            "strip_count": aggregate.get("strip_count"),
            "total_leds": aggregate.get("total_leds"),
            "errors": aggregate.get("errors"),
            "receiver_crc_errors": aggregate.get("receiver_crc_errors"),
            "receiver_spi_queue_errors": aggregate.get(
                "receiver_spi_queue_errors"
            ),
            "receiver_display_errors": aggregate.get("receiver_display_errors"),
            "receiver_status_misses": aggregate.get("receiver_status_misses"),
            "receiver_local_missed_deadlines": aggregate.get(
                "receiver_local_missed_deadlines"
            ),
            "device_map": list(_sequence(aggregate.get("device_map"))),
        },
        "devices": devices,
    }


def _topology_view(value: Any) -> dict[str, Any]:
    item = _mapping(value)
    return {
        "logical_device": item.get("logical_device"),
        "local_strip_count": item.get("local_strip_count"),
        "global_strip_offset": item.get("global_strip_offset"),
        "bus": item.get("bus"),
        "chip_select": item.get("chip_select"),
        "physical_output_lane_mask": item.get("physical_output_lane_mask"),
        "reverse_host_strip_order": item.get("reverse_host_strip_order"),
        "reverse_native_strip_order": item.get("reverse_native_strip_order"),
    }


def evaluate_sample(
    sample: Mapping[str, Any], identity: Mapping[str, Any]
) -> list[str]:
    """Require exact binding, context, profile, and topology on one snapshot."""

    failures: list[str] = []
    if sample.get("release_consistent") is not True:
        failures.append("web/controller release identity is inconsistent")
    release_id = sample.get("controller_release_id")
    if not isinstance(release_id, str) or not release_id.strip():
        failures.append("controller release identity is unavailable")
    for field in (
        "receiver_healthy", "receiver_operational", "receiver_telemetry_complete",
        "receiver_release_acceptance",
    ):
        if sample.get(field) is not True:
            failures.append(f"{field} is not true")
    if sample.get("receiver_fallback_active") is not False:
        failures.append("receiver-native fallback is active")
    if sample.get("receiver_error") is not None:
        failures.append(f"receiver-native manager error: {sample.get('receiver_error')}")
    if sample.get("provider_mode") != "receiver_native":
        failures.append("scene provider mode is not receiver_native")

    scene_state = _mapping(sample.get("scene_state"))
    scene_revision = _integer(scene_state.get("revision"))
    if scene_revision is None or scene_revision != sample.get("scene_revision"):
        failures.append("active scene revision is unavailable or stale")
    active_background = _mapping(scene_state.get("background"))
    if (
        active_background.get("provider") != "receiver_native"
        or active_background.get("plugin_id") != identity.get("plugin_id")
        or active_background.get("bundle_digest") != identity.get("bundle_digest")
        or active_background.get("expected_payload_digest")
        != identity.get("payload_digest")
        or _mapping(active_background.get("resolved_parameters"))
        != _mapping(identity.get("native_parameters"))
    ):
        failures.append("active scene background does not match the requested native binding")
    overlays = [
        item for item in _sequence(scene_state.get("overlays"))
        if _mapping(item).get("slot_id") == identity.get("clock_plugin_id")
    ]
    if len(overlays) != 1:
        failures.append("active scene does not contain exactly one clock overlay")
    else:
        overlay = _mapping(overlays[0])
        component = _mapping(overlay.get("component"))
        stale_policy = _mapping(overlay.get("stale_policy"))
        if (
            overlay.get("enabled") is not True
            or component.get("provider") != "python"
            or component.get("plugin_id") != identity.get("clock_plugin_id")
            or _mapping(component.get("resolved_parameters"))
            != _mapping(identity.get("clock_parameters"))
            or stale_policy != {"policy": "clear_after_lease", "lease_ms": 3_000}
        ):
            failures.append("active clock overlay does not match the requested parameters and lease")

    aggregate = _mapping(sample.get("aggregate"))
    if (
        aggregate.get("num_devices"),
        aggregate.get("strip_count"),
        aggregate.get("total_leds"),
    ) != (5, 33, 33 * 138):
        failures.append("aggregate geometry is not the finalized 33x138 five-receiver wall")
    observed_map = tuple(
        _topology_view(item) for item in _sequence(aggregate.get("device_map"))
    )
    if observed_map != EXPECTED_TOPOLOGY:
        failures.append("aggregate device map does not match the exact finalized topology")

    driver = _mapping(sample.get("native_background"))
    bundle = identity.get("bundle_digest")
    payload = identity.get("payload_digest")
    if driver.get("state") != "active" or driver.get("error") is not None:
        failures.append("native driver is not active without error")
    if driver.get("bundle_digest") != bundle:
        failures.append("native driver bundle binding changed")
    if driver.get("payload_digest") != payload:
        failures.append("native driver payload binding changed")
    if driver.get("parameter_digest") != identity.get("parameter_digest"):
        failures.append("native driver parameter binding changed")
    agreement = _mapping(driver.get("agreement"))
    if agreement.get("exact_roster") is not True:
        failures.append("native driver did not prove exact-roster agreement")
    if agreement.get("verified_receiver_ids") != [0, 1, 2, 3, 4]:
        failures.append("native driver did not verify receiver IDs 0 through 4")

    report = _mapping(driver.get("capability_report"))
    required_capabilities = _integer(report.get("required_capabilities"))
    reported_views = tuple(
        _topology_view(item) for item in _sequence(report.get("devices"))
    )
    if reported_views != EXPECTED_TOPOLOGY:
        failures.append("native capability report topology is not exact")
    if required_capabilities is None or required_capabilities <= 0:
        failures.append("native required-capability mask is unavailable")

    devices = _sequence(sample.get("devices"))
    if len(devices) != 5:
        failures.append(f"sample contains {len(devices)} receiver statuses; expected 5")
        return failures
    context_digest = sample.get("context_digest")
    profile_digest = sample.get("installation_profile_digest")
    if not isinstance(context_digest, str) or DIGEST_PATTERN.fullmatch(context_digest) is None:
        failures.append("manager context digest is unavailable")
    if (
        not isinstance(profile_digest, str)
        or DIGEST_PATTERN.fullmatch(profile_digest) is None
        or profile_digest == "0" * 64
    ):
        failures.append("selected installation-profile digest is unavailable")

    unanimous_fields = {
        "receiver_active_context_digest": context_digest,
        "receiver_profile_active_global_digest": profile_digest,
        "receiver_native_active_bundle_digest": bundle,
        "receiver_native_active_payload_digest": payload,
        "receiver_native_active_parameter_digest": identity.get("parameter_digest"),
    }
    for receiver_id, raw_device in enumerate(devices):
        device = _mapping(raw_device)
        expected = EXPECTED_TOPOLOGY_BY_ID[receiver_id]
        for field in REQUIRED_DEVICE_STATUS_FIELDS:
            value = device.get(field)
            if _integer(value) is None or value < 0:
                failures.append(
                    f"receiver {receiver_id} required status field {field} is unavailable"
                )
        if device.get("logical_device") != receiver_id:
            failures.append(f"receiver status list position {receiver_id} is mislabeled")
        if device.get("receiver_status_seen") is not True:
            failures.append(f"receiver {receiver_id} status is not fresh/readable")
        version = _integer(device.get("receiver_status_version"))
        if version is None or version < 6:
            failures.append(f"receiver {receiver_id} status v6 is required")
        capabilities = _integer(device.get("receiver_capabilities"))
        if (
            required_capabilities is not None
            and (capabilities is None or capabilities & required_capabilities != required_capabilities)
        ):
            failures.append(f"receiver {receiver_id} lacks required native capabilities")
        if device.get("receiver_logical_device") != receiver_id:
            failures.append(f"receiver {receiver_id} reports a different logical identity")
        for field, expected_value in unanimous_fields.items():
            if device.get(field) != expected_value:
                failures.append(f"receiver {receiver_id} {field} is not unanimous")
        if device.get("receiver_native_active_global_strips") != 33:
            failures.append(f"receiver {receiver_id} native global width is not 33")
        if device.get("receiver_native_active_leds_per_strip") != 138:
            failures.append(f"receiver {receiver_id} native height is not 138")
        if device.get("receiver_native_active_local_strips") != expected["local_strip_count"]:
            failures.append(f"receiver {receiver_id} native local width is wrong")
        if (
            device.get("receiver_native_active_global_strip_offset")
            != expected["global_strip_offset"]
        ):
            failures.append(f"receiver {receiver_id} native global offset is wrong")
        if device.get("receiver_native_executing") is not True:
            failures.append(f"receiver {receiver_id} is not executing the native module")
        if device.get("receiver_native_cache_integrity_ok") is not True:
            failures.append(f"receiver {receiver_id} cache integrity is not accepted")
        if device.get("receiver_native_result_name") != "ok":
            failures.append(f"receiver {receiver_id} native result is not ok")

    for field in (
        "receiver_vibe_revision", "receiver_vibe_digest",
        "receiver_plant_modifier_revision", "receiver_plant_modifier_digest",
    ):
        values = [device.get(field) for device in devices]
        if values[0] in (None, "") or any(value != values[0] for value in values[1:]):
            failures.append(f"{field} is not unanimous across all receivers")
    return failures


def _counter_delta(
    first: Mapping[str, Any], last: Mapping[str, Any], field: str
) -> int | None:
    before = _integer(first.get(field))
    after = _integer(last.get(field))
    return None if before is None or after is None else after - before


def _sampled_percentiles(values: Sequence[int]) -> dict[str, int] | None:
    """Summarize sampled last-value telemetry without claiming event histograms."""

    if not values:
        return None
    ordered = sorted(values)

    def at(ratio: float) -> int:
        index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * ratio) - 1))
        return ordered[index]

    return {
        "sample_count": len(ordered),
        "p50_us": at(0.50),
        "p95_us": at(0.95),
        "p99_us": at(0.99),
        "max_us": ordered[-1],
    }


def evaluate_series(
    samples: Sequence[Mapping[str, Any]], identity: Mapping[str, Any]
) -> dict[str, Any]:
    failures: list[str] = []
    if len(samples) < 2:
        return {
            "passed": False,
            "failures": ["acceptance requires at least two status samples"],
            "sample_count": len(samples),
        }
    for index, sample in enumerate(samples):
        failures.extend(
            f"sample {index}: {failure}"
            for failure in evaluate_sample(sample, identity)
        )
    release_ids = [sample.get("controller_release_id") for sample in samples]
    if any(release_id != release_ids[0] for release_id in release_ids[1:]):
        failures.append("controller release identity changed during the soak")

    first_devices = _sequence(samples[0].get("devices"))
    final_devices = _sequence(samples[-1].get("devices"))
    elapsed = float(samples[-1].get("elapsed_seconds", 0.0)) - float(
        samples[0].get("elapsed_seconds", 0.0)
    )
    device_deltas: dict[str, Any] = {}
    reset_events: list[dict[str, Any]] = []
    explicit_reset_deltas: dict[str, dict[str, int | None]] = {}
    for receiver_id in range(min(len(first_devices), len(final_devices), 5)):
        first = _mapping(first_devices[receiver_id])
        last = _mapping(final_devices[receiver_id])
        counters = {
            field: _counter_delta(first, last, field)
            for field in (*ERROR_COUNTERS, *CADENCE_COUNTERS,
                          "receiver_native_watchdog_events")
        }
        device_deltas[str(receiver_id)] = counters
        explicit_reset_deltas[str(receiver_id)] = {}
        for field in ("receiver_reset_count", "receiver_boot_count"):
            values = [
                _integer(_mapping(_sequence(sample.get("devices"))[receiver_id]).get(field))
                if receiver_id < len(_sequence(sample.get("devices"))) else None
                for sample in samples
            ]
            available = [value is not None for value in values]
            if any(available) and not all(available):
                failures.append(
                    f"receiver {receiver_id} {field} availability changed during the soak"
                )
                explicit_reset_deltas[str(receiver_id)][field] = None
            elif all(available):
                delta = values[-1] - values[0]  # type: ignore[operator]
                explicit_reset_deltas[str(receiver_id)][field] = delta
                if delta != 0:
                    failures.append(
                        f"receiver {receiver_id} {field} delta is {delta}; expected 0"
                    )
            else:
                explicit_reset_deltas[str(receiver_id)][field] = None
        for field in ERROR_COUNTERS:
            delta = counters[field]
            if delta is None:
                failures.append(f"receiver {receiver_id} {field} delta is unavailable")
            elif delta != 0:
                failures.append(f"receiver {receiver_id} {field} delta is {delta}; expected 0")
        watchdog_delta = counters["receiver_native_watchdog_events"]
        if watchdog_delta is None:
            failures.append(f"receiver {receiver_id} watchdog delta is unavailable")
        elif watchdog_delta != 0:
            failures.append(
                f"receiver {receiver_id} watchdog-event delta is {watchdog_delta}; expected 0"
            )
        missed_delta = counters["receiver_local_missed_deadlines"]
        if missed_delta is None:
            failures.append(f"receiver {receiver_id} cadence-miss delta is unavailable")
        elif missed_delta != 0:
            failures.append(
                f"receiver {receiver_id} cadence-miss delta is {missed_delta}; expected 0"
            )

        cadence = _integer(first.get("receiver_native_active_cadence_hz"))
        if cadence is None or cadence <= 0:
            failures.append(f"receiver {receiver_id} native cadence is unavailable")
        else:
            expected_frames = elapsed * cadence
            tolerance = max(2.0, expected_frames * 0.10)
            for field in (
                "receiver_local_frames_rendered",
                "receiver_local_cadence_deadlines",
            ):
                delta = counters[field]
                if delta is None:
                    failures.append(f"receiver {receiver_id} {field} delta is unavailable")
                elif abs(delta - expected_frames) > tolerance:
                    failures.append(
                        f"receiver {receiver_id} {field} delta {delta} is outside "
                        f"the declared {cadence} Hz cadence tolerance"
                    )

    for sample_index in range(1, len(samples)):
        prior_devices = _sequence(samples[sample_index - 1].get("devices"))
        current_devices = _sequence(samples[sample_index].get("devices"))
        for receiver_id in range(min(len(prior_devices), len(current_devices), 5)):
            prior = _mapping(prior_devices[receiver_id])
            current = _mapping(current_devices[receiver_id])
            for field in RESET_CONTINUITY_COUNTERS:
                delta = _counter_delta(prior, current, field)
                if delta is not None and delta < 0:
                    reset_events.append({
                        "sample_index": sample_index,
                        "logical_device": receiver_id,
                        "counter": field,
                        "delta": delta,
                    })
    if reset_events:
        failures.append("receiver counter regression indicates a reset or telemetry rollback")

    scene_times: list[dict[int, int]] = []
    skew_series: list[int] = []
    for sample in samples:
        values = {
            receiver_id: value
            for receiver_id, device in enumerate(_sequence(sample.get("devices")))
            if (value := _integer(_mapping(device).get("receiver_last_frame_scene_time_us")))
            is not None
        }
        if len(values) == 5:
            scene_times.append(values)
            skew_series.append(max(values.values()) - min(values.values()))
    cadence = _integer(_mapping(first_devices[0]).get(
        "receiver_native_active_cadence_hz"
    )) if first_devices else None
    period_us = (1_000_000.0 / cadence) if cadence else None
    start_skew_us = skew_series[0] if skew_series else None
    maximum_skew_us = max(skew_series) if skew_series else None
    drift_span_us = None
    if len(scene_times) >= 2:
        first_offsets = {
            key: value - scene_times[0][0] for key, value in scene_times[0].items()
        }
        last_offsets = {
            key: value - scene_times[-1][0] for key, value in scene_times[-1].items()
        }
        drift_values = [last_offsets[key] - first_offsets[key] for key in range(5)]
        drift_span_us = max(drift_values) - min(drift_values)
    if len(scene_times) != len(samples):
        failures.append("five-receiver scene-time evidence is incomplete")
    if period_us is not None:
        if maximum_skew_us is None or maximum_skew_us >= period_us:
            failures.append("first-to-last receiver skew reached one display period")
        if drift_span_us is None or drift_span_us >= period_us:
            failures.append("receiver relative drift reached one display period")

    explicit_reset_fields_available = {
        str(receiver_id): {
            field: explicit_reset_deltas.get(str(receiver_id), {}).get(field) is not None
            for field in ("receiver_reset_count", "receiver_boot_count")
        }
        for receiver_id in range(5)
    }
    timing_fields = {
        "render": "receiver_native_last_render_us",
        "composite": "receiver_overlay_last_composite_us",
        "encode": "receiver_last_encode_us",
        "display": "receiver_last_show_us",
    }
    sampled_timing: dict[str, Any] = {}
    for receiver_id in range(5):
        device_summary: dict[str, Any] = {}
        for label, field in timing_fields.items():
            values = [
                value
                for sample in samples
                if receiver_id < len(_sequence(sample.get("devices")))
                and (value := _integer(_mapping(
                    _sequence(sample.get("devices"))[receiver_id]
                ).get(field))) is not None
            ]
            device_summary[label] = _sampled_percentiles(values)
        sampled_timing[str(receiver_id)] = device_summary

    spi_series = []
    memory_cache_series = []
    for sample in samples:
        devices = _sequence(sample.get("devices"))
        spi_series.append({
            "elapsed_seconds": sample.get("elapsed_seconds"),
            "devices": [
                {
                    "logical_device": receiver_id,
                    **{
                        field: _mapping(device).get(field)
                        for field in SPI_SERIES_FIELDS
                    },
                }
                for receiver_id, device in enumerate(devices)
            ],
        })
        memory_cache_series.append({
            "elapsed_seconds": sample.get("elapsed_seconds"),
            "devices": [
                {
                    "logical_device": receiver_id,
                    **{
                        field: _mapping(device).get(field)
                        for field in (
                            *MEMORY_CACHE_SERIES_FIELDS,
                            "receiver_native_rollback_bundle_digest",
                            "receiver_native_rollback_payload_digest",
                            "receiver_native_quarantine_payload_digest",
                        )
                    },
                }
                for receiver_id, device in enumerate(devices)
            ],
        })
    series_availability = {
        "spi": {
            str(receiver_id): {
                field: all(
                    receiver_id < len(_sequence(sample.get("devices")))
                    and (
                        (value := _integer(_mapping(
                            _sequence(sample.get("devices"))[receiver_id]
                        ).get(field))) is not None
                        and value >= 0
                    )
                    for sample in samples
                )
                for field in SPI_SERIES_FIELDS
            }
            for receiver_id in range(5)
        },
        "memory_cache": {
            str(receiver_id): {
                field: all(
                    receiver_id < len(_sequence(sample.get("devices")))
                    and (
                        (value := _integer(_mapping(
                            _sequence(sample.get("devices"))[receiver_id]
                        ).get(field))) is not None
                        and value >= 0
                    )
                    for sample in samples
                )
                for field in MEMORY_CACHE_SERIES_FIELDS
            }
            for receiver_id in range(5)
        },
    }
    unavailable_series_fields = [
        f"{series_name}:receiver-{receiver_id}:{field}"
        for series_name, receiver_fields in series_availability.items()
        for receiver_id, fields in receiver_fields.items()
        for field, available in fields.items()
        if not available
    ]
    if unavailable_series_fields:
        failures.append(
            "required SPI/memory/cache series fields are unavailable: "
            + ", ".join(unavailable_series_fields)
        )
    return {
        "passed": not failures,
        "failures": failures,
        "sample_count": len(samples),
        "observed_seconds": elapsed,
        "status_field_contract": {
            "required_device_fields": list(REQUIRED_DEVICE_STATUS_FIELDS),
            "availability_by_receiver": {
                str(receiver_id): {
                    field: all(
                        receiver_id < len(_sequence(sample.get("devices")))
                        and _integer(_mapping(
                            _sequence(sample.get("devices"))[receiver_id]
                        ).get(field)) is not None
                        for sample in samples
                    )
                    for field in REQUIRED_DEVICE_STATUS_FIELDS
                }
                for receiver_id in range(5)
            },
        },
        "deltas_by_receiver": device_deltas,
        "reset_detection": {
            "explicit_reset_counter_available": explicit_reset_fields_available,
            "explicit_reset_counter_deltas": explicit_reset_deltas,
            "continuity_counters": list(RESET_CONTINUITY_COUNTERS),
            "reset_delta": len(reset_events),
            "events": reset_events,
        },
        "timing": {
            "declared_cadence_hz": cadence,
            "display_period_us": period_us,
            "start_skew_us": start_skew_us,
            "maximum_skew_us": maximum_skew_us,
            "drift_span_us": drift_span_us,
            "skew_series_us": skew_series,
        },
        "sampled_timing_percentiles": {
            "method": "periodic last-value status samples; not receiver event histograms",
            "receivers": sampled_timing,
        },
        "spi_series": spi_series,
        "memory_cache_series": memory_cache_series,
        "series_field_availability": series_availability,
        "series_fields_complete": not unavailable_series_fields,
    }


def _companion_subgates(
    reports: Sequence[Mapping[str, Any]],
    *,
    gate: str,
    identity: Mapping[str, Any],
    controller_release_id: Any,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    accepted: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    gate_family = "H4" if gate.startswith("H4") else gate
    for index, raw in enumerate(reports):
        report = _mapping(raw)
        schema = report.get("schema")
        if schema not in {SCHEMA, COMPANION_SCHEMA}:
            failures.append(f"companion {index} has an unsupported schema")
            continue
        if report.get("schema_version") != 1:
            failures.append(f"companion {index} has an unsupported schema version")
            continue
        report_gate = str(report.get("gate", ""))
        report_family = "H4" if report_gate.startswith("H4") else report_gate
        if report_family != gate_family:
            failures.append(f"companion {index} belongs to {report_gate or 'no gate'}")
            continue
        artifact = _mapping(report.get("artifact"))
        if (
            artifact.get("bundle_digest") != identity.get("bundle_digest")
            or artifact.get("payload_digest") != identity.get("payload_digest")
        ):
            failures.append(f"companion {index} has a different native binding")
            continue
        release_id = report.get("controller_release_id")
        if release_id is None:
            samples = _sequence(report.get("samples"))
            release_id = (
                _mapping(samples[0]).get("controller_release_id")
                if samples else None
            )
        if release_id != controller_release_id:
            failures.append(f"companion {index} has a different controller release")
            continue
        results = _mapping(report.get("subgate_results"))
        for subgate_id, raw_result in results.items():
            result = _mapping(raw_result)
            evidence = _sequence(result.get("evidence"))
            if result.get("passed") is not True or not evidence:
                continue
            if subgate_id in SOAK_SUBGATES:
                if schema == SCHEMA:
                    requested = _finite_number(report.get("requested_duration_seconds"))
                    observed = _finite_number(
                        _mapping(report.get("evaluation")).get("observed_seconds")
                    )
                    valid_soak = bool(
                        report.get("slice_passed") is True
                        and requested is not None
                        and requested >= MIN_COMPLETE_GATE_SOAK_SECONDS
                        and observed is not None
                        and observed >= MIN_COMPLETE_GATE_SOAK_SECONDS
                    )
                else:
                    observed = _finite_number(result.get("observed_seconds"))
                    valid_soak = bool(
                        observed is not None
                        and observed >= MIN_COMPLETE_GATE_SOAK_SECONDS
                    )
                if not valid_soak:
                    failures.append(
                        f"companion {index} subgate {subgate_id} has no valid "
                        "1800-second soak"
                    )
                    continue
            accepted[subgate_id] = {
                "source": f"companion:{index}",
                "evidence": list(evidence),
            }
    return accepted, failures


def gate_coverage(
    gate: str,
    *,
    slice_passed: bool,
    evaluation: Mapping[str, Any],
    restoration: Mapping[str, Any],
    identity: Mapping[str, Any],
    controller_release_id: Any,
    companion_reports: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Separate this runner's evidence slice from a complete phase-gate claim."""

    companion, companion_failures = _companion_subgates(
        companion_reports,
        gate=gate,
        identity=identity,
        controller_release_id=controller_release_id,
    )
    observed_seconds = _finite_number(evaluation.get("observed_seconds"))
    full_soak = bool(
        slice_passed
        and observed_seconds is not None
        and observed_seconds >= MIN_COMPLETE_GATE_SOAK_SECONDS
    )
    series_fields_complete = evaluation.get("series_fields_complete") is True
    runner_passes = {
        "h2.exact-five-state": slice_passed,
        "h2.native-skew-drift-soak": full_soak,
        "h4.default-native-clock-soak": full_soak and gate == "H4-default",
        "h4.maximum-native-clock-soak": full_soak and gate == "H4-maximum",
        "h4.streamed-spi-series": bool(
            slice_passed and series_fields_complete and evaluation.get("spi_series")
        ),
        "h4.memory-cache-series": bool(
            slice_passed
            and series_fields_complete
            and evaluation.get("memory_cache_series")
        ),
        "h4.python-fallback-restoration": restoration.get("passed") is True,
    }
    results: dict[str, Any] = {}
    for subgate_id, source_kind in GATE_SUBGATES[gate]:
        if source_kind == "runner":
            passed = runner_passes.get(subgate_id, False)
            source = "current-runner"
            evidence = [
                "evaluation"
                if subgate_id != "h4.python-fallback-restoration"
                else "restoration"
            ] if passed else []
        else:
            item = companion.get(subgate_id, {})
            passed = bool(item)
            source = item.get("source", "outstanding")
            evidence = list(item.get("evidence", ()))
        results[subgate_id] = {
            "passed": passed,
            "required_source": source_kind,
            "source": source,
            "evidence": evidence,
        }
    outstanding = [
        subgate_id for subgate_id, result in results.items()
        if result["passed"] is not True
    ]
    return {
        "claim": "supporting-evidence",
        "full_gate_passed": not outstanding and not companion_failures,
        "covered_subgates": [
            subgate_id for subgate_id, result in results.items()
            if result["passed"] is True
        ],
        "outstanding_subgates": outstanding,
        "companion_failures": companion_failures,
        "subgate_results": results,
    }


def _wait_for_status(
    api: Any,
    predicate: Callable[[Mapping[str, Any]], bool],
    *,
    timeout: float,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    deadline = monotonic() + timeout
    latest: dict[str, Any] = {}
    while True:
        latest = api.request("/api/status", timeout=min(timeout, 10.0))
        if predicate(latest):
            return latest
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise PhysicalAcceptanceError("target status did not converge before timeout")
        sleep(min(0.1, remaining))


def _command_processed(status: Mapping[str, Any], command_id: Any) -> bool:
    return command_id is not None and status.get("last_command_id") == command_id


def _native_active(
    status: Mapping[str, Any], command_id: Any, identity: Mapping[str, Any]
) -> bool:
    driver = _native_driver(status)
    sample = normalize_sample(status, elapsed_seconds=0.0, sampled_at=0.0)
    return bool(
        _command_processed(status, command_id)
        and driver.get("state") == "active"
        and driver.get("bundle_digest") == identity.get("bundle_digest")
        and driver.get("payload_digest") == identity.get("payload_digest")
        and driver.get("parameter_digest") == identity.get("parameter_digest")
        and driver.get("error") is None
        and not evaluate_sample(sample, identity)
    )


def _python_fallback_active(
    status: Mapping[str, Any], fallback_plugin: str, command_id: Any = None
) -> bool:
    if command_id is not None and not _command_processed(status, command_id):
        return False
    scene_state = _mapping(status.get("scene_state"))
    background = _mapping(scene_state.get("background"))
    provider_mode = _mapping(status.get("scene")).get("provider_mode")
    native = _mapping(
        _mapping(_mapping(status.get("driver_stats")).get("aggregate")).get(
            "native_background"
        )
    )
    return bool(
        background.get("provider") == "python"
        and background.get("plugin_id") == fallback_plugin
        and provider_mode == "python_host"
        and native.get("state") in {"host_full_scene", "idle", "stopped", "compensated"}
    )


def restore_python_fallback(
    api: Any,
    config: AcceptanceConfig,
    fallback_ref: Mapping[str, Any],
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Attempt exact native recovery, then a direct Python scene as a fallback."""

    result: dict[str, Any] = {
        "attempted": True,
        "passed": False,
        "method": None,
        "failures": [],
    }
    try:
        response = api.request(
            "/api/v1/receiver-native/recover",
            method="POST",
            timeout=config.timeout_seconds,
        )
        command_id = response.get("command_id")
        status = _wait_for_status(
            api,
            lambda item: _python_fallback_active(
                item, config.fallback_plugin, command_id
            ),
            timeout=config.timeout_seconds,
            monotonic=monotonic,
            sleep=sleep,
        )
        result.update({
            "passed": True,
            "method": "receiver-native-recover",
            "command_id": command_id,
            "final_status_updated_at": status.get("updated_at"),
        })
        return result
    except Exception as exc:
        result["failures"].append(f"receiver-native recovery: {exc}")

    fallback_scene = {
        "schema": "ledgrid.scene-state",
        "schema_version": 1,
        "revision": time.time_ns() & ((1 << 64) - 1),
        "background": dict(fallback_ref),
        "overlays": [],
        "known_python_fallback": dict(fallback_ref),
    }
    try:
        response = api.request(
            "/api/v1/scene",
            method="PUT",
            payload=fallback_scene,
            timeout=config.timeout_seconds,
        )
        command_id = response.get("command_id")
        status = _wait_for_status(
            api,
            lambda item: _python_fallback_active(
                item, config.fallback_plugin, command_id
            ),
            timeout=config.timeout_seconds,
            monotonic=monotonic,
            sleep=sleep,
        )
        result.update({
            "passed": True,
            "method": "direct-python-scene",
            "command_id": command_id,
            "final_status_updated_at": status.get("updated_at"),
        })
    except Exception as exc:
        result["failures"].append(f"direct Python fallback: {exc}")
    return result


def run_acceptance(
    config: AcceptanceConfig,
    api: Any,
    *,
    companion_reports: Sequence[Mapping[str, Any]] = (),
    monotonic: Callable[[], float] = time.monotonic,
    wall_time: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run the physical gate and always attempt complete Python restoration."""

    started_unix = wall_time()
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "gate": config.gate,
        "claim": (
            "full-gate" if config.require_complete_gate else "supporting-evidence"
        ),
        "workload": config.workload,
        "target": config.target.rsplit("@", 1)[-1],
        "started_at": _utc_timestamp(started_unix),
        "requested_duration_seconds": config.duration_seconds,
        "sample_interval_seconds": config.sample_interval_seconds,
        "expected_topology": list(EXPECTED_TOPOLOGY),
        "samples": [],
        "failures": [],
    }
    identity: dict[str, Any] = {}
    fallback_ref: dict[str, Any] = {
        "plugin_id": config.fallback_plugin,
        "provider": "python",
        "parameter_overrides": {},
        "resolved_parameters": {},
    }
    interrupted = False
    try:
        scene, identity = resolve_acceptance_scene(api, config)
        fallback_ref = dict(identity["fallback"])
        report["artifact"] = {
            key: identity[key]
            for key in (
                "plugin_id", "bundle_digest", "payload_digest",
                "declared_cadence_hz", "native_parameters", "clock_parameters",
            )
        }
        install_response = api.request(
            f"/api/v1/native-backgrounds/{identity['bundle_digest']}/install",
            method="POST",
            timeout=config.timeout_seconds,
        )
        install_command = install_response.get("command_id")
        _wait_for_status(
            api,
            lambda status: bool(
                _command_processed(status, install_command)
                and _native_driver(status).get("state") == "ready"
                and _native_driver(status).get("bundle_digest")
                    == identity["bundle_digest"]
                and _native_driver(status).get("error") is None
            ),
            timeout=config.timeout_seconds,
            monotonic=monotonic,
            sleep=sleep,
        )
        start_response = api.request(
            "/api/v1/scene",
            method="PUT",
            payload=scene,
            timeout=config.timeout_seconds,
        )
        start_command = start_response.get("command_id")
        active = _wait_for_status(
            api,
            lambda status: _native_active(status, start_command, identity),
            timeout=config.timeout_seconds,
            monotonic=monotonic,
            sleep=sleep,
        )
        report["activation"] = {
            "install_command_id": install_command,
            "start_command_id": start_command,
        }
        soak_started = monotonic()
        first_sample = normalize_sample(
            active, elapsed_seconds=0.0, sampled_at=wall_time()
        )
        report["samples"].append(first_sample)
        initial_failures = evaluate_sample(first_sample, identity)
        if initial_failures:
            raise PhysicalAcceptanceError("; ".join(initial_failures))

        deadline = soak_started + config.duration_seconds
        while True:
            remaining = deadline - monotonic()
            # Float subtraction can leave a sub-nanosecond remainder after an
            # exact scheduled sample. Do not manufacture a duplicate terminal
            # sample or distort cadence evidence for that representation noise.
            if remaining <= 1e-6:
                break
            sleep(min(config.sample_interval_seconds, remaining))
            raw = api.request("/api/status", timeout=min(config.timeout_seconds, 10.0))
            sample = normalize_sample(
                raw,
                elapsed_seconds=max(0.0, monotonic() - soak_started),
                sampled_at=wall_time(),
            )
            report["samples"].append(sample)
            sample_failures = evaluate_sample(sample, identity)
            if sample_failures:
                raise PhysicalAcceptanceError("; ".join(sample_failures))
            if len(report["samples"]) >= 2:
                previous = report["samples"][-2]
                for receiver_id, (before, after) in enumerate(zip(
                    _sequence(previous.get("devices")),
                    _sequence(sample.get("devices")),
                )):
                    for field in (*ERROR_COUNTERS, "receiver_native_watchdog_events",
                                  "receiver_local_missed_deadlines"):
                        delta = _counter_delta(_mapping(before), _mapping(after), field)
                        if delta is not None and delta != 0:
                            raise PhysicalAcceptanceError(
                                f"receiver {receiver_id} {field} changed by {delta}"
                            )
                    for field in RESET_CONTINUITY_COUNTERS:
                        delta = _counter_delta(_mapping(before), _mapping(after), field)
                        if delta is not None and delta < 0:
                            raise PhysicalAcceptanceError(
                                f"receiver {receiver_id} {field} regressed by {delta}"
                            )
        report["evaluation"] = evaluate_series(report["samples"], identity)
    except KeyboardInterrupt:
        interrupted = True
        report["failures"].append("acceptance interrupted by operator")
    except Exception as exc:
        report["failures"].append(str(exc))
        if identity and len(report["samples"]) >= 2:
            report["evaluation"] = evaluate_series(report["samples"], identity)
    finally:
        report["restoration"] = restore_python_fallback(
            api,
            config,
            fallback_ref,
            monotonic=monotonic,
            sleep=sleep,
        )
        completed_unix = wall_time()
        report["completed_at"] = _utc_timestamp(completed_unix)
        report["interrupted"] = interrupted
        evaluation = _mapping(report.get("evaluation"))
        restoration = _mapping(report.get("restoration"))
        slice_passed = bool(
            not report["failures"]
            and evaluation.get("passed") is True
            and restoration.get("passed") is True
        )
        controller_release_id = (
            _mapping(report["samples"][0]).get("controller_release_id")
            if report["samples"] else None
        )
        report["controller_release_id"] = controller_release_id
        coverage = gate_coverage(
            config.gate,
            slice_passed=slice_passed,
            evaluation=evaluation,
            restoration=restoration,
            identity=identity,
            controller_release_id=controller_release_id,
            companion_reports=companion_reports,
        )
        report["slice_passed"] = slice_passed
        report["gate_coverage"] = coverage
        report["subgate_results"] = coverage["subgate_results"]
        report["full_gate_passed"] = coverage["full_gate_passed"]
        if config.require_complete_gate and not coverage["full_gate_passed"]:
            report["claim_failures"] = [
                "complete gate evidence is unavailable for: "
                + ", ".join(coverage["outstanding_subgates"])
            ] + list(coverage["companion_failures"])
        report["passed"] = bool(
            slice_passed
            and (
                not config.require_complete_gate
                or coverage["full_gate_passed"] is True
            )
        )
    return report


def write_report(report: Mapping[str, Any], path: Path) -> Path:
    path = path.expanduser().resolve(strict=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)
    return path


def _default_output(evidence_dir: Path, report: Mapping[str, Any]) -> Path:
    timestamp = str(report["started_at"]).replace(":", "").replace("+00:00", "Z")
    timestamp = timestamp.replace("-", "").replace(".", "")
    gate = str(report["gate"]).lower().replace("-", "_")
    return evidence_dir / f"{timestamp}-{gate}-receiver-native.json"


def load_companion_reports(paths: Sequence[Path]) -> list[dict[str, Any]]:
    reports = []
    for path in paths:
        lexical = Path(os.path.abspath(os.fspath(path.expanduser())))
        current = Path(lexical.anchor)
        try:
            for part in lexical.parts[1:]:
                current /= part
                metadata = current.lstat()
                if stat.S_ISLNK(metadata.st_mode):
                    raise PhysicalAcceptanceError(
                        f"companion evidence path contains a symbolic link: {path}"
                    )
        except OSError as exc:
            raise PhysicalAcceptanceError(
                f"companion evidence is unavailable: {path}"
            ) from exc
        if not stat.S_ISREG(lexical.lstat().st_mode):
            raise PhysicalAcceptanceError(
                f"companion evidence is not a regular file: {path}"
            )
        candidate = lexical.resolve(strict=True)
        try:
            value = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PhysicalAcceptanceError(
                f"companion evidence is not valid JSON: {path}"
            ) from exc
        if not isinstance(value, dict):
            raise PhysicalAcceptanceError(
                f"companion evidence must contain one JSON object: {path}"
            )
        reports.append(value)
    return reports


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selector", nargs="?", default=DEFAULT_PLUGIN)
    parser.add_argument(
        "--gate", choices=("H2", "H4-default", "H4-maximum"), default="H2"
    )
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_SOAK_SECONDS,
        help="soak seconds; release evidence defaults to a real 30 minutes (1800)",
    )
    parser.add_argument("--sample-interval", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--fallback", default=DEFAULT_FALLBACK)
    parser.add_argument("--clock-overlay", default=DEFAULT_CLOCK_OVERLAY)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--companion-evidence", action="append", default=[], type=Path,
        help="machine-readable evidence for subgates this safe runner cannot exercise",
    )
    parser.add_argument(
        "--require-complete-gate", action="store_true",
        help="fail unless current plus companion evidence covers every gate subcase",
    )
    parser.add_argument(
        "--evidence-dir", type=Path,
        default=Path("run_state/physical-acceptance"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = AcceptanceConfig(
            target=args.target,
            selector=args.selector,
            gate=args.gate,
            duration_seconds=args.duration,
            sample_interval_seconds=args.sample_interval,
            timeout_seconds=args.timeout,
            fallback_plugin=args.fallback,
            clock_overlay_plugin=args.clock_overlay,
            require_complete_gate=args.require_complete_gate,
        )
        companions = load_companion_reports(args.companion_evidence)
        report = run_acceptance(
            config, HTTPAPI(config.target), companion_reports=companions
        )
        output = args.output or _default_output(args.evidence_dir, report)
        resolved_output = output.expanduser().resolve(strict=False)
        report["evidence_path"] = os.fspath(resolved_output)
        write_report(report, resolved_output)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["passed"] else 1
    except (PhysicalAcceptanceError, ValueError) as exc:
        print(json.dumps({
            "schema": SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "passed": False,
            "failures": [str(exc)],
        }, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
