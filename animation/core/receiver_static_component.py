"""Catalog metadata and host preview for the compiled receiver rainbow.

The compiled rainbow is firmware-owned code, not a Python animation plugin and
not an uploadable native bundle.  This module deliberately provides only the
side-effect-free product seams shared by catalog and preview code:

* a descriptor that appears only when both receiver rollout gates are enabled;
* strict validation for its two authored parameters; and
* a byte-exact host implementation of the receiver's integer renderer.

Runtime capability/status agreement remains the proof that receivers can run
the component.  The stable digests below bind desired state to this built-in
contract; they are explicitly not claims about the flashed firmware bytes.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from typing import Any, Optional

import numpy as np

from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT

from .feature_flags import AnimationPipelineFeatureFlags
from .presentation_contracts import (
    COMPONENT_DESCRIPTOR_SCHEMA,
    COMPONENT_DESCRIPTOR_VERSION,
    NEXT_DEADLINE_SEMANTICS,
    CadenceContract,
    ComponentDescriptor,
    ComponentProvider,
    ComponentRole,
    TimingAdapter,
)


COMPILED_RAINBOW_PLUGIN_ID = "compiled_rainbow"
COMPILED_RAINBOW_COMPONENT_ID = 1
COMPILED_RAINBOW_PREFERRED_CADENCE_HZ = 30
COMPILED_RAINBOW_COMMON_SEED = 0

COMPILED_RAINBOW_PERIOD_PIXELS = 32
COMPILED_RAINBOW_CYCLE_US = 1_000_000
COMPILED_RAINBOW_HUE_STEPS = 6 * 256
COMPILED_RAINBOW_SPATIAL_STEP = (
    COMPILED_RAINBOW_HUE_STEPS // COMPILED_RAINBOW_PERIOD_PIXELS
)
Q8_8_ONE = 256

# These values are explicit stable identities for the v1 built-in contract.
# ``EXPECTED_PAYLOAD_DIGEST`` fills the frozen receiver-native ComponentRef
# shape, but receivers do not currently report or prove the compiled payload
# hash. Capability bits, component ID, and receiver agreement are authoritative.
COMPILED_RAINBOW_CONTRACT_DIGEST = (
    "b112cc36c811423780ae5bd3812f9656e7c6de85bf76373735bfbc2cdd6a4e47"
)
# ``bundle_digest`` is the frozen ComponentRef field name. For this firmware
# built-in it aliases the contract identity rather than an uploadable bundle.
COMPILED_RAINBOW_BUNDLE_DIGEST = COMPILED_RAINBOW_CONTRACT_DIGEST
COMPILED_RAINBOW_EXPECTED_PAYLOAD_DIGEST = (
    "b968c79c70030e8cecc45e74dc9f38b542713f42509c40c98ec10a596e6f2290"
)

_PARAMETER_SCHEMA = {
    "preferred_cadence_hz": {
        "type": "int",
        "min": 1,
        "max": 200,
        "default": COMPILED_RAINBOW_PREFERRED_CADENCE_HZ,
        "description": "Receiver-local render cadence in frames per second",
    },
    "common_seed": {
        "type": "int",
        "min": 0,
        "max": 0xFFFF_FFFF,
        "default": COMPILED_RAINBOW_COMMON_SEED,
        "description": "Common deterministic phase seed shared by every receiver",
    },
}
_DEFAULTS = {
    name: definition["default"] for name, definition in _PARAMETER_SCHEMA.items()
}
_REQUIRED_FEATURES = (
    "receiver_local_background",
    "receiver_sparse_overlay",
)
_REQUIRED_CAPABILITIES = (
    "static_local_background",
    "presentation_context_v1",
    "sparse_overlay_v1",
)


def validate_compiled_rainbow_parameters(
    parameters: Optional[Mapping[str, Any]] = None,
) -> dict[str, int]:
    """Return complete canonical authored parameters for the built-in renderer."""

    if parameters is None:
        return dict(_DEFAULTS)
    if not isinstance(parameters, Mapping):
        raise TypeError("compiled rainbow parameters must be a mapping")
    unknown = set(parameters).difference(_PARAMETER_SCHEMA)
    if unknown:
        raise ValueError(
            "compiled rainbow parameters contain unsupported fields: "
            + ", ".join(sorted(str(name) for name in unknown))
        )
    result = dict(_DEFAULTS)
    result.update(parameters)
    for name, definition in _PARAMETER_SCHEMA.items():
        value = result[name]
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"compiled rainbow {name} must be an integer")
        if not definition["min"] <= value <= definition["max"]:
            raise ValueError(
                f"compiled rainbow {name} must be between "
                f"{definition['min']} and {definition['max']}"
            )
    return result


def _descriptor_payload() -> dict[str, Any]:
    cadence = CadenceContract(
        "fixed_fps", preferred_fps=COMPILED_RAINBOW_PREFERRED_CADENCE_HZ
    )
    preview = {
        "kind": "host_contract_renderer",
        "renderer": (
            "animation.core.receiver_static_component:"
            "render_compiled_rainbow_preview"
        ),
        "label": "Host preview of receiver-rendered compiled rainbow",
        "framebuffer_readback": False,
        "layout": "strip_major_global_coordinates",
        "time_domain": "receiver_scene_time_us",
    }
    build = {
        "artifact_kind": "firmware_builtin",
        "component_id": COMPILED_RAINBOW_COMPONENT_ID,
        "contract_revision": 1,
        "contract_digest": COMPILED_RAINBOW_CONTRACT_DIGEST,
        "bundle_digest": COMPILED_RAINBOW_BUNDLE_DIGEST,
        "expected_payload_digest": COMPILED_RAINBOW_EXPECTED_PAYLOAD_DIGEST,
        "payload_digest_proven": False,
        "digest_semantics": (
            "stable builtin contract binding; not a receiver-reported firmware hash"
        ),
        "runtime_proof": {
            "authority": "receiver capability/status agreement",
            "required_capabilities": list(_REQUIRED_CAPABILITIES),
            "required_component_id": COMPILED_RAINBOW_COMPONENT_ID,
        },
        "dynamic_upload": False,
    }
    validated = ComponentDescriptor(
        manifest_version=COMPONENT_DESCRIPTOR_VERSION,
        plugin_id=COMPILED_RAINBOW_PLUGIN_ID,
        name="Compiled Rainbow",
        description=(
            "Firmware-built diagonal rainbow for receiver-local background playback"
        ),
        icon="🌈",
        gallery="show",
        provider=ComponentProvider.RECEIVER_NATIVE,
        role=ComponentRole.BACKGROUND,
        entrypoint=f"receiver_builtin:{COMPILED_RAINBOW_COMPONENT_ID}",
        parameter_schema=_PARAMETER_SCHEMA,
        defaults=_DEFAULTS,
        cadence=cadence,
        timing_adapter=TimingAdapter.WALL_CLOCK,
        vibe_color_policy="preserve",
        vibe_capabilities=("luminance",),
        installation_profile_requirements=(),
        preview=preview,
        build=build,
    )
    return {
        "schema": COMPONENT_DESCRIPTOR_SCHEMA,
        "manifest_version": validated.manifest_version,
        "plugin_id": validated.plugin_id,
        "name": validated.name,
        "description": validated.description,
        "icon": validated.icon,
        "gallery": validated.gallery,
        "provider": validated.provider.value,
        "role": validated.role.value,
        "entrypoint": validated.entrypoint,
        "parameter_schema": copy.deepcopy(_PARAMETER_SCHEMA),
        "defaults": dict(_DEFAULTS),
        "cadence": {
            "mode": cadence.mode.value,
            "preferred_fps": cadence.preferred_fps,
            "next_deadline_semantics": NEXT_DEADLINE_SEMANTICS,
        },
        "timing_adapter": validated.timing_adapter.value,
        "vibe_capabilities": list(validated.vibe_capabilities),
        "vibe_color_policy": validated.vibe_color_policy,
        "installation_profile_requirements": [],
        "preview": preview,
        "build": build,
        "feature_requirements": list(_REQUIRED_FEATURES),
        "compatibility": {
            "legacy_manifest": False,
            "classification": "receiver_firmware_builtin",
            "composable": True,
            "implementation_loaded": True,
            "parameter_metadata": "builtin_contract",
            "diagnostic": (
                "Receiver-local compiled background; runtime capability and status "
                "agreement are required before activation."
            ),
        },
    }


_DESCRIPTOR = _descriptor_payload()


def receiver_static_component_descriptor(
    flags: AnimationPipelineFeatureFlags | Mapping[str, Any] | None = None,
) -> Optional[dict[str, Any]]:
    """Return a detached descriptor only when both hybrid rollout gates are on."""

    resolved = (
        flags
        if isinstance(flags, AnimationPipelineFeatureFlags)
        else AnimationPipelineFeatureFlags.from_mapping(flags)
    )
    if not (
        resolved.receiver_local_background and resolved.receiver_sparse_overlay
    ):
        return None
    return copy.deepcopy(_DESCRIPTOR)


def receiver_static_component_catalog(
    flags: AnimationPipelineFeatureFlags | Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return the zero-or-one receiver-static catalog contribution."""

    descriptor = receiver_static_component_descriptor(flags)
    return [] if descriptor is None else [descriptor]


