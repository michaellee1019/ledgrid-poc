"""Strictly typed host scene and vibe options."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any, Mapping


ANIMATION_PIPELINE_FEATURE_FLAG_SCHEMA = "ledgrid.animation-pipeline-feature-flags"
ANIMATION_PIPELINE_FEATURE_FLAG_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class AnimationPipelineFeatureFlags:
    """Host-only options; retired receiver execution options are unsupported."""

    vibe_context: bool = False
    scene_layers: bool = False

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is not bool:
                raise TypeError(f"feature flag {field.name!r} must be a boolean")

    @classmethod
    def from_mapping(
        cls, payload: Mapping[str, Any] | None
    ) -> "AnimationPipelineFeatureFlags":
        """Validate an optional partial mapping, defaulting omitted gates off."""

        if payload is None:
            return cls()
        if not isinstance(payload, Mapping):
            raise TypeError("animation pipeline feature flags must be a mapping")

        known = {field.name for field in fields(cls)}
        unknown = set(payload) - known
        if unknown:
            raise ValueError(
                "unknown animation pipeline feature flags: "
                + ", ".join(sorted(str(name) for name in unknown))
            )
        return cls(**dict(payload))

    def to_dict(self) -> dict[str, bool]:
        """Return the canonical persistence/configuration representation."""

        return asdict(self)

    @property
    def all_disabled(self) -> bool:
        return not any(self.to_dict().values())


ANIMATION_PIPELINE_FEATURE_FLAGS = AnimationPipelineFeatureFlags()
