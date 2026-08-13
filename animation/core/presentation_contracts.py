"""Version 1 contracts for the revamped animation pipeline.

Phase 1 froze these validation and serialization boundaries.  Phase 2A now
activates the vibe/profile and runtime-context subset in the existing Python
manager while the scene, overlay, and receiver-native contracts remain dormant.
"""

from __future__ import annotations

import math
import re
import hashlib
import json
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
AGGREGATE_OVERLAY_SLOT_ID = "clock_overlay"
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

VIBE_CAPABILITIES = frozenset(("palette_roles", "tempo", "luminance"))
VIBE_COLOR_POLICIES = frozenset(("semantic", "grade", "preserve"))
VIBE_PALETTE_ROLES = (
    "background_low",
    "background_mid",
    "background_high",
    "primary",
    "secondary",
    "accent",
    "hud",
    "warning",
)
CANONICAL_VIBE_IDS = ("neutral", "quiet", "cozy", "vivid", "celebration")


def component_preset_fingerprint(
    plugin_id: str, preset_id: str, parameters: Mapping[str, Any]
) -> str:
    """Canonical cross-layer identity for one selected component preset snapshot."""
    _identifier("plugin_id", plugin_id)
    _identifier("preset_id", preset_id)
    if not isinstance(parameters, Mapping):
        raise TypeError("component preset parameters must be a mapping")
    payload = {
        "plugin_id": plugin_id,
        "preset_id": preset_id,
        "parameters": _mutable_json(parameters),
    }
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()).hexdigest()


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
        reserved = {"plant_aware", "plant_modifiers", "vibe", "output"}
        leaked = sorted(reserved & (
            set(self.parameter_overrides) | set(self.resolved_parameters)
        ))
        if leaked:
            raise ValueError(
                "component references must not capture scene-external state: "
                + ", ".join(leaked)
            )
        if self.provider is ComponentProvider.RECEIVER_NATIVE:
            _digest("bundle_digest", self.bundle_digest)
            _digest("expected_payload_digest", self.expected_payload_digest)
        elif self.bundle_digest is not None or self.expected_payload_digest is not None:
            raise ValueError("Python component references must not carry native payload digests")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "plugin_id": self.plugin_id,
            "provider": self.provider.value,
            "parameter_overrides": _mutable_json(self.parameter_overrides),
            "resolved_parameters": _mutable_json(self.resolved_parameters),
        }
        for name in (
            "preset_id", "preset_fingerprint", "bundle_digest",
            "expected_payload_digest",
        ):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        return payload

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ComponentRef":
        data = _strict_payload(
            "component reference",
            payload,
            required=("plugin_id", "provider"),
            optional=(
                "preset_id", "preset_fingerprint", "parameter_overrides",
                "resolved_parameters", "bundle_digest", "expected_payload_digest",
            ),
        )
        return cls(
            plugin_id=data["plugin_id"],
            provider=data["provider"],
            preset_id=data.get("preset_id"),
            preset_fingerprint=data.get("preset_fingerprint"),
            parameter_overrides=data.get("parameter_overrides", {}),
            resolved_parameters=data.get("resolved_parameters", {}),
            bundle_digest=data.get("bundle_digest"),
            expected_payload_digest=data.get("expected_payload_digest"),
        )


