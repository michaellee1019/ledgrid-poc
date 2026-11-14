"""Capability catalog for the current-only Scene v2 contract.

Provider identity is part of a component's integrity address. It is not a
discovery category: callers select a component for a Scene role and this
catalog verifies its declared provider, role, rendering, and fidelity facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import math
from types import MappingProxyType
from typing import Any, Callable, Mapping

from animation.core.plant_awareness import INSTALLATION_GEOMETRY_CONTACT_INPUT


def _freeze_default(value: Any) -> Any:
    """Make catalog defaults recursively immutable and detached from callers."""

    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_default(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_default(item) for item in value)
    return value


def _thaw_default(value: Any) -> Any:
    """Return an isolated JSON-ready copy for component normalization."""

    if isinstance(value, Mapping):
        return {key: _thaw_default(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_default(item) for item in value]
    return value


class ComponentProvider(str, Enum):
    PYTHON = "python"
    RECEIVER_NATIVE = "receiver_native"


class ComponentRole(str, Enum):
    BACKGROUND = "background"
    ANIMATION = "animation"
    WIDGET = "widget"


class PalettePolicy(str, Enum):
    SEMANTIC = "semantic"
    PRESERVE = "preserve"


class TimingPolicy(str, Enum):
    SCALED_CONTEXT = "scaled_context"
    WALL_CLOCK = "wall_clock"


class AlphaBehavior(str, Enum):
    """How a component's visual output participates in composition."""

    NONE = "none"
    PREMULTIPLIED_RGBA = "premultiplied_rgba"
    OPAQUE = "opaque"


class PlantCapability(str, Enum):
    """Named plant-aware behaviours supported by a component."""

    NONE = "none"
    EFFECT_INTENT = "effect_intent"
    SIMULATION_INPUTS = "simulation_inputs"
    FINAL_OPTICS = "final_optics"


@dataclass(frozen=True)
class ComponentAction:
    """One semantic action and its optional physical controller binding."""

    label: str
    button: str | None = None
    release: bool = False
    repeat: bool = False
    position: bool = False
    chord: int | None = None

    def __post_init__(self) -> None:
        if not self.label or (self.chord is not None and (
            not self.position or self.button is None or not 1 <= self.chord < 16
        )):
            raise ValueError("Invalid component action declaration")

    def public(self) -> dict[str, Any]:
        return {
            "label": self.label, "button": self.button, "release": self.release,
            "repeat": self.repeat, "position": self.position, "chord": self.chord,
        }


@dataclass(frozen=True)
class ComponentInput:
    """The animation's input ownership and available semantic actions."""

    mode: str = "none"
    actions: Mapping[str, ComponentAction] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mode not in {"none", "actions", "player"} or (self.mode == "none") == bool(self.actions):
            raise ValueError("Invalid component input declaration")
        object.__setattr__(self, "actions", MappingProxyType(dict(self.actions)))

    def public(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "actions": {name: action.public() for name, action in self.actions.items()},
        }


