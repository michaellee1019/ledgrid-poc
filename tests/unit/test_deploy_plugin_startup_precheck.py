"""Fail-closed, no-hardware deployment plugin startup coverage."""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any
import unittest
from unittest import mock

from animation import AnimationBase
from animation.core.plugin_loader import AnimationPluginLoader
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT
from tools.deployment.plugin_startup_precheck import (
    PluginStartupPrecheckError,
    run_plugin_startup_precheck,
)


ROOT = Path(__file__).resolve().parents[2]


class _HistoricalAnyLoader(AnimationPluginLoader):
    """The pre-fix candidate test which forwarded ``typing.Any`` to issubclass."""

    @staticmethod
    def _is_concrete_animation_class(candidate: object, module_name: str) -> bool:
        if not module_name.endswith("snake"):
            return AnimationPluginLoader._is_concrete_animation_class(
                candidate, module_name
            )
        # This is the exact historical bad branch: ``typing.Any`` was treated
        # as a subclass candidate rather than filtered before ``issubclass``.
        if candidate is Any:
            return issubclass(candidate, AnimationBase)
        return (
            inspect.isclass(candidate)
            and issubclass(candidate, AnimationBase)
            and candidate is not AnimationBase
            and not inspect.isabstract(candidate)
        )


class DeploymentPluginStartupPrecheckTests(unittest.TestCase):
    def _write_plugin(self, root: Path, plugin_id: str, source: str, class_name: str) -> None:
        package = root / plugin_id
        package.mkdir()
        (package / "__init__.py").write_text(source, encoding="utf-8")
        (package / "manifest.json").write_text(
            json.dumps({
                "plugin_id": plugin_id,
                "class": class_name,
                "icon": "🧪",
                "gallery": "show",
            }),
            encoding="utf-8",
        )

    def _write_complete_plugin_tree_with(
        self,
        root: Path,
        plugin_id: str,
        source: str,
        class_name: str,
        *,
        omit: tuple[str, ...] = (),
    ) -> None:
        """Keep every current retained plugin while replacing one package."""
        for candidate in AnimationPluginLoader.DEFAULT_PLUGINS_DIR.iterdir():
            if candidate.name in (plugin_id, *omit) or candidate.stem in (plugin_id, *omit):
                continue
            destination = root / candidate.name
            if candidate.name == "aurora_curtains_native":
                # Its manifest deliberately rejects a symlinked package root.
                shutil.copytree(candidate, destination)
            else:
                os.symlink(
                    candidate,
                    destination,
                    target_is_directory=candidate.is_dir(),
                )
        self._write_plugin(root, plugin_id, source, class_name)

    def test_healthy_current_tree_loads_every_plugin_and_renders_saved_plant_glow(self):
        result = run_plugin_startup_precheck()

        self.assertIn("plant_glow", result.plugins)
        self.assertIn("ambient_scene", result.plugins)
        self.assertIn("snake", result.plugins)
        self.assertEqual(
            result.frame_shape,
            (DEFAULT_STRIP_COUNT * DEFAULT_LEDS_PER_STRIP, 3),
        )

    def test_deploy_precheck_recipe_runs_only_the_local_startup_gate(self):
        justfile = (ROOT / "Justfile").read_text(encoding="utf-8")
        recipe = justfile.split("deploy-precheck:", 1)[1].split(
            "\n# Run the receiver-side", 1
        )[0]

        self.assertIn("plugin_startup_precheck.py", recipe)
        self.assertNotIn("ssh", recipe)
        self.assertNotIn("receiver_acceptance", recipe)
        self.assertNotIn("deploy_entrypoint.py run", recipe)

    def test_demo_gate_tracks_the_reconstructed_composer_slice(self):
        justfile = (ROOT / "Justfile").read_text(encoding="utf-8")
        recipe = justfile.split("test-demo:", 1)[1].split(
            "\n# Required gate", 1
        )[0]

        for current_test in (
            "test_composer_desktop_workspace.py",
            "test_composer_runtime_preview.py",
            "test_composer_preset_catalog_boundary.py",
            "test_composer_maintenance_api.py",
            "test_deploy_plugin_startup_precheck.py",
            "test_deploy_startup.py",
        ):
            self.assertIn(current_test, recipe)
        for retired_test in (
            "test_browser_composer_mobile_ux.py",
            "test_browser_composer_pwa.py",
            "test_scene_activation_api.py",
        ):
            self.assertNotIn(retired_test, recipe)

    def test_removed_plant_glow_backgrounds_reference_blocks_before_activation(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            self._write_complete_plugin_tree_with(
                Path(temporary_dir),
                "plant_glow",
                "from animation.plugins.plant_glow import BACKGROUNDS\n",
                "PlantGlowAnimation",
            )
            with self.assertRaisesRegex(
                PluginStartupPrecheckError,
                r"plugin 'plant_glow'.*(ImportError|BACKGROUNDS)",
            ):
                run_plugin_startup_precheck(plugins_dir=temporary_dir)

    def test_missing_allowlisted_plugin_blocks_before_any_startup(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            self._write_complete_plugin_tree_with(
                Path(temporary_dir),
                "plant_glow",
                "from animation import AnimationBase\n"
                "class PlantGlowAnimation(AnimationBase):\n"
                "    def generate_frame(self, time_elapsed, frame_count):\n"
                "        return self.next_frame_buffer()\n",
                "PlantGlowAnimation",
                omit=("snake",),
            )
            with self.assertRaisesRegex(
                PluginStartupPrecheckError,
                r"missing allowlisted plugin\(s\): .*'snake'",
            ):
                run_plugin_startup_precheck(plugins_dir=temporary_dir)

    def test_undefined_ambient_scene_schema_blocks_before_activation(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            self._write_complete_plugin_tree_with(
                Path(temporary_dir),
                "ambient_scene",
                "from animation import AnimationBase\n"
                "class AmbientSceneAnimation(AnimationBase):\n"
                "    SCHEMA = UNDEFINED_SCHEMA\n",
                "AmbientSceneAnimation",
            )
            with self.assertRaisesRegex(
                PluginStartupPrecheckError,
                r"plugin 'ambient_scene'.*(NameError|UNDEFINED_SCHEMA)",
            ):
                run_plugin_startup_precheck(plugins_dir=temporary_dir)

    def test_historical_typing_any_issubclass_probe_blocks_before_activation(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            self._write_complete_plugin_tree_with(
                Path(temporary_dir),
                "snake",
                "from typing import Any\n"
                "from animation import AnimationBase\n"
                "class SnakeAnimation(AnimationBase):\n"
                "    def generate_frame(self, time_elapsed, frame_count):\n"
                "        return self.next_frame_buffer()\n",
                "SnakeAnimation",
            )
            with self.assertRaisesRegex(
                PluginStartupPrecheckError,
                r"plugin 'snake'.*TypeError",
            ):
                original_issubclass = issubclass

                def reject_historical_any(candidate: object, parent: object) -> bool:
                    if candidate is Any:
                        raise TypeError("typing.Any reached issubclass")
                    return original_issubclass(candidate, parent)

                with mock.patch("builtins.issubclass", side_effect=reject_historical_any):
                    run_plugin_startup_precheck(
                        plugins_dir=temporary_dir,
                        loader_factory=_HistoricalAnyLoader,
                    )


if __name__ == "__main__":
    unittest.main()