@dataclass(frozen=True)
class OverlayPlacement:
    strip_translation: int = 0
    led_translation: int = 0
    clip_policy: ClipPolicy = ClipPolicy.CLIP_TO_WALL

    def __post_init__(self) -> None:
        for name in ("strip_translation", "led_translation"):
            _bounded_int(name, getattr(self, name), -(2**31), 2**31 - 1)
        object.__setattr__(self, "clip_policy", _enum_value("clip_policy", self.clip_policy, ClipPolicy))

    def to_dict(self) -> dict[str, Any]:
        return {
            "strip_translation": self.strip_translation,
            "led_translation": self.led_translation,
            "clip_policy": self.clip_policy.value,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "OverlayPlacement":
        data = _strict_payload(
            "overlay placement",
            payload,
            optional=("strip_translation", "led_translation", "clip_policy"),
        )
        return cls(
            strip_translation=data.get("strip_translation", 0),
            led_translation=data.get("led_translation", 0),
            clip_policy=data.get("clip_policy", ClipPolicy.CLIP_TO_WALL),
        )


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

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"policy": self.policy.value}
        if self.lease_ms is not None:
            payload["lease_ms"] = self.lease_ms
        return payload

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "StalePolicy":
        data = _strict_payload(
            "stale policy", payload, required=("policy",), optional=("lease_ms",)
        )
        return cls(policy=data["policy"], lease_ms=data.get("lease_ms"))


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "component": self.component.to_dict(),
            "enabled": self.enabled,
            "opacity": self.opacity,
            "placement": self.placement.to_dict(),
            "stale_policy": self.stale_policy.to_dict(),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "OverlayRef":
        data = _strict_payload(
            "overlay reference",
            payload,
            required=(
                "slot_id", "component", "enabled", "opacity", "placement",
                "stale_policy",
            ),
        )
        return cls(
            slot_id=data["slot_id"],
            component=ComponentRef.from_payload(data["component"]),
            enabled=data["enabled"],
            opacity=data["opacity"],
            placement=OverlayPlacement.from_payload(data["placement"]),
            stale_policy=StalePolicy.from_payload(data["stale_policy"]),
        )


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

    def to_dict(self) -> dict[str, Any]:
        """Return the complete scene-only persistence envelope.

        Vibe, plant geometry, and output controls intentionally have no fields in
        this schema and therefore cannot leak into scene presets or snapshots.
        """
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "revision": self.revision,
            "background": self.background.to_dict(),
            "overlays": [overlay.to_dict() for overlay in self.overlays],
            "known_python_fallback": self.known_python_fallback.to_dict(),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "SceneState":
        data = _strict_payload(
            "scene state",
            payload,
            required=(
                "schema", "schema_version", "revision", "background", "overlays",
                "known_python_fallback",
            ),
        )
        overlays = data["overlays"]
        if not isinstance(overlays, (list, tuple)):
            raise TypeError("scene state overlays must be an array")
        return cls(
            revision=data["revision"],
            background=ComponentRef.from_payload(data["background"]),
            overlays=tuple(OverlayRef.from_payload(item) for item in overlays),
            known_python_fallback=ComponentRef.from_payload(
                data["known_python_fallback"]
            ),
            schema_version=data["schema_version"],
            schema=data["schema"],
        )


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
        if self.vibe_id not in CANONICAL_VIBE_IDS:
            raise ValueError(
                f"unknown vibe ID {self.vibe_id!r}; expected one of "
                f"{', '.join(CANONICAL_VIBE_IDS)}"
            )
        _bounded_int("profile_version", self.profile_version, 1, 2**31 - 1)
        _digest("resolved_profile_digest", self.resolved_profile_digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "revision": self.revision,
            "vibe_id": self.vibe_id,
            "profile_version": self.profile_version,
            "resolved_profile_digest": self.resolved_profile_digest,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "VibeState":
        if not isinstance(payload, Mapping):
            raise TypeError("vibe state must be a mapping")
        known = {
            "schema", "schema_version", "revision", "vibe_id", "id",
            "profile_version", "resolved_profile_digest",
        }
        unknown = set(payload) - known
        if unknown:
            raise ValueError(f"unknown vibe state fields: {', '.join(sorted(unknown))}")
        vibe_id = payload.get("vibe_id", payload.get("id"))
        return cls(
            revision=payload.get("revision", 0),
            vibe_id=vibe_id,
            profile_version=payload.get("profile_version", VIBE_PROFILE_VERSION),
            resolved_profile_digest=payload.get("resolved_profile_digest"),
            schema_version=payload.get("schema_version", VIBE_STATE_VERSION),
            schema=payload.get("schema", VIBE_STATE_SCHEMA),
        )


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "vibe_id": self.vibe_id,
            "profile_version": self.profile_version,
            "palette_roles": _mutable_json(self.palette_roles),
            "tempo_scale": self.tempo_scale,
            "luminance_scale": self.luminance_scale,
            "capability_values": _mutable_json(self.capability_values),
        }

    @property
    def resolved_profile_digest(self) -> str:
        payload = json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def to_state(self, *, revision: int = 0) -> VibeState:
        return VibeState(
            revision=revision,
            vibe_id=self.vibe_id,
            profile_version=self.profile_version,
            resolved_profile_digest=self.resolved_profile_digest,
        )


@dataclass(frozen=True)
class ResolvedVibe:
    profile: VibeProfile
    state: VibeState

    def __post_init__(self) -> None:
        if self.state.vibe_id != self.profile.vibe_id:
            raise ValueError("resolved vibe state/profile IDs must match")
        if self.state.profile_version != self.profile.profile_version:
            raise ValueError("resolved vibe state/profile versions must match")
        if self.state.resolved_profile_digest != self.profile.resolved_profile_digest:
            raise ValueError("resolved vibe digest does not match the profile")


def _profile(
    vibe_id: str,
    tempo: float,
    luminance: float,
    colors: tuple[tuple[int, int, int], ...],
    *,
    chroma: float,
    energy: float,
) -> VibeProfile:
    return VibeProfile(
        vibe_id=vibe_id,
        profile_version=VIBE_PROFILE_VERSION,
        palette_roles=dict(zip(VIBE_PALETTE_ROLES, colors)),
        tempo_scale=tempo,
        luminance_scale=luminance,
        capability_values={"chroma_scale": chroma, "energy": energy},
    )


def get_vibe_profile(vibe_id: str, profile_version: Optional[int] = None) -> VibeProfile:
    """Return one canonical profile, rejecting unknown IDs and versions."""
    if not isinstance(vibe_id, str) or vibe_id not in VIBE_PROFILE_REGISTRY:
        raise ValueError(
            f"unknown vibe ID {vibe_id!r}; expected one of {', '.join(CANONICAL_VIBE_IDS)}"
        )
    profile = VIBE_PROFILE_REGISTRY[vibe_id]
    if profile_version is not None and profile_version != profile.profile_version:
        raise ValueError(
            f"unsupported profile version {profile_version!r} for vibe {vibe_id!r}; "
            f"expected {profile.profile_version}"
        )
    return profile