@dataclass(frozen=True)
class ComponentDescriptor:
    """A fully declared capability record for one catalog component."""

    component_id: str
    version: int
    provider: ComponentProvider | str
    role: ComponentRole | str
    timing_policy: TimingPolicy | str
    alpha_behavior: AlphaBehavior | str
    palette_policy: PalettePolicy | str
    plant_capabilities: tuple[PlantCapability | str, ...]
    fidelity_exceptions: tuple[str, ...]
    intensity_parameter: str | None = None
    optional_simulation_inputs: tuple[str, ...] = ()
    required_simulation_inputs: tuple[str, ...] = ()
    defaults: Mapping[str, Any] = field(default_factory=dict)
    parameter_normalizer: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = field(
        default=None, compare=False, repr=False
    )

    name: str = ""
    description: str = ""
    package_id: str = ""
    parameter_schema: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    preferred_parameters: tuple[str, ...] = ()
    input_capability: ComponentInput = field(default_factory=ComponentInput)
    default_placement: Mapping[str, Any] = field(default_factory=dict)
    live_parameters: frozenset[str] = frozenset()
    factory: Callable[[Any, Mapping[str, Any]], Any] | None = field(
        default=None, compare=False, repr=False
    )
    # Older descriptors without a canonical normalizer kept the author's JSON
    # number encodings. Keep that identity while applying the same validator.
    preserve_parameter_encoding: bool = False

    def __post_init__(self) -> None:
        if not self.component_id or not isinstance(self.component_id, str):
            raise ValueError("component_id must be a non-empty string")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("component version must be a positive integer")
        object.__setattr__(self, "provider", ComponentProvider(self.provider))
        object.__setattr__(self, "role", ComponentRole(self.role))
        object.__setattr__(self, "timing_policy", TimingPolicy(self.timing_policy))
        object.__setattr__(self, "alpha_behavior", AlphaBehavior(self.alpha_behavior))
        object.__setattr__(self, "palette_policy", PalettePolicy(self.palette_policy))
        capabilities = tuple(PlantCapability(item) for item in self.plant_capabilities)
        if not capabilities or len(capabilities) != len(set(capabilities)):
            raise ValueError("plant_capabilities must be a non-empty unique declaration")
        if PlantCapability.NONE in capabilities and len(capabilities) != 1:
            raise ValueError("plant capability none cannot be combined with another capability")
        object.__setattr__(self, "plant_capabilities", capabilities)
        exceptions = tuple(self.fidelity_exceptions)
        if any(not isinstance(item, str) or not item for item in exceptions) or len(exceptions) != len(set(exceptions)):
            raise ValueError("fidelity_exceptions must be unique non-empty names")
        if self.palette_policy is PalettePolicy.PRESERVE and not exceptions:
            raise ValueError("preserve palette policy requires a fidelity exception")
        if self.palette_policy is PalettePolicy.SEMANTIC and exceptions:
            raise ValueError("semantic palette policy cannot have fidelity exceptions")
        object.__setattr__(self, "fidelity_exceptions", exceptions)
        if self.intensity_parameter is not None and (
            not isinstance(self.intensity_parameter, str)
            or not self.intensity_parameter.isidentifier()
            or self.intensity_parameter in {"brightness", "intensity", "speed", "rate"}
        ):
            raise ValueError("intensity_parameter must be a named component-local control")
        optional_inputs = tuple(self.optional_simulation_inputs)
        required_inputs = tuple(self.required_simulation_inputs)
        if any(not isinstance(item, str) or not item for item in optional_inputs + required_inputs):
            raise ValueError("simulation inputs must be non-empty names")
        if len(optional_inputs) != len(set(optional_inputs)) or len(required_inputs) != len(set(required_inputs)):
            raise ValueError("simulation inputs must be unique names")
        if set(optional_inputs) & set(required_inputs):
            raise ValueError("simulation inputs cannot be both optional and required")
        if (optional_inputs or required_inputs) and PlantCapability.SIMULATION_INPUTS not in capabilities:
            raise ValueError("simulation inputs require the simulation_inputs plant capability")
        if INSTALLATION_GEOMETRY_CONTACT_INPUT in required_inputs:
            raise ValueError("installation geometry contact must remain an optional runtime input")
        if INSTALLATION_GEOMETRY_CONTACT_INPUT in optional_inputs and (
            self.provider is not ComponentProvider.PYTHON
            or self.role is not ComponentRole.ANIMATION
        ):
            raise ValueError(
                "installation geometry contact is limited to Python Animation providers"
            )
        object.__setattr__(self, "optional_simulation_inputs", optional_inputs)
        object.__setattr__(self, "required_simulation_inputs", required_inputs)
        if not isinstance(self.defaults, Mapping):
            raise ValueError("defaults must be a mapping")
        object.__setattr__(self, "defaults", _freeze_default(self.defaults))
        object.__setattr__(self, "parameter_schema", _freeze_default(self.parameter_schema))
        object.__setattr__(self, "default_placement", _freeze_default(self.default_placement))
        if self.parameter_normalizer is not None and not callable(self.parameter_normalizer):
            raise ValueError("parameter_normalizer must be callable")
        self._validate_role_shape()

    def default_parameters(self) -> dict[str, Any]:
        """Return a mutable JSON-ready copy without exposing catalog state."""

        return _thaw_default(self.defaults)

    def controls(self) -> dict[str, Any]:
        """Serialize the static controls for an editor or catalog response."""
        return _thaw_default(self.parameter_schema)

    def normalize_parameters(self, values: Mapping[str, Any], *, authored_preset: bool = False) -> dict[str, Any]:
        """Validate complete component values and return JSON-ready parameters."""
        from animation.component_parameters import validate_component_parameters

        # Authored packages can carry historical preset keys that their own
        # normalizer checks and strips before entering a current Scene.
        authored = values if authored_preset else validate_component_parameters(
            values, intensity_parameter=self.intensity_parameter,
        )
        merged = {**self.default_parameters(), **authored}
        if self.parameter_normalizer is None:
            return validate_component_parameters(merged, intensity_parameter=self.intensity_parameter)
        validated = self.parameter_normalizer(merged)
        return validate_component_parameters(
            merged if self.preserve_parameter_encoding and not authored_preset else _thaw_default(validated),
            intensity_parameter=self.intensity_parameter,
        )

    @property
    def accepts_installation_geometry_contact(self) -> bool:
        """Whether this exact provider explicitly accepts installed geometry."""

        return INSTALLATION_GEOMETRY_CONTACT_INPUT in self.optional_simulation_inputs

    def validate_scene_v2(self) -> None:
        """Retain a named validation hook for scene-boundary callers."""

        self._validate_role_shape()

    def _validate_role_shape(self) -> None:
        if self.role is ComponentRole.BACKGROUND:
            if self.provider is not ComponentProvider.PYTHON or self.alpha_behavior is not AlphaBehavior.NONE:
                raise ValueError("Scene v2 Background must be Python with alpha_behavior none")
        elif self.role is ComponentRole.ANIMATION:
            if self.provider is not ComponentProvider.PYTHON:
                raise ValueError("Scene v2 Animation must be provided by Python")
            if self.alpha_behavior not in {AlphaBehavior.PREMULTIPLIED_RGBA, AlphaBehavior.OPAQUE}:
                raise ValueError("Scene v2 Animation must declare premultiplied_rgba or opaque alpha behavior")
        elif self.role is ComponentRole.WIDGET:
            if self.provider is not ComponentProvider.PYTHON or self.alpha_behavior is not AlphaBehavior.PREMULTIPLIED_RGBA:
                raise ValueError("Scene v2 Widget must be Python premultiplied_rgba")


