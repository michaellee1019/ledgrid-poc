"""Fresh-process import boundaries for the Scene v2 contract."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]


class SceneContractImportTests(unittest.TestCase):
    def _assert_import_order(self, imports: str) -> None:
        script = textwrap.dedent(f"""
            {imports}
            from animation import AnimationBase, RenderedFrame
            from ipc.scene_contract import (
                SCENE_V2_REVISION,
                SCENE_V2_SCHEMA,
                SceneIdentity,
                normalize_composer_scene,
            )
            from animation.core.presentation_contracts import (
                SCENE_V2_SCHEMA as PRESENTATION_SCHEMA,
                resolve_scene,
            )
            from tests.unit.test_scene_activation_contract import _catalog, _request

            assert SCENE_V2_SCHEMA == PRESENTATION_SCHEMA == "ledgrid.scene.v2"
            assert SCENE_V2_REVISION == 2
            assert SceneIdentity(2, "a" * 64).to_dict() == {{
                "revision": 2,
                "digest": "a" * 64,
            }}
            assert AnimationBase.__module__ == "animation.core.base"
            assert RenderedFrame.__module__ == "animation.core.base"
            assert normalize_composer_scene.__module__ == "ipc.scene_contract"
            assert resolve_scene.__module__ == "animation.core.presentation_contracts"
            canonical = normalize_composer_scene(_request(), _catalog())
            resolved = resolve_scene(
                canonical.scene, _catalog(), monotonic_elapsed=1.25
            )
            assert resolved.canonical_bytes == canonical.canonical_bytes
            assert resolved.digest == canonical.identity.digest
        """)
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_scene_contract_then_presentation_entrypoints(self) -> None:
        self._assert_import_order(
            "from ipc.scene_contract import normalize_composer_scene"
        )

    def test_presentation_entrypoints_then_scene_contract(self) -> None:
        self._assert_import_order(
            "from animation.core.presentation_contracts import resolve_scene"
        )


if __name__ == "__main__":
    unittest.main()
