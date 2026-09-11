#!/usr/bin/env python3
"""Fail a deployment before activation when current plugin startup is broken.

The gate imports every retained package, then resolves and renders the current
Plant Glow Scene through the production host runtime without opening an SPI
device, publishing a frame, or altering persisted runtime state.  Its RGB
output uses an inert host background and is not receiver-native final proof.
"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from pathlib import Path
import sys
from typing import Callable, Optional, Type

import numpy as np


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from animation.core.manager import AnimationManager
from animation.core.plugin_loader import AnimationPluginLoader
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT


class PluginStartupPrecheckError(RuntimeError):
    """A retained import or current Scene startup failure that blocks activation."""


@dataclass(frozen=True)
class PluginStartupPrecheckResult:
    """Small, serializable-free proof returned to tests and local callers."""

    plugins: tuple[str, ...]
    frame_shape: tuple[int, int]
    foreground_shape: tuple[int, int]


LoaderFactory = Callable[..., AnimationPluginLoader]


def _failure_detail(output: str) -> str:
    """Keep the loader's named exception while avoiding an entire traceback dump."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    relevant = [
        line
        for line in lines
        if (
            "Failed to load plugin" in line
            or line.startswith(("ImportError:", "NameError:", "TypeError:"))
        )
    ]
    detail = relevant[-1] if relevant else (lines[-1] if lines else "no loader diagnostic")
    return detail


def _load_retained_plugins(
    loader: AnimationPluginLoader, plugin_ids: tuple[str, ...]
) -> None:
    """Load each retained package one-at-a-time so a failure names its package."""
    for plugin_id in plugin_ids:
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            loaded = loader.load_plugin(plugin_id)
        if loaded is None:
            raise PluginStartupPrecheckError(
                f"plugin {plugin_id!r} failed discovery/import: "
                f"{_failure_detail(output.getvalue())}"
            )


def _validate_complete_retained_set(plugin_ids: tuple[str, ...]) -> None:
    """Reject a discovery result that silently dropped a shipped plugin."""
    expected = set(AnimationManager.ALLOWED_PLUGINS)
    discovered = set(plugin_ids)
    missing = sorted(expected - discovered)
    unexpected = sorted(discovered - expected)
    if missing:
        named = ", ".join(repr(plugin_id) for plugin_id in missing)
        raise PluginStartupPrecheckError(
            f"plugin discovery is missing allowlisted plugin(s): {named}"
        )
    if unexpected:
        named = ", ".join(repr(plugin_id) for plugin_id in unexpected)
        raise PluginStartupPrecheckError(
            f"plugin discovery found unexpected plugin(s): {named}"
        )