def component_definition(
    package_id: str, implementation: type, descriptor: ComponentDescriptor,
) -> ComponentDescriptor:
    """Bind a package's static renderer, controls and input to its Scene identity."""
    if descriptor.factory is not None:
        return replace(descriptor, package_id=package_id)
    normalizer = implementation._normalized_parameters
    schema = implementation.get_parameter_schema()
    defaults = descriptor.default_parameters()
    preferred = getattr(implementation, "PREFERRED_PARAMETERS", ())
    live = getattr(implementation, "LIVE_TUNABLE_PARAMETERS", frozenset())
    annotations = getattr(implementation, "PARAMETER_CONTROLS", {})
    controls = {}
    for name in defaults:
        definition = {**schema[name], **annotations.get(name, {}), "default": defaults[name]}
        definition.setdefault("label", name.replace("_", " "))
        definition.setdefault("mutation", "live" if name in live else "rebuild")
        if "min" in definition and "max" in definition and not definition.get("options"):
            span = definition.get("ui_max", definition["max"]) - definition.get("ui_min", definition["min"])
            integer = definition["type"] == "int"
            definition.setdefault("step", 1 if integer or span > 30 else .01 if span <= 2 else .1)
            definition.setdefault("coarse_step", max(1, math.ceil(span / 10)) if integer else span / 10)
        controls[name] = definition
    point_bindings = getattr(implementation, "NIMBUS_POINT_BINDINGS", {})
    point_labels = getattr(implementation, "POINT_ACTION_LABELS", {})
    actions = {}
    for name in sorted(implementation.INTERACTION_TYPES):
        button, chord = point_bindings.get(name, (None, None))
        actions[name] = ComponentAction(point_labels.get(name, name.capitalize()), button, position=True, chord=chord)
    player_buttons = getattr(implementation, "PLAYER_BUTTONS", {})
    for button, (label, repeat, *release) in player_buttons.items():
        actions[button] = ComponentAction(label, button, repeat=repeat, release=any(release))
    mode = "player" if player_buttons else "actions" if actions else "none"
    return replace(
        descriptor, package_id=package_id, factory=implementation,
        name=getattr(implementation, "COMPOSER_NAME", implementation.ANIMATION_NAME),
        description=implementation.ANIMATION_DESCRIPTION,
        parameter_schema=controls, preferred_parameters=preferred, live_parameters=live,
        parameter_normalizer=descriptor.parameter_normalizer or normalizer,
        preserve_parameter_encoding=descriptor.parameter_normalizer is None,
        input_capability=ComponentInput(mode, actions),
        default_placement=getattr(implementation, "DEFAULT_WIDGET_PLACEMENT", {}),
    )


