"""Current host catalog browser capabilities are built from source artifacts."""
from pathlib import Path
import tempfile
import unittest
from tools.build_browser_composer_assets import build_stage
import json
ROOT=Path(__file__).resolve().parents[2]

class BrowserCatalogTests(unittest.TestCase):
    def test_browser_renderers_remain_available_for_the_retained_games_and_shows(self):
        with tempfile.TemporaryDirectory() as d:
            stage=Path(d)/'composer';build_stage(ROOT,stage)
            catalog=json.loads((stage/'bootstrap.v1.json').read_text())['components']
            entries={item['plugin_id']:item for item in catalog}
            for component_id in ('aurora_curtains','cellular_tapestry','circadian_window','snake','tetris','maze_chase','pinball','pixel_quest','pixel_chase','canopy_cup'):
                with self.subTest(component_id=component_id):
                    self.assertEqual(entries[component_id]['provider'],'python')
                    self.assertTrue(entries[component_id]['browser_runtime']['supported'])
                    self.assertEqual(entries[component_id]['browser_runtime']['kind'],'python')
            self.assertNotIn('world_flags',entries)
            self.assertNotIn('native_aurora',entries)
