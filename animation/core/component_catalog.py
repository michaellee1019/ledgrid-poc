"""A narrow in-memory catalog for the one-background Scene v1 packet."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


class ComponentProvider(str, Enum):
    PYTHON = "python"
    RECEIVER_NATIVE = "receiver_native"


class ComponentRole(str, Enum):
    BACKGROUND = "background"
    OVERLAY = "overlay"


class PalettePolicy(str, Enum):
    SEMANTIC = "semantic"
    PRESERVE = "preserve"


class TimingPolicy(str, Enum):
    SCALED_CONTEXT = "scaled_context"
    WALL_CLOCK = "wall_clock"


@dataclass(frozen=True)
class ComponentDescriptor:
    """The capability declaration consumed by the Scene-v1 resolver."""

    component_id: str
    version: int
    provider: ComponentProvider | str = ComponentProvider.PYTHON
    role: ComponentRole | str = ComponentRole.BACKGROUND
    palette_policy: PalettePolicy | str = PalettePolicy.SEMANTIC
    timing_policy: TimingPolicy | str = TimingPolicy.SCALED_CONTEXT
    intensity_parameter: str | None = None
    preserve_reason: str | None = None
    optional_simulation_inputs: tuple[str, ...] = ()
    required_simulation_inputs: tuple[str, ...] = ()
    defaults: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.component_id or not isinstance(self.component_id, str):
            raise ValueError("component_id must be a non-empty string")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("component version must be a positive integer")
        object.__setattr__(self, "provider", ComponentProvider(self.provider))
        object.__setattr__(self, "role", ComponentRole(self.role))
        object.__setattr__(self, "palette_policy", PalettePolicy(self.palette_policy))
        object.__setattr__(self, "timing_policy", TimingPolicy(self.timing_policy))
        if self.palette_policy is PalettePolicy.PRESERVE and not self.preserve_reason:
            raise ValueError("preserve palette policy requires a fidelity reason")
        if self.palette_policy is PalettePolicy.SEMANTIC and self.preserve_reason:
            raise ValueError("semantic palette policy cannot have a preserve reason")
        if self.intensity_parameter is not None and (
            not isinstance(self.intensity_parameter, str)
            or not self.intensity_parameter.isidentifier()
            or self.intensity_parameter in {"brightness", "intensity", "speed", "rate"}
        ):
            raise ValueError("intensity_parameter must be a named component-local control")
        if self.required_simulation_inputs:
            raise ValueError("packet A does not support required plant inputs")
        if self.timing_policy is TimingPolicy.WALL_CLOCK:
            raise ValueError("packet A accepts only scaled_context components")
        optional_inputs = tuple(self.optional_simulation_inputs)
        if (
            any(not isinstance(item, str) or not item for item in optional_inputs)
            or len(optional_inputs) != len(set(optional_inputs))
        ):
            raise ValueError("optional simulation inputs must be unique names")
        object.__setattr__(self, "optional_simulation_inputs", optional_inputs)
        object.__setattr__(self, "required_simulation_inputs", tuple(self.required_simulation_inputs))
        object.__setattr__(self, "defaults", MappingProxyType(dict(self.defaults)))


class ComponentCatalog:
    """Provider-qualified immutable component lookup."""

    def __init__(self, descriptors: tuple[ComponentDescriptor, ...] | list[ComponentDescriptor]):
        indexed: dict[tuple[str, str], ComponentDescriptor] = {}
        for descriptor in descriptors:
            key = (descriptor.provider.value, descriptor.component_id)
            if key in indexed:
                raise ValueError(f"duplicate component descriptor {key!r}")
            indexed[key] = descriptor
        self._descriptors = MappingProxyType(indexed)

    def require(self, *, provider: str, component_id: str, version: int) -> ComponentDescriptor:
        try:
            descriptor = self._descriptors[(ComponentProvider(provider).value, component_id)]
        except (KeyError, ValueError) as exc:
            raise ValueError("scene background is not a qualified catalog component") from exc
        if descriptor.version != version:
            raise ValueError("scene component version does not match the catalog")
        return descriptor
