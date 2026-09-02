"""Browser-safe compatibility exports for plugins importing ``animation.core.base``."""

from animation import AnimationBase, FrameOutput, RenderedFrame, StatefulAnimationBase

__all__ = (
    "AnimationBase",
    "FrameOutput",
    "RenderedFrame",
    "StatefulAnimationBase",
)