class ComponentCatalog:
    """Provider-qualified immutable component lookup."""

    def __init__(self, descriptors: tuple[ComponentDescriptor, ...] | list[ComponentDescriptor]):
        qualified = tuple(descriptors)
        indexed: dict[tuple[str, str], ComponentDescriptor] = {}
        for descriptor in qualified:
            key = (descriptor.provider.value, descriptor.component_id)
            if key in indexed:
                raise ValueError(f"duplicate component descriptor {key!r}")
            indexed[key] = descriptor
        self._qualified_descriptors = qualified
        self._descriptors = MappingProxyType(indexed)

    @property
    def descriptors(self) -> tuple[ComponentDescriptor, ...]:
        """The finite, provider-qualified packet exposed to Scene v2 callers.

        Provider remains part of the integrity address used by :meth:`require`;
        this ordered packet is intentionally not a provider discovery surface.
        """

        return self._qualified_descriptors

    def require(self, *, provider: str, component_id: str, version: int) -> ComponentDescriptor:
        try:
            descriptor = self._descriptors[(ComponentProvider(provider).value, component_id)]
        except (KeyError, ValueError) as exc:
            raise ValueError("scene component is not a qualified catalog component") from exc
        if descriptor.version != version:
            raise ValueError("scene component version does not match the catalog")
        return descriptor


_LEGACY_CATALOG_EXPORTS = frozenset({
    "bind_python_implementation",
    "color_policy_inventory",
    "filter_catalog",
    "normalize_cadence",
    "scanned_descriptor",
    "validate_parameter_overrides",
})


def validate_and_normalize_manifest(
    payload: dict[str, Any], manifest_path: Any, plugin_id: str
) -> dict[str, Any]:
    """Adapt current Scene v2 metadata for the mature Python plugin loader."""

    from animation.core.legacy_component_catalog import (
        validate_and_normalize_manifest as validate_legacy_manifest,
    )

    adapted = dict(payload)
    if (
        "component" in adapted
        or (
            adapted.get("provider") == ComponentProvider.PYTHON.value
            and "entrypoint" not in adapted
        )
    ):
        for key in (
            "component",
            "provider",
            "role",
            "entrypoint",
            "cadence",
            "manifest_version",
            "frame_format",
            "timing",
            "alpha_behavior",
            "palette_policy",
            "palette_roles",
            "plant_capabilities",
            "fidelity_exceptions",
            "capabilities",
        ):
            adapted.pop(key, None)
    return validate_legacy_manifest(adapted, manifest_path, plugin_id)


def __getattr__(name: str) -> Any:
    """Load mature manifest helpers only when legacy callers request them."""

    if name in _LEGACY_CATALOG_EXPORTS:
        from animation.core import legacy_component_catalog

        return getattr(legacy_component_catalog, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
