"""Core animation framework components."""

from animation.core.base import AnimationBase, FrameOutput, RenderedFrame, StatefulAnimationBase
from animation.core.plugin_loader import AnimationPluginLoader

__all__ = ["AnimationBase", "FrameOutput", "RenderedFrame", "StatefulAnimationBase", "AnimationPluginLoader"]
