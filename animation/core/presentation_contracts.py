"""Dormant version 1 presentation contracts for the revamped pipeline.

These value objects freeze validation, naming, and serialization boundaries for
later phases.  Nothing in the current manager imports or activates them.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional, Tuple

import numpy as np

from animation.core.compositing import normalize_optional_dirty_ranges


COMPONENT_DESCRIPTOR_SCHEMA = "ledgrid.component-descriptor"
COMPONENT_DESCRIPTOR_VERSION = 1
SCENE_STATE_SCHEMA = "ledgrid.scene-state"
SCENE_STATE_VERSION = 1
DESIRED_DISPLAY_STATE_SCHEMA = "ledgrid.desired-display-state"
DESIRED_DISPLAY_STATE_VERSION = 1
VIBE_STATE_SCHEMA = "ledgrid.vibe-state"
VIBE_STATE_VERSION = 1
VIBE_PROFILE_SCHEMA = "ledgrid.vibe-profile"
VIBE_PROFILE_VERSION = 1
ANIMATION_RUNTIME_CONTEXT_SCHEMA = "ledgrid.animation-runtime-context"
ANIMATION_RUNTIME_CONTEXT_VERSION = 1
FRAME_CONTRACT_SCHEMA = "ledgrid.layer-frame"
FRAME_CONTRACT_VERSION = 1

# Deadlines are absolute seconds on the unscaled scene clock whose zero is the
# scene epoch.  They are not wall-clock timestamps or relative sleep durations.
NEXT_DEADLINE_SEMANTICS = "absolute_unscaled_seconds_since_scene_epoch"

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EMPTY_MAPPING: Mapping[str, Any] = MappingProxyType({})


class ComponentProvider(str, Enum):
    PYTHON = "python"
    RECEIVER_NATIVE = "receiver_native"


class ComponentRole(str, Enum):
    BACKGROUND = "background"
    OVERLAY = "overlay"
    FULL_SCENE = "full_scene"


class TimingAdapter(str, Enum):
    LEGACY_SPEED_PARAM = "legacy_speed_param"
    SCALED_CONTEXT = "scaled_context"
    WALL_CLOCK = "wall_clock"


class CadenceMode(str, Enum):
    FIXED_FPS = "fixed_fps"
    EVENT_DRIVEN = "event_driven"


class ClipPolicy(str, Enum):
    CLIP_TO_WALL = "clip_to_wall"


class ForegroundStalePolicy(str, Enum):
    CLEAR_AFTER_LEASE = "clear_after_lease"
    HOLD = "hold"


@dataclass(frozen=True)
class CadenceContract:
    """Component scheduling declaration; manager behavior is added in Phase 2."""

    mode: CadenceMode
    preferred_fps: Optional[float] = None
    next_deadline_semantics: str = NEXT_DEADLINE_SEMANTICS

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", _enum_value("mode", self.mode, CadenceMode))
        if self.next_deadline_semantics != NEXT_DEADLINE_SEMANTICS:
            raise ValueError(
                "next_deadline_semantics must be "
                f"{NEXT_DEADLINE_SEMANTICS!r}, got {self.next_deadline_semantics!r}"
            )
        if self.mode is CadenceMode.FIXED_FPS:
            if self.preferred_fps is None:
                raise ValueError("fixed_fps cadence requires preferred_fps")
            _finite_number("preferred_fps", self.preferred_fps, minimum=0.001, maximum=1000.0)
        elif self.preferred_fps is not None:
            raise ValueError("event_driven cadence must not declare preferred_fps")


@dataclass(frozen=True)
class ComponentDescriptor:
    """Versioned descriptor for one selectable repository component."""

    manifest_version: int
    plugin_id: str
    name: str
    description: str
    icon: str
    gallery: str
    provider: ComponentProvider
    role: ComponentRole
    entrypoint: str
    parameter_schema: Mapping[str, Any]
    defaults: Mapping[str, Any]
    cadence: CadenceContract
    timing_adapter: TimingAdapter = TimingAdapter.LEGACY_SPEED_PARAM
    vibe_capabilities: Tuple[str, ...] = ()
    installation_profile_requirements: Tuple[str, ...] = ()
    preview: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_MAPPING)
    build: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_MAPPING)
    schema: str = COMPONENT_DESCRIPTOR_SCHEMA

    def __post_init__(self) -> None:
        _schema_version("component descriptor", self.schema, COMPONENT_DESCRIPTOR_SCHEMA, self.manifest_version)
        _identifier("plugin_id", self.plugin_id)
        for name in ("name", "description", "icon", "entrypoint"):
            _nonempty_string(name, getattr(self, name))
        if self.gallery not in {"show", "test"}:
            raise ValueError("gallery must be 'show' or 'test'")
        object.__setattr__(self, "provider", _enum_value("provider", self.provider, ComponentProvider))
        object.__setattr__(self, "role", _enum_value("role", self.role, ComponentRole))
        object.__setattr__(
            self, "timing_adapter", _enum_value("timing_adapter", self.timing_adapter, TimingAdapter)
        )
        if not isinstance(self.cadence, CadenceContract):
            raise TypeError("cadence must be a CadenceContract")
        for name in ("parameter_schema", "defaults", "preview", "build"):
            object.__setattr__(self, name, _immutable_mapping(name, getattr(self, name)))
        for name in ("vibe_capabilities", "installation_profile_requirements"):
            values = _identifier_tuple(name, getattr(self, name))
            object.__setattr__(self, name, values)


@dataclass(frozen=True)
class ComponentRef:
    plugin_id: str
    provider: ComponentProvider
    preset_id: Optional[str] = None
    preset_fingerprint: Optional[str] = None
    parameter_overrides: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_MAPPING)
    resolved_parameters: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_MAPPING)
    bundle_digest: Optional[str] = None
    expected_payload_digest: Optional[str] = None

    def __post_init__(self) -> None:
        _identifier("plugin_id", self.plugin_id)
        object.__setattr__(self, "provider", _enum_value("provider", self.provider, ComponentProvider))
        if self.preset_id is None:
            if self.preset_fingerprint is not None:
                raise ValueError("preset_fingerprint requires preset_id")
        else:
            _identifier("preset_id", self.preset_id)
            _digest("preset_fingerprint", self.preset_fingerprint)
        for name in ("parameter_overrides", "resolved_parameters"):
            object.__setattr__(self, name, _immutable_mapping(name, getattr(self, name)))
        if self.provider is ComponentProvider.RECEIVER_NATIVE:
            _digest("bundle_digest", self.bundle_digest)
            _digest("expected_payload_digest", self.expected_payload_digest)
        elif self.bundle_digest is not None or self.expected_payload_digest is not None:
            raise ValueError("Python component references must not carry native payload digests")


@dataclass(frozen=True)
class OverlayPlacement:
    strip_translation: int = 0
    led_translation: int = 0
    clip_policy: ClipPolicy = ClipPolicy.CLIP_TO_WALL

    def __post_init__(self) -> None:
        for name in ("strip_translation", "led_translation"):
            _bounded_int(name, getattr(self, name), -(2**31), 2**31 - 1)
        object.__setattr__(self, "clip_policy", _enum_value("clip_policy", self.clip_policy, ClipPolicy))


@dataclass(frozen=True)
class StalePolicy:
    policy: ForegroundStalePolicy
    lease_ms: Optional[int] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy", _enum_value("policy", self.policy, ForegroundStalePolicy))
        if self.policy is ForegroundStalePolicy.CLEAR_AFTER_LEASE:
            _bounded_int("lease_ms", self.lease_ms, 1, 2**32 - 1)
        elif self.lease_ms is not None:
            raise ValueError("hold stale policy must not declare lease_ms")


@dataclass(frozen=True)
class OverlayRef:
    slot_id: str
    component: ComponentRef
    enabled: bool
    opacity: int
    placement: OverlayPlacement
    stale_policy: StalePolicy

    def __post_init__(self) -> None:
        _identifier("slot_id", self.slot_id)
        if not isinstance(self.component, ComponentRef):
            raise TypeError("component must be a ComponentRef")
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be a bool")
        _bounded_int("opacity", self.opacity, 0, 255)
        if not isinstance(self.placement, OverlayPlacement):
            raise TypeError("placement must be an OverlayPlacement")
        if not isinstance(self.stale_policy, StalePolicy):
            raise TypeError("stale_policy must be a StalePolicy")


@dataclass(frozen=True)
class SceneState:
    revision: int
    background: ComponentRef
    overlays: Tuple[OverlayRef, ...]
    known_python_fallback: ComponentRef
    schema_version: int = SCENE_STATE_VERSION
    schema: str = SCENE_STATE_SCHEMA

    def __post_init__(self) -> None:
        _schema_version("scene state", self.schema, SCENE_STATE_SCHEMA, self.schema_version)
        _uint64("revision", self.revision)
        if not isinstance(self.background, ComponentRef):
            raise TypeError("background must be a ComponentRef")
        if not isinstance(self.known_python_fallback, ComponentRef):
            raise TypeError("known_python_fallback must be a ComponentRef")
        if self.known_python_fallback.provider is not ComponentProvider.PYTHON:
            raise ValueError("known_python_fallback must use the python provider")
        if not isinstance(self.overlays, tuple) or not all(isinstance(item, OverlayRef) for item in self.overlays):
            raise TypeError("overlays must be a tuple of OverlayRef values")
        slot_ids = [overlay.slot_id for overlay in self.overlays]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("overlay slot_id values must be unique within a scene")


@dataclass(frozen=True)
class VibeState:
    revision: int
    vibe_id: str
    profile_version: int
    resolved_profile_digest: str
    schema_version: int = VIBE_STATE_VERSION
    schema: str = VIBE_STATE_SCHEMA

    def __post_init__(self) -> None:
        _schema_version("vibe state", self.schema, VIBE_STATE_SCHEMA, self.schema_version)
        _uint64("revision", self.revision)
        _identifier("vibe_id", self.vibe_id)
        _bounded_int("profile_version", self.profile_version, 1, 2**31 - 1)
        _digest("resolved_profile_digest", self.resolved_profile_digest)


@dataclass(frozen=True)
class VibeProfile:
    """Canonical independently serialized atmosphere profile."""

    vibe_id: str
    profile_version: int
    palette_roles: Mapping[str, Any]
    tempo_scale: float
    luminance_scale: float
    capability_values: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_MAPPING)
    schema_version: int = VIBE_PROFILE_VERSION
    schema: str = VIBE_PROFILE_SCHEMA

    def __post_init__(self) -> None:
        _schema_version("vibe profile", self.schema, VIBE_PROFILE_SCHEMA, self.schema_version)
        _identifier("vibe_id", self.vibe_id)
        _bounded_int("profile_version", self.profile_version, 1, 2**31 - 1)
        _finite_number("tempo_scale", self.tempo_scale, minimum=0.01, maximum=100.0)
        _finite_number("luminance_scale", self.luminance_scale, minimum=0.0, maximum=1.0)
        for name in ("palette_roles", "capability_values"):
            object.__setattr__(self, name, _immutable_mapping(name, getattr(self, name)))
        _validate_palette_roles(self.palette_roles)


@dataclass(frozen=True)
class OutputState:
    master_brightness: float
    operator_tempo_scale: float
    power: bool

    def __post_init__(self) -> None:
        _finite_number("master_brightness", self.master_brightness, minimum=0.0, maximum=1.0)
        _finite_number("operator_tempo_scale", self.operator_tempo_scale, minimum=0.01, maximum=100.0)
        if not isinstance(self.power, bool):
            raise TypeError("power must be a bool")


@dataclass(frozen=True)
class DesiredDisplayState:
    revision: int
    scene: SceneState
    vibe: VibeState
    plant_modifiers: Mapping[str, Any]
    installation_profile_digest: str
    output: OutputState
    schema_version: int = DESIRED_DISPLAY_STATE_VERSION
    schema: str = DESIRED_DISPLAY_STATE_SCHEMA

    def __post_init__(self) -> None:
        _schema_version(
            "desired display state",
            self.schema,
            DESIRED_DISPLAY_STATE_SCHEMA,
            self.schema_version,
        )
        _uint64("revision", self.revision)
        if not isinstance(self.scene, SceneState):
            raise TypeError("scene must be a SceneState")
        if not isinstance(self.vibe, VibeState):
            raise TypeError("vibe must be a VibeState")
        if not isinstance(self.output, OutputState):
            raise TypeError("output must be an OutputState")
        object.__setattr__(
            self, "plant_modifiers", _immutable_mapping("plant_modifiers", self.plant_modifiers)
        )
        _digest("installation_profile_digest", self.installation_profile_digest)


@dataclass(frozen=True)
class AnimationRuntimeContext:
    wall_time: float
    unscaled_elapsed: float
    scaled_elapsed: float
    frame_index: int
    scene_epoch: int
    global_width: int
    height: int
    local_strip_offset: int
    local_width: int
    vibe_id: str
    vibe_profile_version: int
    palette_roles: Mapping[str, Any]
    capability_values: Mapping[str, Any]
    installation_profile_view: Mapping[str, Any]
    plant_modifiers: Mapping[str, Any]
    schema_version: int = ANIMATION_RUNTIME_CONTEXT_VERSION
    schema: str = ANIMATION_RUNTIME_CONTEXT_SCHEMA

    def __post_init__(self) -> None:
        _schema_version(
            "animation runtime context",
            self.schema,
            ANIMATION_RUNTIME_CONTEXT_SCHEMA,
            self.schema_version,
        )
        _finite_number("wall_time", self.wall_time, minimum=0.0)
        _finite_number("unscaled_elapsed", self.unscaled_elapsed, minimum=0.0)
        _finite_number("scaled_elapsed", self.scaled_elapsed, minimum=0.0)
        _uint64("frame_index", self.frame_index)
        _uint64("scene_epoch", self.scene_epoch)
        _bounded_int("global_width", self.global_width, 1, 2**16 - 1)
        _bounded_int("height", self.height, 1, 2**16 - 1)
        _bounded_int("local_strip_offset", self.local_strip_offset, 0, self.global_width - 1)
        _bounded_int("local_width", self.local_width, 1, self.global_width)
        if self.local_strip_offset + self.local_width > self.global_width:
            raise ValueError("local strip range must fit within global_width")
        _identifier("vibe_id", self.vibe_id)
        _bounded_int("vibe_profile_version", self.vibe_profile_version, 1, 2**31 - 1)
        for name in (
            "palette_roles",
            "capability_values",
            "installation_profile_view",
            "plant_modifiers",
        ):
            object.__setattr__(self, name, _immutable_mapping(name, getattr(self, name)))
        _validate_palette_roles(self.palette_roles)

    @property
    def next_deadline_clock(self) -> float:
        """Return the absolute clock on which ``next_deadline_scene_time`` is based."""

        return float(self.unscaled_elapsed)


@dataclass(frozen=True)
class BaseFrame:
    pixels: np.ndarray
    changed: bool = True
    dirty_ranges: Optional[Tuple[Tuple[int, int], ...]] = None
    contract_version: int = FRAME_CONTRACT_VERSION
    schema: str = FRAME_CONTRACT_SCHEMA

    def __post_init__(self) -> None:
        _frame_common(self, channels=3)


@dataclass(frozen=True)
class OverlayFrame:
    pixels: np.ndarray
    revision: int
    changed: bool = True
    dirty_ranges: Optional[Tuple[Tuple[int, int], ...]] = None
    contract_version: int = FRAME_CONTRACT_VERSION
    schema: str = FRAME_CONTRACT_SCHEMA

    def __post_init__(self) -> None:
        _frame_common(self, channels=4)
        _uint64("revision", self.revision)
        alpha = self.pixels[:, 3:4]
        invalid = np.argwhere(self.pixels[:, :3] > alpha)
        if invalid.size:
            pixel, channel = (int(value) for value in invalid[0])
            raise ValueError(
                "OverlayFrame pixels must be premultiplied RGBA8; "
                f"pixel {pixel} channel {channel} exceeds alpha {int(alpha[pixel, 0])}"
            )


def _frame_common(frame: Any, *, channels: int) -> None:
    if frame.schema != FRAME_CONTRACT_SCHEMA:
        raise ValueError(
            f"frame schema must be {FRAME_CONTRACT_SCHEMA!r}, got {frame.schema!r}"
        )
    if frame.contract_version != FRAME_CONTRACT_VERSION:
        raise ValueError(
            f"frame contract_version must be {FRAME_CONTRACT_VERSION}, got {frame.contract_version}"
        )
    if not isinstance(frame.changed, bool):
        raise TypeError("changed must be a bool")
    pixels = frame.pixels
    if not isinstance(pixels, np.ndarray):
        raise TypeError("pixels must be a numpy.ndarray")
    if pixels.dtype != np.uint8 or pixels.ndim != 2 or pixels.shape[1:] != (channels,):
        raise ValueError(
            f"pixels must have dtype uint8 and shape (total_leds, {channels}); "
            f"got dtype={pixels.dtype}, shape={pixels.shape}"
        )
    if pixels.shape[0] <= 0:
        raise ValueError("pixels must contain at least one LED")
    if not pixels.flags.c_contiguous:
        raise ValueError("pixels must be C-contiguous")
    dirty_ranges = normalize_optional_dirty_ranges(frame.dirty_ranges, pixels.shape[0])
    if not frame.changed and dirty_ranges:
        raise ValueError("changed=False cannot carry non-empty dirty_ranges")
    object.__setattr__(frame, "dirty_ranges", dirty_ranges)


def _schema_version(label: str, schema: str, expected_schema: str, version: int) -> None:
    if schema != expected_schema:
        raise ValueError(f"{label} schema must be {expected_schema!r}, got {schema!r}")
    _bounded_int(f"{label} version", version, 1, 1)


def _identifier(name: str, value: Any) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(
            f"{name} must start with a lowercase letter and contain only lowercase "
            "letters, digits, underscores, dots, or hyphens"
        )


def _identifier_tuple(name: str, value: Any) -> Tuple[str, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple")
    for item in value:
        _identifier(name, item)
    if len(value) != len(set(value)):
        raise ValueError(f"{name} must not contain duplicates")
    return value


def _nonempty_string(name: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _bounded_int(name: str, value: Any, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be from {minimum} to {maximum}, got {value}")
    return value


def _uint64(name: str, value: Any) -> int:
    return _bounded_int(name, value, 0, 2**64 - 1)


def _finite_number(
    name: str, value: Any, *, minimum: Optional[float] = None, maximum: Optional[float] = None
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise TypeError(f"{name} must be a finite number")
    result = float(value)
    if minimum is not None and result < minimum:
        raise ValueError(f"{name} must be at least {minimum}, got {result}")
    if maximum is not None and result > maximum:
        raise ValueError(f"{name} must be at most {maximum}, got {result}")
    return result


def _digest(name: str, value: Any) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase 64-character SHA-256 hex digest")
    return value


def _enum_value(name: str, value: Any, enum_type: type[Enum]) -> Any:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        supported = ", ".join(repr(item.value) for item in enum_type)
        raise ValueError(f"{name} must be one of {supported}, got {value!r}") from exc


def _immutable_mapping(name: str, value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return MappingProxyType({
        key: _immutable_json(f"{name}.{key}", item)
        for key, item in value.items()
        if _mapping_key(name, key)
    })


def _mapping_key(name: str, key: Any) -> bool:
    if not isinstance(key, str):
        raise TypeError(f"{name} keys must be strings, got {type(key).__name__}")
    return True


def _immutable_json(name: str, value: Any) -> Any:
    if isinstance(value, Mapping):
        return _immutable_mapping(name, value)
    if isinstance(value, (list, tuple)):
        return tuple(_immutable_json(f"{name}[]", item) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise TypeError(f"{name} must contain only finite JSON-compatible values")


def _validate_palette_roles(palette_roles: Mapping[str, Any]) -> None:
    for role, color in palette_roles.items():
        _identifier("palette role", role)
        if not isinstance(color, tuple) or len(color) != 3:
            raise ValueError(f"palette_roles[{role!r}] must be an RGB triplet")
        for channel in color:
            _bounded_int(f"palette_roles[{role!r}] channel", channel, 0, 255)
