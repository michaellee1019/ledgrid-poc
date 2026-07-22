# Animation plugins

Animations are allowlisted, self-contained Python packages discovered by the
animation manager. The web UI reads the same registry for names, descriptions,
parameters, presets, previews, and reload operations.

## Package contract

Each built-in animation owns one directory:

```text
animation/plugins/<plugin_id>/
├── __init__.py       # AnimationBase subclass and plugin-specific code
├── manifest.json     # stable registry metadata
├── presets/          # curated JSON presets
├── tests/            # focused unit and behavior tests
└── assets/           # optional files used only by this plugin
```

Only `__init__.py` and `manifest.json` are required. Framework and lifecycle
contracts live in `animation/core/` with tests under `animation/core/tests/`.
Reusable rendering or simulation primitives used by multiple plugins belong in
`animation/libraries/` with tests under `animation/libraries/tests/`.

The package directory and manifest `plugin_id` must agree, and the manifest's
`class` must name the package's one concrete animation class. `icon` is required;
`gallery` is either `show` or `test`. Built-in packages are discovered in sorted
`plugin_id` order. Flat `.py` plugins remain supported only for explicitly
configured external plugin directories.

Root `presets/animations/<plugin_id>/` is a user-writable runtime overlay.
Do not place curated source presets there.

## Minimal plugin

`animation/plugins/example/__init__.py`:

```python
import numpy as np

from animation.core.base import AnimationBase


class ExampleAnimation(AnimationBase):
    ANIMATION_NAME = "Example"
    ANIMATION_DESCRIPTION = "A static red frame."
    ANIMATION_AUTHOR = "LED Grid"
    ANIMATION_VERSION = "1.0"

    def generate_frame(self, time_elapsed: float, frame_count: int) -> np.ndarray:
        frame = self.next_frame_buffer(clear=False)
        frame[:] = (255, 0, 0)
        return frame
```

`animation/plugins/example/manifest.json`:

```json
{
  "plugin_id": "example",
  "class": "ExampleAnimation",
  "icon": "💡",
  "gallery": "show"
}
```

Checked-in manifests are the built-in allowlist. A directory without a valid
manifest is not loaded or exposed by the web API.

## Frame contract

`generate_frame(time_elapsed, frame_count)` returns either:

- a C-contiguous `numpy.uint8` array shaped `(controller.total_leds, 3)`; or
- `RenderedFrame(pixels, changed, dirty_ranges)` with presentation hints.

Use `next_frame_buffer()` instead of allocating a fresh full-wall array on each
frame. Source-rate or event-driven plugins should return `changed=False` while
their image is unchanged. `dirty_ranges` may identify canonical flat-index
ranges for a controller that supports partial transfer.

Simulation state belongs to the plugin instance. Use elapsed time or a bounded
fixed timestep for motion; do not make behavior depend on web request timing.
Plugins must not call SPI or the web layer directly.

## Parameters

Extend the base schema and read applied values from `self.params`:

```python
def get_parameter_schema(self):
    schema = super().get_parameter_schema()
    schema["density"] = {
        "type": "float",
        "min": 0.0,
        "max": 1.0,
        "default": 0.25,
        "description": "Fraction of active pixels",
    }
    return schema
```

Defaults must render a useful, bounded scene without network, sensor, or user
input. Keep parameter names stable because saved runtime presets refer to them.

Numeric controls may also declare named values with an optional `presets`
mapping. The names are presentation metadata: the selected value sent to the
animation and stored in an animation preset remains numeric, and operators can
still choose any value allowed by the control's range.

```python
schema["background_speed"] = {
    "type": "float",
    "min": 0.0,
    "max": 3.0,
    "default": 1.0,
    "presets": {"frozen": 0.0, "normal": 1.0, "lively": 2.0},
    "description": "Backdrop motion speed",
}
```

## Plant-aware rendering

The base schema supplies `plant_aware`, `plant_clearance`, `plant_mask_path`,
and `plant_globe_mask_path`. Plugins load calibrated masks lazily through
`get_plant_masks()` and guard all mask-specific behavior with
`plant_aware_enabled()`.

Keep foliage, globes, their union, and clearance-expanded obstacles semantically
separate. Interactive simulations can use them for collision and routing;
visual effects can use them as masks or accent layers. Turning plant awareness
off must restore ordinary plugin behavior.

Shared mask geometry belongs in `animation/libraries/`, not in individual
plugins.

## Presets and assets

Curated presets live at `animation/plugins/<plugin_id>/presets/*.json` and are
versioned with the code they configure. The registry merges them with runtime
presets from `presets/animations/<plugin_id>/`, with runtime files remaining
ignored until intentionally promoted into the plugin package.

An asset used by one plugin belongs in that plugin's `assets/` directory. An
asset used by several plugins may live under root `assets/` with a documented
owner and format.

## Tests and acceptance

Focused tests live beside the plugin. They should cover:

- manifest discovery and import;
- frame shape, dtype, contiguity, and bounds;
- deterministic state transitions for seeded simulations;
- meaningful parameter extremes;
- ordinary and plant-aware behavior when applicable;
- every curated preset loading and rendering successfully.

Run the repository checks before exposing a plugin:

```bash
just test
just test-rendering
```

The rendering benchmark is the authoritative performance gate for the installed
32 x 138 geometry.

## Runtime boundaries

The controller process owns plugin instances and hardware presentation. The web
process writes commands through `ipc/control_channel.py` and reads status and
preview frames from the same channel. Hot reload is suitable for local plugin
iteration, but production changes should go through the normal deploy and
acceptance flow.
