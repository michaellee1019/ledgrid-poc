"""Source-only Composer assets are built in staging without offline/native output."""
from pathlib import Path
import tempfile
import unittest
from tools.build_browser_composer_assets import build_stage, validate_asset_set
ROOT=Path(__file__).resolve().parents[2]

class ComposerAssetPublicationTests(unittest.TestCase):
    def test_staged_source_assets_are_deterministic_and_complete(self):
        with tempfile.TemporaryDirectory() as d:
            first=build_stage(ROOT,Path(d)/'first')
            second=build_stage(ROOT,Path(d)/'second')
            self.assertEqual(first,second)
            self.assertEqual(len(first),3)
            self.assertIn('bootstrap.v1.json',first)
            self.assertIn('ledgrid_python_runtime.zip',first)
            self.assertFalse(any('offline' in name or 'native' in name for name in first))
            validate_asset_set(ROOT,Path(d)/'first')
    def test_browser_runtime_identity_detects_tampered_generated_zip(self):
        with tempfile.TemporaryDirectory() as d:
            stage=Path(d)/'composer';build_stage(ROOT,stage)
            (stage/'ledgrid_python_runtime.zip').write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError,'runtime digest'):
                validate_asset_set(ROOT,stage)