def _validate_current_plant_glow_frame(
    rendered: object,
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Validate the host RGB diagnostic and receiver foreground contract."""

    pixels = np.asarray(getattr(rendered, "pixels", None))
    expected_rgb_shape = (DEFAULT_STRIP_COUNT * DEFAULT_LEDS_PER_STRIP, 3)
    if pixels.shape != expected_rgb_shape or pixels.dtype != np.uint8:
        raise PluginStartupPrecheckError(
            "plugin 'plant_glow' returned an invalid current host RGB frame: "
            f"shape={pixels.shape!r}, dtype={pixels.dtype!s}"
        )

    foreground = getattr(rendered, "foreground", None)
    rgba = np.asarray(getattr(foreground, "pixels", None))
    expected_rgba_shape = (DEFAULT_STRIP_COUNT * DEFAULT_LEDS_PER_STRIP, 4)
    if (
        rgba.shape != expected_rgba_shape
        or rgba.dtype != np.uint8
        or not rgba.flags.c_contiguous
    ):
        raise PluginStartupPrecheckError(
            "plugin 'plant_glow' returned an invalid current foreground plane: "
            f"shape={rgba.shape!r}, dtype={rgba.dtype!s}, "
            f"c_contiguous={rgba.flags.c_contiguous!r}"
        )
    alpha = rgba[:, 3]
    if np.any(rgba[:, :3] > alpha[:, None]):
        raise PluginStartupPrecheckError(
            "plugin 'plant_glow' returned non-premultiplied current foreground RGBA"
        )
    if not np.any(alpha > 0) or not np.any(alpha == 0):
        raise PluginStartupPrecheckError(
            "plugin 'plant_glow' current foreground must contain both lit and transparent pixels"
        )
    return tuple(pixels.shape), tuple(rgba.shape)


def run_plugin_startup_precheck(
    *,
    plugins_dir: Optional[str | Path] = None,
    loader_factory: LoaderFactory = AnimationPluginLoader,
) -> PluginStartupPrecheckResult:
    """Exercise retained imports plus Plant Glow's current resolved Scene path.

    The explicit first discovery pass turns the manager's historical
    best-effort logging into a fail-closed deployment result with both the
    plugin ID and its import/schema/type exception. The second pass exercises
    production Scene normalization and rendering in foreground-only host mode.
    That mode proves the receiver foreground payload, not receiver-native final
    background rendering.
    """
    resolved_plugins_dir = (
        Path(plugins_dir).resolve() if plugins_dir is not None else None
    )
    loader = loader_factory(
        None if resolved_plugins_dir is None else str(resolved_plugins_dir),
        allowed_plugins=AnimationManager.ALLOWED_PLUGINS,
    )
    try:
        plugin_ids = tuple(loader.scan_plugins())
    except Exception as exc:
        raise PluginStartupPrecheckError(
            "plugin '<discovery>' failed manifest discovery: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    if not plugin_ids:
        raise PluginStartupPrecheckError("plugin '<discovery>' found no retained plugins")
    _validate_complete_retained_set(plugin_ids)
    _load_retained_plugins(loader, plugin_ids)

    try:
        # Import after the fail-closed retained-package pass. Broken retained
        # imports must keep their precise package diagnostics.
        from ipc.scene_contract import normalize_composer_scene
        from web.composer_final_preview import (
            ComposerFinalPreview,
            current_component_catalog,
            native_aurora_descriptor,
        )
        from animation.plugins.plant_glow import PlantGlowAnimation

        catalog = current_component_catalog()
        background = native_aurora_descriptor()
        plant_glow = PlantGlowAnimation.component_descriptor()
        scene = {
            "schema": "ledgrid.scene.v2",
            "background": {
                "component_id": background.component_id,
                "version": background.version,
                "provider": background.provider.value,
                "role": background.role.value,
                "bundle_digest": background.defaults["bundle_digest"],
                "parameters": {},
            },
            "animation": {
                "component_id": plant_glow.component_id,
                "version": plant_glow.version,
                "provider": plant_glow.provider.value,
                "role": plant_glow.role.value,
                "parameters": plant_glow.default_parameters(),
            },
            "widgets": [],
            "plants": {"effects": {"version": 1, "active": [], "strengths": {}}},
            "look": {
                "palette_id": "neutral",
                "pace": 1.0,
                "presentation_brightness": 1.0,
            },
        }
        canonical = normalize_composer_scene(
            {"origin": "composer", "scene": scene}, catalog
        )
        preview = ComposerFinalPreview(catalog, REPOSITORY_ROOT, foreground_only=True)
        rendered = preview.render(canonical, 0.0, datetime.now().astimezone())
        frame_shape, foreground_shape = _validate_current_plant_glow_frame(rendered)
        return PluginStartupPrecheckResult(plugin_ids, frame_shape, foreground_shape)
    except PluginStartupPrecheckError:
        raise
    except Exception as exc:
        raise PluginStartupPrecheckError(
            "plugin 'plant_glow' failed current resolved-Scene startup: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def main() -> int:
    try:
        result = run_plugin_startup_precheck()
    except PluginStartupPrecheckError as exc:
        print(f"deployment plugin startup precheck failed: {exc}", file=sys.stderr)
        return 1
    print(
        "deployment plugin startup precheck passed: "
        f"{len(result.plugins)} retained plugin imports; Plant Glow rendered "
        f"the canonical {DEFAULT_STRIP_COUNT}x{DEFAULT_LEDS_PER_STRIP} wall "
        f"({result.foreground_shape[0]} premultiplied foreground RGBA pixels); "
        "host foreground RGB only, not receiver-native final proof"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