def _bounded_integer(name: str, value: Any, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _hue_to_rgb(hue: np.ndarray, output: np.ndarray) -> None:
    """Apply the firmware's six-sector integer hue wheel in-place."""

    sector = np.right_shift(hue, 8)
    ramp = np.bitwise_and(hue, 0xFF).astype(np.uint16, copy=False)
    falling = 0xFF - ramp
    output.fill(0)

    masks = tuple(sector == index for index in range(6))
    output[masks[0], 0] = 0xFF
    output[masks[0], 1] = ramp[masks[0]]
    output[masks[1], 0] = falling[masks[1]]
    output[masks[1], 1] = 0xFF
    output[masks[2], 1] = 0xFF
    output[masks[2], 2] = ramp[masks[2]]
    output[masks[3], 1] = falling[masks[3]]
    output[masks[3], 2] = 0xFF
    output[masks[4], 0] = ramp[masks[4]]
    output[masks[4], 2] = 0xFF
    output[masks[5], 0] = 0xFF
    output[masks[5], 2] = falling[masks[5]]


def render_compiled_rainbow_preview(
    elapsed_us: int,
    parameters: Optional[Mapping[str, Any]] = None,
    *,
    strip_count: int = DEFAULT_STRIP_COUNT,
    leds_per_strip: int = DEFAULT_LEDS_PER_STRIP,
    global_strip_offset: int = 0,
    luminance_q8_8: int = Q8_8_ONE,
    out: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Render the receiver's compiled rainbow into strip-major RGB bytes.

    ``elapsed_us`` intentionally uses the firmware's integer scene-time unit so
    previews, stitched board slices, and portable receiver vectors have no
    floating-point timing ambiguity. ``preferred_cadence_hz`` is validated but
    does not change the pixels at an already selected semantic time.
    """

    elapsed = _bounded_integer("elapsed_us", elapsed_us, 0, 0xFFFF_FFFF_FFFF_FFFF)
    width = _bounded_integer("strip_count", strip_count, 1, 0xFF)
    height = _bounded_integer("leds_per_strip", leds_per_strip, 1, 0xFFFF)
    offset = _bounded_integer("global_strip_offset", global_strip_offset, 0, 0xFFFF_FFFF)
    if offset + width - 1 > 0xFFFF_FFFF:
        raise ValueError("global strip coordinates overflow unsigned 32-bit range")
    luminance = _bounded_integer("luminance_q8_8", luminance_q8_8, 0, Q8_8_ONE)
    authored = validate_compiled_rainbow_parameters(parameters)

    pixel_count = width * height
    if out is None:
        result = np.empty((pixel_count, 3), dtype=np.uint8)
    else:
        if not isinstance(out, np.ndarray):
            raise TypeError("compiled rainbow output must be a numpy array")
        if out.dtype != np.uint8 or out.shape != (pixel_count, 3):
            raise ValueError(
                "compiled rainbow output must have dtype uint8 and shape "
                f"({pixel_count}, 3)"
            )
        if not out.flags.c_contiguous:
            raise ValueError("compiled rainbow output must be C-contiguous")
        result = out

    motion = (
        (elapsed % COMPILED_RAINBOW_CYCLE_US) * COMPILED_RAINBOW_HUE_STEPS
    ) // COMPILED_RAINBOW_CYCLE_US
    seed_phase = authored["common_seed"] % COMPILED_RAINBOW_HUE_STEPS
    strips = np.arange(offset, offset + width, dtype=np.uint64)[:, None]
    leds = np.arange(height, dtype=np.uint64)[None, :]
    spatial = (
        ((strips + leds) % COMPILED_RAINBOW_PERIOD_PIXELS)
        * COMPILED_RAINBOW_SPATIAL_STEP
    )
    hue = (
        spatial
        + seed_phase
        + COMPILED_RAINBOW_HUE_STEPS
        - motion
    ) % COMPILED_RAINBOW_HUE_STEPS
    _hue_to_rgb(hue.reshape(-1), result)

    if luminance != Q8_8_ONE:
        scaled = result.astype(np.uint16)
        scaled *= luminance
        scaled += 128
        np.floor_divide(scaled, Q8_8_ONE, out=scaled)
        np.minimum(scaled, 255, out=scaled)
        np.copyto(result, scaled, casting="unsafe")
    return result


def preview_elapsed_us(elapsed_seconds: Any) -> int:
    """Convert an API preview time to the receiver's non-negative time unit."""

    if isinstance(elapsed_seconds, bool) or not isinstance(
        elapsed_seconds, (int, float)
    ):
        raise TypeError("preview elapsed seconds must be a finite number")
    value = float(elapsed_seconds)
    if not math.isfinite(value) or value < 0:
        raise ValueError("preview elapsed seconds must be finite and non-negative")
    elapsed_us = math.floor(value * COMPILED_RAINBOW_CYCLE_US)
    if elapsed_us > 0xFFFF_FFFF_FFFF_FFFF:
        raise ValueError("preview elapsed seconds overflow receiver scene time")
    return elapsed_us


__all__ = [
    "COMPILED_RAINBOW_COMMON_SEED",
    "COMPILED_RAINBOW_BUNDLE_DIGEST",
    "COMPILED_RAINBOW_COMPONENT_ID",
    "COMPILED_RAINBOW_CONTRACT_DIGEST",
    "COMPILED_RAINBOW_CYCLE_US",
    "COMPILED_RAINBOW_EXPECTED_PAYLOAD_DIGEST",
    "COMPILED_RAINBOW_PLUGIN_ID",
    "COMPILED_RAINBOW_PREFERRED_CADENCE_HZ",
    "Q8_8_ONE",
    "preview_elapsed_us",
    "receiver_static_component_catalog",
    "receiver_static_component_descriptor",
    "render_compiled_rainbow_preview",
    "validate_compiled_rainbow_parameters",
]
