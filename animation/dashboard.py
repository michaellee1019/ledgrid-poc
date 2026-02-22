"""Compatibility shim exposing the dashboard plugin from the new module."""

from dashboard.plugin import DashboardAnimationPlugin as _DashboardAnimationPlugin


class DashboardAnimation(_DashboardAnimationPlugin):
    """AnimationBase-compatible dashboard wrapper."""

