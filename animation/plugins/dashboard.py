#!/usr/bin/env python3
"""Plugin-loader entrypoint for dashboard animation."""

from animation.dashboard import DashboardAnimation as _DashboardAnimation


class DashboardAnimation(_DashboardAnimation):
    """Thin compatibility wrapper used by plugin discovery."""