def list_vibe_profiles() -> tuple[VibeProfile, ...]:
    return tuple(VIBE_PROFILE_REGISTRY.values())


def resolve_vibe(
    vibe_id: str = "neutral",
    *,
    revision: int = 0,
    profile_version: Optional[int] = None,
) -> ResolvedVibe:
    profile = get_vibe_profile(vibe_id, profile_version)
    return ResolvedVibe(profile=profile, state=profile.to_state(revision=revision))


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
    resolved_profile_digest: Optional[str] = None
    tempo_scale: float = 1.0
    luminance_scale: float = 1.0
    operator_tempo_scale: float = 1.0
    authored_speed: float = 1.0
    effective_time_scale: float = 1.0
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
        if self.resolved_profile_digest is not None:
            _digest("resolved_profile_digest", self.resolved_profile_digest)
        for name in (
            "tempo_scale", "operator_tempo_scale", "authored_speed", "effective_time_scale"
        ):
            _finite_number(name, getattr(self, name), minimum=0.01, maximum=10000.0)
        _finite_number("luminance_scale", self.luminance_scale, minimum=0.0, maximum=1.0)
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

    @property
    def presentation_identity(self) -> tuple[Any, ...]:
        """Inputs whose change may invalidate presentation caches.

        Frame clocks and indices are intentionally absent: advancing a frame is
        not a presentation-context change event.
        """
        return (
            self.vibe_id,
            self.vibe_profile_version,
            self.resolved_profile_digest,
            self.tempo_scale,
            self.luminance_scale,
            self.operator_tempo_scale,
            self.authored_speed,
            tuple(self.palette_roles.items()),
            tuple(self.capability_values.items()),
            tuple(self.installation_profile_view.items()),
            tuple(self.plant_modifiers.items()),
        )


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


def _strict_payload(
    label: str,
    payload: Mapping[str, Any],
    *,
    required: tuple[str, ...] = (),
    optional: tuple[str, ...] = (),
) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise TypeError(f"{label} must be a mapping")
    known = set(required) | set(optional)
    unknown = sorted(set(payload) - known)
    if unknown:
        raise ValueError(f"unknown {label} fields: {', '.join(unknown)}")
    missing = [name for name in required if name not in payload]
    if missing:
        raise ValueError(f"missing {label} fields: {', '.join(missing)}")
    return payload


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


def _mutable_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _mutable_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_mutable_json(item) for item in value]
    return value


def _validate_palette_roles(palette_roles: Mapping[str, Any]) -> None:
    for role, color in palette_roles.items():
        _identifier("palette role", role)
        if not isinstance(color, tuple) or len(color) != 3:
            raise ValueError(f"palette_roles[{role!r}] must be an RGB triplet")
        for channel in color:
            _bounded_int(f"palette_roles[{role!r}] channel", channel, 0, 255)


# Registry construction intentionally follows every validator used by
# VibeProfile.__post_init__; module import must be deterministic and side-effect free.
_VIBE_PROFILES = (
    _profile("neutral", 1.0, 1.0, (
        (8, 10, 16), (32, 38, 52), (92, 104, 128), (224, 228, 236),
        (152, 164, 184), (255, 184, 72), (240, 244, 252), (255, 72, 64),
    ), chroma=1.0, energy=0.5),
    _profile("quiet", 0.65, 0.55, (
        (5, 9, 14), (17, 30, 40), (47, 72, 82), (126, 166, 170),
        (91, 123, 132), (151, 142, 116), (178, 202, 201), (192, 108, 96),
    ), chroma=0.62, energy=0.18),
    _profile("cozy", 0.85, 0.75, (
        (16, 7, 5), (53, 24, 16), (112, 59, 32), (244, 164, 86),
        (194, 94, 58), (255, 205, 108), (255, 228, 174), (238, 77, 54),
    ), chroma=0.86, energy=0.4),
    _profile("vivid", 1.15, 0.95, (
        (4, 5, 20), (18, 23, 76), (45, 66, 154), (70, 225, 255),
        (191, 72, 255), (255, 224, 48), (232, 250, 255), (255, 52, 91),
    ), chroma=1.2, energy=0.78),
    _profile("celebration", 1.35, 1.0, (
        (12, 3, 20), (53, 15, 80), (112, 32, 157), (255, 78, 174),
        (44, 224, 242), (255, 211, 35), (255, 250, 226), (255, 53, 60),
    ), chroma=1.35, energy=1.0),
)
VIBE_PROFILE_REGISTRY: Mapping[str, VibeProfile] = MappingProxyType({
    profile.vibe_id: profile for profile in _VIBE_PROFILES
})
if tuple(VIBE_PROFILE_REGISTRY) != CANONICAL_VIBE_IDS:
    raise RuntimeError("canonical vibe registry order does not match the wire vocabulary")
