"""Portable, hardware-free activation qualification contracts.

This module validates retained evidence and evaluates it against one explicit,
versioned installation budget.  It deliberately performs no capture, IPC, or
hardware operation.  Browser electrical estimates remain useful diagnostics,
but only a calibrated controller/receiver measurement can satisfy POWER-01.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


QUALIFICATION_RECORD_SCHEMA = "ledgrid.activation-qualification-record"
QUALIFICATION_RECORD_VERSION = 2
QUALIFICATION_RESULT_SCHEMA = "ledgrid.activation-qualification-result"
QUALIFICATION_RESULT_VERSION = 1
INSTALLATION_BUDGET_SCHEMA = "ledgrid.installation-qualification-budget"
INSTALLATION_BUDGET_VERSION = 1
TARGET_EVIDENCE_SCHEMA = "ledgrid.target-qualification-evidence"
TARGET_EVIDENCE_VERSION = 3
EVIDENCE_SOURCES = ("browser", "controller_pi", "receiver")

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INSTALLATION_BUDGET_PATH = (
    _REPOSITORY_ROOT / "config/installation_qualification_budget.json"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONTROLLER_SESSION_ID = re.compile(r"^[0-9a-f]{32}$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$")


class QualificationValidationError(ValueError):
    """A qualification record or budget violates the versioned contract."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise QualificationValidationError(f"{label} must be a JSON object")
    return dict(value)


