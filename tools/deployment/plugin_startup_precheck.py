#!/usr/bin/env python3
"""Fail a deployment before activation when controller plugin startup is broken.

This intentionally uses the controller's in-memory preview implementation.  It
therefore exercises the same manager discovery and saved-animation startup path
as the controller, without opening an SPI device, publishing a frame, or
altering persisted runtime state.
"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
import sys
from typing import Callable, Optional, Type

import numpy as np


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from animation.core.base import RenderedFrame
from animation.core.manager import AnimationManager, PreviewLEDController
from animation.core.plugin_loader import AnimationPluginLoader
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT


# This is the preserved operator selection that must still be able to start
# after every deployment.  Parameters deliberately remain empty: the plugin
# owns its current defaults and schema.
SAVED_PLANT_GLOW_SCENE = {
    "plugin_id": "plant_glow",
    "parameters": {},
}


class PluginStartupPrecheckError(RuntimeError):
    """A loader or saved-scene startup failure that must block activation."""


@dataclass(frozen=True)
class PluginStartupPrecheckResult:
    """Small, serializable-free proof returned to tests and local callers."""

    plugins: tuple[str, ...]
    frame_shape: tuple[int, int]


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


def run_plugin_startup_precheck(
    *,
    plugins_dir: Optional[str | Path] = None,
    loader_factory: LoaderFactory = AnimationPluginLoader,
) -> PluginStartupPrecheckResult:
    """Exercise retained plugin loading plus the saved Plant Glow startup path.

    The explicit first discovery pass turns the manager's historical
    best-effort logging into a fail-closed deployment result with both the
    plugin ID and its import/schema/type exception.  The second pass is the
    production manager startup path, on its no-I/O ``PreviewLEDController``.
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

    controller = PreviewLEDController(
        strips=DEFAULT_STRIP_COUNT,
        leds_per_strip=DEFAULT_LEDS_PER_STRIP,
    )
    manager: Optional[AnimationManager] = None
    startup_output = StringIO()
    try:
        with redirect_stdout(startup_output), redirect_stderr(startup_output):
            manager = AnimationManager(
                controller,
                plugins_dir=(None if resolved_plugins_dir is None else str(resolved_plugins_dir)),
                default_animation=SAVED_PLANT_GLOW_SCENE["plugin_id"],
                default_animation_config=dict(SAVED_PLANT_GLOW_SCENE["parameters"]),
                auto_start=True,
            )

        missing = sorted(set(plugin_ids) - set(manager.plugin_loader.loaded_plugins))
        if missing:
            raise PluginStartupPrecheckError(
                f"plugin {missing[0]!r} failed manager startup: "
                f"{_failure_detail(startup_output.getvalue())}"
            )
        if (
            not manager.is_running
            or manager.current_animation_name != SAVED_PLANT_GLOW_SCENE["plugin_id"]
            or manager.current_animation is None
        ):
            raise PluginStartupPrecheckError(
                f"plugin 'plant_glow' failed saved-scene startup: "
                f"{_failure_detail(startup_output.getvalue())}"
            )

        rendered = manager.current_animation.generate_frame(0.0, 0)
        pixels = rendered.pixels if isinstance(rendered, RenderedFrame) else rendered
        pixels = np.asarray(pixels)
        expected_shape = (DEFAULT_STRIP_COUNT * DEFAULT_LEDS_PER_STRIP, 3)
        if pixels.shape != expected_shape or pixels.dtype != np.uint8:
            raise PluginStartupPrecheckError(
                "plugin 'plant_glow' returned an invalid saved-scene frame: "
                f"shape={pixels.shape!r}, dtype={pixels.dtype!s}"
            )
        return PluginStartupPrecheckResult(plugin_ids, tuple(pixels.shape))
    except PluginStartupPrecheckError:
        raise
    except Exception as exc:
        raise PluginStartupPrecheckError(
            "plugin 'plant_glow' failed saved-scene startup: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    finally:
        if manager is not None:
            manager.stop_animation(clear_leds=False)


def main() -> int:
    try:
        result = run_plugin_startup_precheck()
    except PluginStartupPrecheckError as exc:
        print(f"deployment plugin startup precheck failed: {exc}", file=sys.stderr)
        return 1
    print(
        "deployment plugin startup precheck passed: "
        f"{len(result.plugins)} retained plugins; Plant Glow rendered "
        f"the canonical {DEFAULT_STRIP_COUNT}x{DEFAULT_LEDS_PER_STRIP} wall "
        f"({result.frame_shape[0]} RGB pixels)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
