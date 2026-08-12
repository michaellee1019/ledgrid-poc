"""Core animation framework components."""

from animation.core.base import AnimationBase, FrameOutput, RenderedFrame, StatefulAnimationBase
from animation.core.plugin_loader import AnimationPluginLoader
from animation.core.plant_awareness import PlantMaskGeometry
from animation.core.presentation_contracts import (
    AnimationRuntimeContext,
    ResolvedVibe,
    TimingAdapter,
    VibeProfile,
    VibeState,
    get_vibe_profile,
    list_vibe_profiles,
    resolve_vibe,
)

__all__ = [
    "AnimationBase", "FrameOutput", "RenderedFrame", "StatefulAnimationBase",
    "AnimationPluginLoader", "PlantMaskGeometry", "AnimationRuntimeContext",
    "ResolvedVibe", "TimingAdapter", "VibeProfile", "VibeState",
    "get_vibe_profile", "list_vibe_profiles", "resolve_vibe",
]
