"""Retained host animation sources render in the portable browser bundle."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
import io
from tools.build_browser_python_bundle import build_archive
ROOT=Path(__file__).resolve().parents[2]

class HostBrowserRenderingTests(unittest.TestCase):
    def test_retained_show_renderers_load_from_the_isolated_browser_archive(self):
        archive=build_archive(ROOT)
        with tempfile.TemporaryDirectory() as d:
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                self.assertFalse(any('world_flags' in name or 'native_aurora' in name for name in bundle.namelist()))
                bundle.extractall(d)
            script=r'''
import pathlib, sys
sys.path.insert(0,sys.argv[1])
from ledgrid_browser_runtime import BrowserPreviewRuntime
profile=pathlib.Path(sys.argv[2]);digest=profile.read_bytes()[68:100].hex()
runtime=BrowserPreviewRuntime();runtime.bind_installation_profile_path(str(profile),digest)
for plugin,cls in [('aurora_curtains','AuroraCurtainsAnimation'),('cellular_tapestry','CellularTapestryAnimation'),('circadian_window','CircadianWindowAnimation'),('snake','SnakeAnimation'),('tetris','TetrisAnimation'),('maze_chase','MazeChaseAnimation'),('pinball','PinballAnimation'),('pixel_quest','PixelQuestAnimation'),('pixel_chase','PixelChaseAnimation'),('canopy_cup','CanopyCupAnimation')]:
    runtime.initialize(plugin,cls,{'width':33,'height':138},instance_id=plugin,installation_profile_digest=digest)
    result=runtime.render(1.25,1,instance_id=plugin)
    assert result['frameFormat'] in ('rgb','premultiplied-rgba'),result
    assert result['width']==33 and result['height']==138,result
    assert len(runtime.frame_bytes_for(plugin))==33*138*(3 if result['frameFormat']=='rgb' else 4)
    runtime.dispose_instance(plugin)
'''
            result=subprocess.run([sys.executable,'-c',script,d,str(ROOT/'tests/fixtures/installation_profile_v1.bin')],cwd=d,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
