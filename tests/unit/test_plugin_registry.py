from pathlib import Path
import unittest

from animation.core.manager import AnimationManager


class PluginRegistryTests(unittest.TestCase):
    def test_allowlist_matches_shipped_plugins(self):
        plugins_dir = Path(__file__).resolve().parents[2] / "animation" / "plugins"
        shipped_plugins = {
            path.stem
            for path in plugins_dir.glob("*.py")
            if not path.name.startswith("__")
        }

        self.assertSetEqual(AnimationManager.ALLOWED_PLUGINS, shipped_plugins)
