"""Core animation framework components."""

from animation.core.base import AnimationBase, StatefulAnimationBase
from animation.core.plugin_loader import AnimationPluginLoader

__all__ = ["AnimationBase", "StatefulAnimationBase", "AnimationPluginLoader"]
