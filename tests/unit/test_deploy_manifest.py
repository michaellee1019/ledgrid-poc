"""Regression tests for tracked deployment manifests and rsync safety."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path, PurePosixPath

from tools.deployment.deploy_manifest import tracked_paths


class DeployManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_dir.name)
        subprocess.run(["git", "init", "-q", self.root], check=True)

    def tearDown(self):
        self.temporary_dir.cleanup()

    def _write(self, relative_path: str, content: bytes = b"test") -> Path:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def _track(self, *relative_paths: str) -> None:
        subprocess.run(["git", "-C", self.root, "add", "--", *relative_paths], check=True)

    def test_fast_manifest_includes_tracked_code_templates_and_plugin_assets(self):
        tracked = (
            "animation/plugins/rainbow/__init__.py",
            "animation/plugins/rainbow/presets/vivid.json",
            "animation/plugins/gif_animation/assets/party.gif",
            "animation/plugins/gif_animation/assets/contact-sheet.png",
            "web/templates/index.html",
            "web/static/css/dashboard.css",
            "web/static/js/dashboard.js",
            "config/plant_globe_map_32x138.json",
        )
        for path in tracked:
            self._write(path)
        self._track(*tracked)

        self.assertEqual(
            set(tracked_paths(self.root, "fast")),
            {PurePosixPath(path) for path in tracked},
        )

    def test_fast_manifest_excludes_untracked_and_runtime_presets(self):
        self._write("animation/plugins/rainbow/untracked.py")
        self._write("animation/plugins/rainbow/presets/untracked.json")
        self._write("presets/animations/rainbow/user-saved.json")
        self._write("presets/animations/rainbow/old-curated.json")
        self._track("presets/animations/rainbow/old-curated.json")

        self.assertEqual(tracked_paths(self.root, "fast"), [])

    def test_full_manifest_includes_only_tracked_non_runtime_files(self):
        self._write("scripts/start_server.py")
        self._write("docs/README.md")
        self._write("local-only.txt")
        self._write("presets/animations/rainbow/runtime.json")
        self._track(
            "scripts/start_server.py",
            "docs/README.md",
            "presets/animations/rainbow/runtime.json",
        )

        self.assertEqual(
            tracked_paths(self.root, "full"),
            [PurePosixPath("docs/README.md"), PurePosixPath("scripts/start_server.py")],
        )

    def test_manifest_omits_tracked_files_deleted_from_worktree(self):
        deleted = self._write("docs/removed.md")
        self._track("docs/removed.md")
        deleted.unlink()

        self.assertEqual(tracked_paths(self.root, "full"), [])

    def test_sync_contract_deletes_stale_code_but_protects_target_state(self):
        root = Path(__file__).resolve().parents[2]
        sync_script = (root / "tools/deployment/sync_files.sh").read_text(encoding="utf-8")

        self.assertIn("rsync -az --delete --stats", sync_script)
        for protected_path in (
            "--exclude 'venv/'",
            "--exclude 'run_state/'",
            "--exclude 'presets/animations/'",
            "--exclude '.esp32_firmware_hash'",
            "--exclude '*.log'",
        ):
            self.assertIn(protected_path, sync_script)
        self.assertIn("deployment_manifest fast", sync_script)
        self.assertIn("deployment_manifest full", sync_script)
        self.assertNotIn("--delete", sync_script[sync_script.index("sync_fast_deployment"):])


if __name__ == "__main__":
    unittest.main()
