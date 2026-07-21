"""Animation framework package."""

from animation.core.base import AnimationBase, FrameOutput, RenderedFrame, StatefulAnimationBase
from animation.core.plant_awareness import PlantMaskGeometry

__all__ = ["AnimationBase", "FrameOutput", "RenderedFrame", "StatefulAnimationBase", "PlantMaskGeometry"]
