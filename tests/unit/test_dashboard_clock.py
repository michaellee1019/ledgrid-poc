"""Unit tests for dashboard clock widget fitting behavior."""

import unittest
from datetime import datetime

from dashboard.layout import Rect
from dashboard.text import measure_text
from dashboard.widgets.clock import ClockWidget


class DashboardClockWidgetTests(unittest.TestCase):
    def test_24h_seconds_compacts_to_fit_32_columns(self):
        widget = ClockWidget(use_24h=True, show_seconds=True)
        widget.consume({}, datetime(2026, 2, 22, 12, 34, 56))
        text, font = widget._select_render_text(Rect(0, 0, 32, 140))
        width, _height = measure_text(text, font, spacing=widget.spacing)
        self.assertLessEqual(width, 32)

    def test_12h_seconds_compacts_to_fit_32_columns(self):
        widget = ClockWidget(use_24h=False, show_seconds=True)
        widget.consume({}, datetime(2026, 2, 22, 12, 34, 56))
        text, font = widget._select_render_text(Rect(0, 0, 32, 140))
        width, _height = measure_text(text, font, spacing=widget.spacing)
        self.assertLessEqual(width, 32)


if __name__ == "__main__":
    unittest.main()
