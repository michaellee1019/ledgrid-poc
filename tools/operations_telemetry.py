"""Adapters for the versioned non-browser Composer operations contract."""

from __future__ import annotations

from typing import Any, Mapping


SCHEMA = "ledgrid.composer-operations-telemetry"
SCHEMA_VERSION = 1


def _section(payload: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = payload.get(name)
    if not isinstance(value, Mapping):
        raise ValueError(f"operations telemetry {name!r} section is unavailable")
    return value


def status_from_telemetry(payload: Any) -> dict[str, Any]:
    """Return the bounded legacy-shaped evidence needed by retained tools."""

    if not isinstance(payload, Mapping):
        raise ValueError("operations telemetry is unavailable or malformed")
    if payload.get("schema") != SCHEMA or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("operations telemetry schema is unsupported")
    controller = _section(payload, "controller")
    diagnostics = _section(payload, "diagnostics")
    calibration = _section(payload, "calibration")
    qualification = _section(payload, "qualification")
    receiver_native = payload.get("receiver_native")
    if receiver_native is not None and not isinstance(receiver_native, Mapping):
        raise ValueError("operations telemetry receiver_native section is malformed")
    return {
        **dict(controller),
        **dict(qualification),
        "performance": diagnostics.get("performance"),
        "driver_stats": diagnostics.get("driver_stats"),
        "installation_profile_digest": calibration.get("installation_profile_digest"),
        "plant_modifiers": calibration.get("plant_modifiers"),
        "receiver_hybrid": dict(receiver_native) if receiver_native else None,
    }


def metrics_from_telemetry(payload: Any) -> dict[str, Any]:
    """Project telemetry into the bounded metrics shape retained by benchmarks."""

    status = status_from_telemetry(payload)
    return {
        "animation": {
            "target_fps": status.get("target_fps", 0),
            "actual_fps": status.get("actual_fps", 0),
            "uptime": status.get("uptime", 0),
        },
        "performance": status.get("performance") or {},
        "driver": status.get("driver_stats") or {},
        "system": {},
    }