def _only(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise QualificationValidationError(
            f"unsupported {label} fields: {', '.join(unknown)}"
        )


def _integer(
    value: Any,
    label: str,
    *,
    minimum: int = 0,
    maximum: int = 2**64 - 1,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise QualificationValidationError(
            f"{label} must be an integer from {minimum} through {maximum}"
        )
    return value


def _finite(
    value: Any,
    label: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QualificationValidationError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise QualificationValidationError(f"{label} must be finite")
    if minimum is not None and result < minimum:
        raise QualificationValidationError(f"{label} must be at least {minimum}")
    if maximum is not None and result > maximum:
        raise QualificationValidationError(f"{label} must be at most {maximum}")
    return result


def _optional_finite(
    value: Any,
    label: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    if value is None:
        return None
    return _finite(value, label, minimum=minimum, maximum=maximum)


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise QualificationValidationError(
            f"{label} must be a lowercase SHA-256 digest"
        )
    return value


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise QualificationValidationError(f"{label} must be a stable identifier")
    return value


def _text(value: Any, label: str, *, maximum_bytes: int = 1024) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QualificationValidationError(f"{label} must be a non-empty string")
    if len(value.encode("utf-8")) > maximum_bytes:
        raise QualificationValidationError(
            f"{label} exceeds the {maximum_bytes}-byte limit"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise QualificationValidationError(f"{label} contains control characters")
    return value


def canonical_json_bytes(value: Any) -> bytes:
    """Return strict deterministic JSON bytes for qualification identities."""

    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, Mapping):
            for key, item in current.items():
                if not isinstance(key, str):
                    raise QualificationValidationError(
                        "canonical JSON object keys must be strings"
                    )
                stack.append(item)
        elif isinstance(current, (list, tuple)):
            stack.extend(current)
        elif isinstance(current, float):
            if not math.isfinite(current):
                raise QualificationValidationError(
                    "canonical JSON numbers must be finite"
                )
        elif current is not None and not isinstance(current, (str, int, bool)):
            raise QualificationValidationError(
                f"canonical JSON does not support {type(current).__name__}"
            )
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise QualificationValidationError("value is not canonical JSON") from exc


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _unique_json_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise QualificationValidationError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise QualificationValidationError(f"non-finite JSON number is not allowed: {value}")


def _identity(value: Any, label: str) -> dict[str, Any]:
    payload = _object(value, label)
    _only(payload, {"revision", "digest"}, label)
    return {
        "revision": _integer(payload.get("revision"), f"{label}.revision"),
        "digest": _digest(payload.get("digest"), f"{label}.digest"),
    }


def _geometry(value: Any, label: str) -> dict[str, int]:
    payload = _object(value, label)
    _only(payload, {"strip_count", "leds_per_strip"}, label)
    return {
        "strip_count": _integer(
            payload.get("strip_count"), f"{label}.strip_count", minimum=1, maximum=65535
        ),
        "leds_per_strip": _integer(
            payload.get("leds_per_strip"),
            f"{label}.leds_per_strip",
            minimum=1,
            maximum=65535,
        ),
    }


def _metric_stats(value: Any, label: str) -> dict[str, float]:
    payload = _object(value, label)
    _only(payload, {"mean", "p95", "p99", "max"}, label)
    result = {
        name: _finite(payload.get(name), f"{label}.{name}", minimum=0.0)
        for name in ("mean", "p95", "p99", "max")
    }
    if not result["mean"] <= result["p95"] <= result["p99"] <= result["max"]:
        raise QualificationValidationError(
            f"{label} must satisfy mean <= p95 <= p99 <= max"
        )
    return result


def normalize_installation_qualification_budget(value: Any) -> dict[str, Any]:
    """Normalize one versioned installed-wall budget.

    Null budget values are intentional: they represent facts that have not yet
    been calibrated and cause evaluation to fail closed.  This avoids turning a
    software default into an assertion about the physical installation.
    """

    payload = _object(value, "installation qualification budget")
    _only(
        payload,
        {
            "schema",
            "schema_version",
            "revision",
            "installation_id",
            "geometry",
            "calibration",
            "maximum_evidence_age_ms",
            "budgets",
        },
        "installation qualification budget",
    )
    if payload.get("schema") != INSTALLATION_BUDGET_SCHEMA:
        raise QualificationValidationError(
            f"installation qualification budget.schema must be {INSTALLATION_BUDGET_SCHEMA!r}"
        )
    if payload.get("schema_version") != INSTALLATION_BUDGET_VERSION:
        raise QualificationValidationError(
            "installation qualification budget.schema_version must be "
            f"{INSTALLATION_BUDGET_VERSION}"
        )

    calibration = _object(
        payload.get("calibration"), "installation qualification budget.calibration"
    )
    _only(
        calibration,
        {"status", "captured_at", "environment"},
        "installation qualification budget.calibration",
    )
    status = calibration.get("status")
    if status not in {"unqualified", "calibrated"}:
        raise QualificationValidationError(
            "installation qualification budget.calibration.status must be "
            "'unqualified' or 'calibrated'"
        )
    captured_at = calibration.get("captured_at")
    environment = calibration.get("environment")
    if status == "calibrated":
        captured_at = _integer(
            captured_at,
            "installation qualification budget.calibration.captured_at",
            minimum=1,
        )
        environment = _text(
            environment, "installation qualification budget.calibration.environment"
        )
    elif captured_at is not None or environment is not None:
        raise QualificationValidationError(
            "an unqualified installation budget must not claim calibration evidence"
        )

    maximum_age = payload.get("maximum_evidence_age_ms")
    if maximum_age is not None:
        maximum_age = _integer(
            maximum_age,
            "installation qualification budget.maximum_evidence_age_ms",
            minimum=1,
        )

    budgets = _object(
        payload.get("budgets"), "installation qualification budget.budgets"
    )
    _only(
        budgets,
        {"voltage", "current", "brightness", "safety"},
        "installation qualification budget.budgets",
    )
    voltage = _object(
        budgets.get("voltage"),
        "installation qualification budget.budgets.voltage",
    )
    _only(
        voltage,
        {"minimum_mean_v", "maximum_p99_v"},
        "installation qualification budget.budgets.voltage",
    )
    current = _object(
        budgets.get("current"),
        "installation qualification budget.budgets.current",
    )
    _only(
        current,
        {"maximum_a"},
        "installation qualification budget.budgets.current",
    )
    brightness = _object(
        budgets.get("brightness"),
        "installation qualification budget.budgets.brightness",
    )
    _only(
        brightness,
        {"maximum_controller_value"},
        "installation qualification budget.budgets.brightness",
    )
    safety = _object(
        budgets.get("safety"),
        "installation qualification budget.budgets.safety",
    )
    _only(
        safety,
        {"required_current_headroom_ratio", "maximum_p99_power_w"},
        "installation qualification budget.budgets.safety",
    )

    normalized_budgets = {
        "voltage": {
            "minimum_mean_v": _optional_finite(
                voltage.get("minimum_mean_v"),
                "installation qualification budget.budgets.voltage.minimum_mean_v",
                minimum=0.0,
            ),
            "maximum_p99_v": _optional_finite(
                voltage.get("maximum_p99_v"),
                "installation qualification budget.budgets.voltage.maximum_p99_v",
                minimum=0.0,
            ),
        },
        "current": {
            "maximum_a": _optional_finite(
                current.get("maximum_a"),
                "installation qualification budget.budgets.current.maximum_a",
                minimum=0.0,
            )
        },
        "brightness": {
            "maximum_controller_value": (
                None
                if brightness.get("maximum_controller_value") is None
                else _integer(
                    brightness.get("maximum_controller_value"),
                    "installation qualification budget.budgets.brightness."
                    "maximum_controller_value",
                    maximum=255,
                )
            )
        },
        "safety": {
            "required_current_headroom_ratio": _optional_finite(
                safety.get("required_current_headroom_ratio"),
                "installation qualification budget.budgets.safety.required_current_headroom_ratio",
                minimum=0.0,
                maximum=0.999999999,
            ),
            "maximum_p99_power_w": _optional_finite(
                safety.get("maximum_p99_power_w"),
                "installation qualification budget.budgets.safety.maximum_p99_power_w",
                minimum=0.0,
            ),
        },
    }
    flat_values = (
        normalized_budgets["voltage"]["minimum_mean_v"],
        normalized_budgets["voltage"]["maximum_p99_v"],
        normalized_budgets["current"]["maximum_a"],
        normalized_budgets["brightness"]["maximum_controller_value"],
        normalized_budgets["safety"]["required_current_headroom_ratio"],
        normalized_budgets["safety"]["maximum_p99_power_w"],
    )
    if status == "calibrated" and (
        maximum_age is None or any(item is None for item in flat_values)
    ):
        raise QualificationValidationError(
            "a calibrated installation budget requires an evidence age and "
            "every voltage/current/brightness/safety limit"
        )
    if status == "unqualified" and any(item is not None for item in flat_values):
        raise QualificationValidationError(
            "an unqualified installation budget must keep every physical limit null"
        )
    if (
        normalized_budgets["voltage"]["minimum_mean_v"] is not None
        and normalized_budgets["voltage"]["maximum_p99_v"] is not None
        and normalized_budgets["voltage"]["minimum_mean_v"]
        > normalized_budgets["voltage"]["maximum_p99_v"]
    ):
        raise QualificationValidationError(
            "minimum_mean_v must not exceed maximum_p99_v"
        )

    return {
        "schema": INSTALLATION_BUDGET_SCHEMA,
        "schema_version": INSTALLATION_BUDGET_VERSION,
        "revision": _integer(
            payload.get("revision"),
            "installation qualification budget.revision",
            minimum=1,
        ),
        "installation_id": _identifier(
            payload.get("installation_id"),
            "installation qualification budget.installation_id",
        ),
        "geometry": _geometry(
            payload.get("geometry"), "installation qualification budget.geometry"
        ),
        "calibration": {
            "status": status,
            "captured_at": captured_at,
            "environment": environment,
        },
        "maximum_evidence_age_ms": maximum_age,
        "budgets": normalized_budgets,
    }


def installation_qualification_budget_digest(value: Any) -> str:
    return canonical_json_sha256(normalize_installation_qualification_budget(value))


def load_installation_qualification_budget(
    path: str | Path = DEFAULT_INSTALLATION_BUDGET_PATH,
) -> dict[str, Any]:
    """Load and normalize a budget without contacting the installation."""

    try:
        raw = Path(path).read_text(encoding="utf-8")
        value = json.loads(
            raw,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except QualificationValidationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QualificationValidationError(
            f"could not load installation qualification budget: {exc}"
        ) from exc
    return normalize_installation_qualification_budget(value)


def _normalize_binding(value: Any) -> dict[str, Any]:
    payload = _object(value, "qualification binding")
    _only(
        payload,
        {
            "browser_scene",
            "installation_profile_digest",
            "global_settings",
            "geometry",
            "brightness",
            "vibe",
            "plant_modifiers",
            "target_fps",
        },
        "qualification binding",
    )
    vibe = _object(payload.get("vibe"), "qualification binding.vibe")
    _only(
        vibe,
        {"vibe_id", "profile_version", "resolved_profile_digest"},
        "qualification binding.vibe",
    )
    modifiers = _object(
        payload.get("plant_modifiers"), "qualification binding.plant_modifiers"
    )
    # Ensure modifiers are finite JSON and detach them from caller mutation.
    modifiers = json.loads(canonical_json_bytes(modifiers).decode("utf-8"))
    return {
        "browser_scene": _identity(
            payload.get("browser_scene"), "qualification binding.browser_scene"
        ),
        "installation_profile_digest": _digest(
            payload.get("installation_profile_digest"),
            "qualification binding.installation_profile_digest",
        ),
        "global_settings": _identity(
            payload.get("global_settings"), "qualification binding.global_settings"
        ),
        "geometry": _geometry(payload.get("geometry"), "qualification binding.geometry"),
        "brightness": _integer(
            payload.get("brightness"),
            "qualification binding.brightness",
            maximum=255,
        ),
        "vibe": {
            "vibe_id": _identifier(
                vibe.get("vibe_id"), "qualification binding.vibe.vibe_id"
            ),
            "profile_version": _integer(
                vibe.get("profile_version"),
                "qualification binding.vibe.profile_version",
                minimum=1,
                maximum=2**31 - 1,
            ),
            "resolved_profile_digest": _digest(
                vibe.get("resolved_profile_digest"),
                "qualification binding.vibe.resolved_profile_digest",
            ),
        },
        "plant_modifiers": modifiers,
        "target_fps": _integer(
            payload.get("target_fps"),
            "qualification binding.target_fps",
            minimum=1,
            maximum=200,
        ),
    }


def activation_qualification_binding_digest(value: Any) -> str:
    """Return the exact scene/profile/globals/render-context identity."""

    return canonical_json_sha256(_normalize_binding(value))


def _electrical(value: Any, source: str, label: str) -> dict[str, Any]:
    payload = _object(value, label)
    _only(
        payload,
        {"kind", "budget_digest", "brightness", "voltage_v", "current_a"},
        label,
    )
    kind = payload.get("kind")
    if kind not in {"uncalibrated_estimate", "calibrated_measurement"}:
        raise QualificationValidationError(
            f"{label}.kind must be 'uncalibrated_estimate' or 'calibrated_measurement'"
        )
    budget_digest = payload.get("budget_digest")
    if kind == "uncalibrated_estimate":
        if source != "browser":
            raise QualificationValidationError(
                "uncalibrated electrical estimates must be labeled browser evidence"
            )
        if budget_digest is not None:
            raise QualificationValidationError(
                "an uncalibrated electrical estimate must not claim a budget digest"
            )
    else:
        if source == "browser":
            raise QualificationValidationError(
                "browser evidence cannot claim a calibrated electrical measurement"
            )
        budget_digest = _digest(budget_digest, f"{label}.budget_digest")
    return {
        "kind": kind,
        "budget_digest": budget_digest,
        "brightness": _integer(
            payload.get("brightness"), f"{label}.brightness", maximum=255
        ),
        "voltage_v": _metric_stats(payload.get("voltage_v"), f"{label}.voltage_v"),
        "current_a": _metric_stats(payload.get("current_a"), f"{label}.current_a"),
    }


def _evidence(value: Any, index: int) -> dict[str, Any]:
    label = f"qualification evidence[{index}]"
    payload = _object(value, label)
    _only(
        payload,
        {
            "source",
            "binding_digest",
            "transport_digest",
            "captured_at",
            "environment",
            "sample_count",
            "frame_time_ms",
            "cadence",
            "electrical",
        },
        label,
    )
    source = payload.get("source")
    if source not in EVIDENCE_SOURCES:
        raise QualificationValidationError(
            f"{label}.source must be browser, controller_pi, or receiver"
        )
    cadence = _object(payload.get("cadence"), f"{label}.cadence")
    _only(
        cadence,
        {"observed_fps", "missed_frame_ratio", "changed_frame_ratio"},
        f"{label}.cadence",
    )
    changed = cadence.get("changed_frame_ratio")
    electrical = payload.get("electrical")
    has_transport_digest = "transport_digest" in payload
    transport_digest = payload.get("transport_digest")
    if has_transport_digest and source != "receiver":
        raise QualificationValidationError(
            f"{label}.transport_digest is only valid for receiver evidence"
        )
    if source == "receiver" and not has_transport_digest:
        raise QualificationValidationError(
            f"{label}.transport_digest is required for receiver evidence"
        )
    result = {
        "source": source,
        "binding_digest": _digest(
            payload.get("binding_digest"), f"{label}.binding_digest"
        ),
        "captured_at": _integer(
            payload.get("captured_at"), f"{label}.captured_at", minimum=1
        ),
        "environment": _text(payload.get("environment"), f"{label}.environment"),
        "sample_count": _integer(
            payload.get("sample_count"), f"{label}.sample_count", minimum=1
        ),
        "frame_time_ms": _metric_stats(
            payload.get("frame_time_ms"), f"{label}.frame_time_ms"
        ),
        "cadence": {
            "observed_fps": _finite(
                cadence.get("observed_fps"),
                f"{label}.cadence.observed_fps",
                minimum=0.0,
            ),
            "missed_frame_ratio": _finite(
                cadence.get("missed_frame_ratio"),
                f"{label}.cadence.missed_frame_ratio",
                minimum=0.0,
                maximum=1.0,
            ),
            "changed_frame_ratio": (
                None
                if changed is None
                else _finite(
                    changed,
                    f"{label}.cadence.changed_frame_ratio",
                    minimum=0.0,
                    maximum=1.0,
                )
            ),
        },
        "electrical": (
            None
            if electrical is None
            else _electrical(electrical, source, f"{label}.electrical")
        ),
    }
    if source == "receiver":
        result["transport_digest"] = _digest(
            transport_digest, f"{label}.transport_digest"
        )
    return result


def normalize_activation_qualification_record(value: Any) -> dict[str, Any]:
    """Normalize retained evidence for one exact proposed activation."""

    payload = _object(value, "activation qualification record")
    _only(
        payload,
        {
            "schema",
            "schema_version",
            "revision",
            "qualification_version",
            "binding",
            "budget",
            "evidence",
        },
        "activation qualification record",
    )
    if payload.get("schema") != QUALIFICATION_RECORD_SCHEMA:
        raise QualificationValidationError(
            f"activation qualification record.schema must be {QUALIFICATION_RECORD_SCHEMA!r}"
        )
    if payload.get("schema_version") != QUALIFICATION_RECORD_VERSION:
        raise QualificationValidationError(
            "activation qualification record.schema_version must be "
            f"{QUALIFICATION_RECORD_VERSION}"
        )
    budget = _identity(payload.get("budget"), "activation qualification record.budget")
    raw_evidence = payload.get("evidence")
    if not isinstance(raw_evidence, list):
        raise QualificationValidationError(
            "activation qualification record.evidence must be an array"
        )
    evidence = [_evidence(item, index) for index, item in enumerate(raw_evidence)]
    sources = [item["source"] for item in evidence]
    if len(sources) != len(set(sources)):
        raise QualificationValidationError(
            "activation qualification record.evidence contains duplicate sources"
        )
    source_order = {source: index for index, source in enumerate(EVIDENCE_SOURCES)}
    evidence.sort(key=lambda item: source_order[item["source"]])
    return {
        "schema": QUALIFICATION_RECORD_SCHEMA,
        "schema_version": QUALIFICATION_RECORD_VERSION,
        "revision": _integer(
            payload.get("revision"), "activation qualification record.revision"
        ),
        "qualification_version": _identifier(
            payload.get("qualification_version"),
            "activation qualification record.qualification_version",
        ),
        "binding": _normalize_binding(payload.get("binding")),
        "budget": budget,
        "evidence": evidence,
    }


def activation_qualification_record_digest(value: Any) -> str:
    """Return a deterministic digest suitable for an activation basis."""

    return canonical_json_sha256(normalize_activation_qualification_record(value))


_TARGET_TRANSPORT_DELTA_FIELDS = (
    "full_frame_transfers",
    "full_frame_status_transfers",
    "full_frame_status_samples",
    "full_frame_status_sample_misses",
    "full_frame_write_only_transfers",
)
_TARGET_TRANSPORT_FINAL_FIELDS = (
    "receiver_status_version",
    "receiver_status_max_version_seen",
    "full_frame_frames_since_status_sample",
    "full_frame_max_status_sample_gap",
    "spidev_buffer_size",
    "full_frame_write_only_supported",
)
_TARGET_TRANSPORT_EXPECTED_WIRE_BYTES = (3320, 3320, 3320, 3960, 424)
_TARGET_TRANSPORT_MAX_SAMPLE_GAP = 256
_TARGET_FEC_DELTA_FIELDS = (
    "fec_frames_sent",
    "fec_codewords_sent",
    "fec_parity_bytes_sent",
    "fec_data_padding_bytes_sent",
    "receiver_fec_packets_received",
    "receiver_fec_packets_accepted",
    "receiver_fec_corrected_packets",
    "receiver_fec_corrected_codewords",
    "receiver_fec_uncorrectable_packets",
    "receiver_fec_semantic_crc_errors",
    "receiver_fec_framing_errors",
)


def _target_fec_item(
    value: Any,
    label: str,
    *,
    expected_count: int,
    full_frames: int | None,
) -> dict[str, Any]:
    payload = _object(value, label)
    _only(payload, {"requested_count", "enabled_count", "deltas", "final"}, label)
    requested = _integer(payload.get("requested_count"), f"{label}.requested_count", maximum=1)
    enabled = _integer(payload.get("enabled_count"), f"{label}.enabled_count", maximum=1)
    if requested != expected_count or enabled != expected_count:
        raise QualificationValidationError(
            f"{label} must select exactly the configured receiver-3 FEC policy"
        )
    raw_deltas = _object(payload.get("deltas"), f"{label}.deltas")
    _only(raw_deltas, set(_TARGET_FEC_DELTA_FIELDS), f"{label}.deltas")
    deltas = {
        field: _integer(raw_deltas.get(field), f"{label}.deltas.{field}")
        for field in _TARGET_FEC_DELTA_FIELDS
    }
    if expected_count == 0:
        if any(deltas.values()):
            raise QualificationValidationError(f"{label} contains unconfigured FEC traffic")
    else:
        expected_fec_frames = (
            full_frames if full_frames is not None else deltas["fec_frames_sent"]
        )
        expected_host = {
            "fec_frames_sent": expected_fec_frames,
            "fec_codewords_sent": 208 * expected_fec_frames,
            "fec_parity_bytes_sent": 624 * expected_fec_frames,
            "fec_data_padding_bytes_sent": 4 * expected_fec_frames,
        }
        for field, expected in expected_host.items():
            if deltas[field] != expected:
                raise QualificationValidationError(
                    f"{label}.deltas.{field} must be {expected}"
                )
        received = deltas["receiver_fec_packets_received"]
        accepted = deltas["receiver_fec_packets_accepted"]
        uncorrectable = deltas["receiver_fec_uncorrectable_packets"]
        semantic_crc = deltas["receiver_fec_semantic_crc_errors"]
        framing = deltas["receiver_fec_framing_errors"]
        if received != accepted + uncorrectable + semantic_crc + framing:
            raise QualificationValidationError(
                f"{label}.deltas receiver FEC outcomes do not partition received packets"
            )
        if (
            received != expected_fec_frames
            or accepted != expected_fec_frames
            or uncorrectable
            or semantic_crc
            or framing
        ):
            raise QualificationValidationError(
                f"{label}.deltas requires one accepted FEC packet per sent frame "
                "with zero terminal faults"
            )
        corrected_packets = deltas["receiver_fec_corrected_packets"]
        corrected_codewords = deltas["receiver_fec_corrected_codewords"]
        if (
            corrected_packets > accepted
            or corrected_codewords < corrected_packets
            or corrected_codewords > 208 * corrected_packets
        ):
            raise QualificationValidationError(
                f"{label}.deltas corrected FEC accounting is inconsistent"
            )
    raw_final = _object(payload.get("final"), f"{label}.final")
    _only(
        raw_final,
        {"receiver_fec_last_decode_us", "receiver_fec_max_decode_us"},
        f"{label}.final",
    )
    last_decode = _integer(
        raw_final.get("receiver_fec_last_decode_us"),
        f"{label}.final.receiver_fec_last_decode_us",
    )
    max_decode = _integer(
        raw_final.get("receiver_fec_max_decode_us"),
        f"{label}.final.receiver_fec_max_decode_us",
    )
    if last_decode > max_decode:
        raise QualificationValidationError(
            f"{label}.final last FEC decode exceeds maximum"
        )
    if expected_count == 0 and (last_decode != 0 or max_decode != 0):
        raise QualificationValidationError(
            f"{label}.final unconfigured receiver has FEC decode timing"
        )
    return {
        "requested_count": requested,
        "enabled_count": enabled,
        "deltas": deltas,
        "final": {
            "receiver_fec_last_decode_us": last_decode,
            "receiver_fec_max_decode_us": max_decode,
        },
    }


def _target_transport_item(
    value: Any,
    label: str,
    *,
    expected_logical_device: int | None,
    expected_wire_bytes: int,
) -> dict[str, Any]:
    payload = _object(value, label)
    allowed = {"expected_wire_bytes", "deltas", "final", "fec"}
    if expected_logical_device is not None:
        allowed.add("logical_device")
    _only(payload, allowed, label)
    if expected_logical_device is not None:
        logical_device = _integer(
            payload.get("logical_device"),
            f"{label}.logical_device",
            maximum=4,
        )
        if logical_device != expected_logical_device:
            raise QualificationValidationError(
                f"{label}.logical_device must be {expected_logical_device}"
            )
    observed_wire_bytes = _integer(
        payload.get("expected_wire_bytes"),
        f"{label}.expected_wire_bytes",
        minimum=1,
    )
    if observed_wire_bytes != expected_wire_bytes:
        raise QualificationValidationError(
            f"{label}.expected_wire_bytes must be {expected_wire_bytes}"
        )

    raw_deltas = _object(payload.get("deltas"), f"{label}.deltas")
    _only(raw_deltas, set(_TARGET_TRANSPORT_DELTA_FIELDS), f"{label}.deltas")
    deltas = {
        field: _integer(raw_deltas.get(field), f"{label}.deltas.{field}")
        for field in _TARGET_TRANSPORT_DELTA_FIELDS
    }
    if (
        deltas["full_frame_status_transfers"]
        + deltas["full_frame_write_only_transfers"]
        != deltas["full_frame_transfers"]
    ):
        raise QualificationValidationError(
            f"{label}.deltas must partition every full-frame transfer"
        )
    if (
        deltas["full_frame_status_samples"]
        > deltas["full_frame_status_transfers"]
    ):
        raise QualificationValidationError(
            f"{label}.deltas successful samples exceed status transfers"
        )
    if (
        deltas["full_frame_status_samples"]
        + deltas["full_frame_status_sample_misses"]
        != deltas["full_frame_status_transfers"]
    ):
        raise QualificationValidationError(
            f"{label}.deltas must classify every status transfer"
        )
    if deltas["full_frame_status_samples"] == 0:
        raise QualificationValidationError(
            f"{label}.deltas must contain a successful status sample"
        )
    if deltas["full_frame_status_sample_misses"] != 0:
        raise QualificationValidationError(
            f"{label}.deltas status sample misses must be zero"
        )
    if deltas["full_frame_write_only_transfers"] == 0:
        raise QualificationValidationError(
            f"{label}.deltas must exercise the write-only fast path"
        )

    raw_final = _object(payload.get("final"), f"{label}.final")
    _only(raw_final, set(_TARGET_TRANSPORT_FINAL_FIELDS), f"{label}.final")
    latest_status_version = _integer(
        raw_final.get("receiver_status_version"),
        f"{label}.final.receiver_status_version",
        minimum=3,
    )
    max_status_version_seen = _integer(
        raw_final.get("receiver_status_max_version_seen"),
        f"{label}.final.receiver_status_max_version_seen",
        minimum=7,
    )
    if latest_status_version > max_status_version_seen:
        raise QualificationValidationError(
            f"{label}.final latest status version exceeds observed maximum"
        )
    current_gap = _integer(
        raw_final.get("full_frame_frames_since_status_sample"),
        f"{label}.final.full_frame_frames_since_status_sample",
        maximum=_TARGET_TRANSPORT_MAX_SAMPLE_GAP,
    )
    maximum_gap = _integer(
        raw_final.get("full_frame_max_status_sample_gap"),
        f"{label}.final.full_frame_max_status_sample_gap",
        maximum=_TARGET_TRANSPORT_MAX_SAMPLE_GAP,
    )
    if current_gap > maximum_gap:
        raise QualificationValidationError(
            f"{label}.final current status sample gap exceeds lifetime maximum"
        )
    buffer_size = _integer(
        raw_final.get("spidev_buffer_size"),
        f"{label}.final.spidev_buffer_size",
        minimum=expected_wire_bytes,
    )
    if raw_final.get("full_frame_write_only_supported") is not True:
        raise QualificationValidationError(
            f"{label}.final.full_frame_write_only_supported must be true"
        )
    result = {
        "expected_wire_bytes": observed_wire_bytes,
        "deltas": deltas,
        "final": {
            "receiver_status_version": latest_status_version,
            "receiver_status_max_version_seen": max_status_version_seen,
            "full_frame_frames_since_status_sample": current_gap,
            "full_frame_max_status_sample_gap": maximum_gap,
            "spidev_buffer_size": buffer_size,
            "full_frame_write_only_supported": True,
        },
        "fec": _target_fec_item(
            payload.get("fec"),
            f"{label}.fec",
            expected_count=(
                1
                if expected_logical_device is None or expected_logical_device == 3
                else 0
            ),
            full_frames=(
                None
                if expected_logical_device is None
                else deltas["full_frame_transfers"]
            ),
        ),
    }
    if expected_logical_device is not None:
        result["logical_device"] = expected_logical_device
    return result


def _target_transport_evidence(value: Any) -> dict[str, Any]:
    payload = _object(value, "target qualification evidence.transport")
    _only(
        payload,
        {"aggregate", "devices"},
        "target qualification evidence.transport",
    )
    label = "target qualification evidence.transport"
    aggregate = _target_transport_item(
        payload.get("aggregate"),
        f"{label}.aggregate",
        expected_logical_device=None,
        expected_wire_bytes=max(_TARGET_TRANSPORT_EXPECTED_WIRE_BYTES),
    )
    raw_devices = payload.get("devices")
    if not isinstance(raw_devices, list) or len(raw_devices) != 5:
        raise QualificationValidationError(
            f"{label}.devices must contain exactly five receivers"
        )
    devices_by_id: dict[int, dict[str, Any]] = {}
    for index, item in enumerate(raw_devices):
        raw_item = _object(item, f"{label}.devices[{index}]")
        logical_device = _integer(
            raw_item.get("logical_device"),
            f"{label}.devices[{index}].logical_device",
            maximum=4,
        )
        if logical_device in devices_by_id:
            raise QualificationValidationError(
                f"{label}.devices contains duplicate logical_device {logical_device}"
            )
        devices_by_id[logical_device] = _target_transport_item(
            raw_item,
            f"{label}.devices[{index}]",
            expected_logical_device=logical_device,
            expected_wire_bytes=_TARGET_TRANSPORT_EXPECTED_WIRE_BYTES[logical_device],
        )
    if sorted(devices_by_id) != list(range(5)):
        raise QualificationValidationError(
            f"{label}.devices must contain logical devices 0 through 4"
        )
    devices = [devices_by_id[logical_device] for logical_device in range(5)]
    for field in _TARGET_TRANSPORT_DELTA_FIELDS:
        if aggregate["deltas"][field] != sum(
            device["deltas"][field] for device in devices
        ):
            raise QualificationValidationError(
                f"{label}.aggregate.deltas.{field} drifted from receiver sum"
            )
    for field in _TARGET_FEC_DELTA_FIELDS:
        if aggregate["fec"]["deltas"][field] != sum(
            device["fec"]["deltas"][field] for device in devices
        ):
            raise QualificationValidationError(
                f"{label}.aggregate.fec.deltas.{field} drifted from receiver sum"
            )
    expected_fec_final = {
        "receiver_fec_last_decode_us": max(
            device["fec"]["final"]["receiver_fec_last_decode_us"]
            for device in devices
        ),
        "receiver_fec_max_decode_us": max(
            device["fec"]["final"]["receiver_fec_max_decode_us"]
            for device in devices
        ),
    }
    if aggregate["fec"]["final"] != expected_fec_final:
        raise QualificationValidationError(
            f"{label}.aggregate.fec.final drifted from receiver values"
        )
    expected_final = {
        "receiver_status_version": min(
            device["final"]["receiver_status_version"] for device in devices
        ),
        "receiver_status_max_version_seen": min(
            device["final"]["receiver_status_max_version_seen"]
            for device in devices
        ),
        "full_frame_frames_since_status_sample": max(
            device["final"]["full_frame_frames_since_status_sample"]
            for device in devices
        ),
        "full_frame_max_status_sample_gap": max(
            device["final"]["full_frame_max_status_sample_gap"]
            for device in devices
        ),
        "spidev_buffer_size": min(
            device["final"]["spidev_buffer_size"] for device in devices
        ),
        "full_frame_write_only_supported": all(
            device["final"]["full_frame_write_only_supported"] is True
            for device in devices
        ),
    }
    if aggregate["final"] != expected_final:
        raise QualificationValidationError(
            f"{label}.aggregate.final drifted from receiver values"
        )
    return {"aggregate": aggregate, "devices": devices}


def _target_runtime_identity(value: Any) -> dict[str, Any]:
    label = "target qualification evidence.runtime_identity"
    payload = _object(value, label)
    _only(
        payload,
        {
            "release_id",
            "controller_session_id",
            "controller_state_revision",
            "current_identity_digest",
        },
        label,
    )
    session_id = payload.get("controller_session_id")
    if (
        not isinstance(session_id, str)
        or _CONTROLLER_SESSION_ID.fullmatch(session_id) is None
    ):
        raise QualificationValidationError(
            f"{label}.controller_session_id must be a lowercase 128-bit hexadecimal ID"
        )
    return {
        "release_id": _digest(payload.get("release_id"), f"{label}.release_id"),
        "controller_session_id": session_id,
        "controller_state_revision": _integer(
            payload.get("controller_state_revision"),
            f"{label}.controller_state_revision",
        ),
        "current_identity_digest": _digest(
            payload.get("current_identity_digest"),
            f"{label}.current_identity_digest",
        ),
    }


def normalize_target_qualification_evidence(value: Any) -> dict[str, Any]:
    """Normalize one atomically retained controller/receiver capture.

    The envelope deliberately contains target evidence only. Browser evidence
    is produced by the browser Check and electrical evidence remains absent
    unless an actual calibrated instrument supplied it during capture.
    """

    payload = _object(value, "target qualification evidence")
    _only(
        payload,
        {
            "schema",
            "schema_version",
            "revision",
            "binding_digest",
            "captured_at",
            "environment",
            "runtime_identity",
            "transport",
            "evidence",
        },
        "target qualification evidence",
    )
    if payload.get("schema") != TARGET_EVIDENCE_SCHEMA:
        raise QualificationValidationError(
            "target qualification evidence.schema must be "
            f"{TARGET_EVIDENCE_SCHEMA!r}"
        )
    if payload.get("schema_version") != TARGET_EVIDENCE_VERSION:
        raise QualificationValidationError(
            "target qualification evidence.schema_version must be "
            f"{TARGET_EVIDENCE_VERSION}"
        )
    binding_digest = _digest(
        payload.get("binding_digest"),
        "target qualification evidence.binding_digest",
    )
    captured_at = _integer(
        payload.get("captured_at"),
        "target qualification evidence.captured_at",
        minimum=1,
    )
    raw_evidence = payload.get("evidence")
    if not isinstance(raw_evidence, list):
        raise QualificationValidationError(
            "target qualification evidence.evidence must be an array"
        )
    evidence = [_evidence(item, index) for index, item in enumerate(raw_evidence)]
    sources = [item["source"] for item in evidence]
    if sorted(sources) != ["controller_pi", "receiver"]:
        raise QualificationValidationError(
            "target qualification evidence must contain exactly one "
            "controller_pi and one receiver item"
        )
    for item in evidence:
        if item["binding_digest"] != binding_digest:
            raise QualificationValidationError(
                f"{item['source']} evidence does not match the envelope binding"
            )
        if item["captured_at"] != captured_at:
            raise QualificationValidationError(
                f"{item['source']} evidence does not match the envelope capture time"
            )
    evidence.sort(key=lambda item: EVIDENCE_SOURCES.index(item["source"]))
    runtime_identity = _target_runtime_identity(payload.get("runtime_identity"))
    transport = _target_transport_evidence(payload.get("transport"))
    transport_digest = canonical_json_sha256(transport)
    receiver = next(item for item in evidence if item["source"] == "receiver")
    if receiver["transport_digest"] != transport_digest:
        raise QualificationValidationError(
            "receiver evidence transport_digest does not match normalized transport proof"
        )
    return {
        "schema": TARGET_EVIDENCE_SCHEMA,
        "schema_version": TARGET_EVIDENCE_VERSION,
        "revision": _integer(
            payload.get("revision"),
            "target qualification evidence.revision",
            minimum=1,
        ),
        "binding_digest": binding_digest,
        "captured_at": captured_at,
        "environment": _text(
            payload.get("environment"),
            "target qualification evidence.environment",
        ),
        "runtime_identity": runtime_identity,
        "transport": transport,
        "evidence": evidence,
    }


def load_target_qualification_evidence(path: str | Path) -> dict[str, Any]:
    """Load one strict retained capture without contacting the target."""

    try:
        raw = Path(path).read_text(encoding="utf-8")
        value = json.loads(
            raw,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except QualificationValidationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QualificationValidationError(
            f"could not load target qualification evidence: {exc}"
        ) from exc
    return normalize_target_qualification_evidence(value)


def _gate(reasons: list[str]) -> dict[str, Any]:
    return {"passed": not reasons, "reasons": sorted(reasons)}


def evaluate_activation_qualification(
    record: Any,
    budget: Any,
    *,
    now_ms: int,
) -> dict[str, Any]:
    """Evaluate PERF-01 and POWER-01 without sampling or touching hardware."""

    normalized_record = normalize_activation_qualification_record(record)
    normalized_budget = normalize_installation_qualification_budget(budget)
    now = _integer(now_ms, "qualification evaluation now_ms", minimum=1)
    budget_digest = canonical_json_sha256(normalized_budget)
    binding_digest = canonical_json_sha256(normalized_record["binding"])
    frame_budget_ms = 1000.0 / normalized_record["binding"]["target_fps"]

    identity_reasons: list[str] = []
    freshness_reasons: list[str] = []
    performance_reasons: list[str] = []
    power_reasons: list[str] = []

    if normalized_record["budget"] != {
        "revision": normalized_budget["revision"],
        "digest": budget_digest,
    }:
        identity_reasons.append("budget_identity_mismatch")
    if normalized_record["binding"]["geometry"] != normalized_budget["geometry"]:
        identity_reasons.append("installation_geometry_mismatch")

    by_source = {item["source"]: item for item in normalized_record["evidence"]}
    maximum_age = normalized_budget["maximum_evidence_age_ms"]
    for source in EVIDENCE_SOURCES:
        item = by_source.get(source)
        if item is None:
            freshness_reasons.append(f"missing_{source}_evidence")
            continue
        if item["binding_digest"] != binding_digest:
            identity_reasons.append(f"{source}_binding_mismatch")
        age = now - item["captured_at"]
        if age < 0:
            freshness_reasons.append(f"future_{source}_evidence")
        elif maximum_age is None or age > maximum_age:
            freshness_reasons.append(f"stale_{source}_evidence")
        if item["frame_time_ms"]["p95"] > frame_budget_ms:
            performance_reasons.append(f"{source}_p95_exceeds_frame_budget")
        if item["cadence"]["observed_fps"] < normalized_record["binding"]["target_fps"]:
            performance_reasons.append(f"{source}_cadence_below_target_fps")

    if normalized_budget["calibration"]["status"] != "calibrated":
        power_reasons.append("installation_budget_uncalibrated")
    physical = normalized_budget["budgets"]
    brightness = normalized_record["binding"]["brightness"]
    maximum_brightness = physical["brightness"]["maximum_controller_value"]
    if maximum_brightness is None:
        power_reasons.append("brightness_budget_unknown")
    elif brightness > maximum_brightness:
        power_reasons.append("activation_brightness_exceeds_budget")

    calibrated_measurements = []
    for source in ("controller_pi", "receiver"):
        item = by_source.get(source)
        electrical = None if item is None else item["electrical"]
        if electrical is not None and electrical["kind"] == "calibrated_measurement":
            calibrated_measurements.append((source, electrical))
    if not calibrated_measurements:
        power_reasons.append("missing_calibrated_target_electrical_evidence")

    minimum_voltage = physical["voltage"]["minimum_mean_v"]
    maximum_voltage = physical["voltage"]["maximum_p99_v"]
    maximum_current = physical["current"]["maximum_a"]
    headroom = physical["safety"]["required_current_headroom_ratio"]
    maximum_power = physical["safety"]["maximum_p99_power_w"]
    for source, electrical in calibrated_measurements:
        if electrical["budget_digest"] != budget_digest:
            identity_reasons.append(f"{source}_electrical_budget_mismatch")
        if electrical["brightness"] != brightness:
            power_reasons.append(f"{source}_electrical_brightness_mismatch")
        voltage = electrical["voltage_v"]
        current = electrical["current_a"]
        if minimum_voltage is None or voltage["mean"] < minimum_voltage:
            power_reasons.append(f"{source}_voltage_below_budget")
        if maximum_voltage is None or voltage["p99"] > maximum_voltage:
            power_reasons.append(f"{source}_voltage_exceeds_budget")
        if maximum_current is None or headroom is None:
            power_reasons.append(f"{source}_current_budget_unknown")
        elif current["max"] > maximum_current * (1.0 - headroom):
            power_reasons.append(f"{source}_current_exceeds_safe_budget")
        if maximum_power is None or voltage["p99"] * current["p99"] > maximum_power:
            power_reasons.append(f"{source}_power_exceeds_safe_budget")

    gates = {
        "identity": _gate(identity_reasons),
        "freshness": _gate(freshness_reasons),
        "performance": _gate(performance_reasons),
        "power": _gate(power_reasons),
    }
    reasons = sorted(
        reason
        for gate in gates.values()
        for reason in gate["reasons"]
    )
    return {
        "schema": QUALIFICATION_RESULT_SCHEMA,
        "schema_version": QUALIFICATION_RESULT_VERSION,
        "record_digest": canonical_json_sha256(normalized_record),
        "binding_digest": binding_digest,
        "budget_digest": budget_digest,
        "frame_budget_ms": frame_budget_ms,
        "qualified": not reasons,
        "gates": gates,
        "reasons": reasons,
        "advisory": {
            "browser_electrical_estimate_present": bool(
                by_source.get("browser")
                and by_source["browser"]["electrical"] is not None
                and by_source["browser"]["electrical"]["kind"]
                == "uncalibrated_estimate"
            )
        },
    }


__all__ = [
    "DEFAULT_INSTALLATION_BUDGET_PATH",
    "EVIDENCE_SOURCES",
    "INSTALLATION_BUDGET_SCHEMA",
    "INSTALLATION_BUDGET_VERSION",
    "QUALIFICATION_RECORD_SCHEMA",
    "QUALIFICATION_RECORD_VERSION",
    "TARGET_EVIDENCE_SCHEMA",
    "TARGET_EVIDENCE_VERSION",
    "QualificationValidationError",
    "activation_qualification_binding_digest",
    "activation_qualification_record_digest",
    "evaluate_activation_qualification",
    "installation_qualification_budget_digest",
    "load_installation_qualification_budget",
    "load_target_qualification_evidence",
    "normalize_activation_qualification_record",
    "normalize_installation_qualification_budget",
    "normalize_target_qualification_evidence",
]
