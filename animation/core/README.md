# Animation core

The core owns framework and lifecycle contracts used by every plugin.

- `base.py`: `AnimationBase`, `RenderedFrame`, reusable frame buffers, and
  `StatefulAnimationBase`.
- `manager.py`: animation lifecycle, timing, previews, painter state, and frame
  presentation.
- `plugin_loader.py`: manifest validation, built-in package discovery, external
  plugin loading, and curated-preset enumeration.
- `plant_awareness.py`: cached installation geometry and plant-aware parameters.
- `tests/`: focused tests for these runtime contracts.

Reusable color, mask, palette, pixel-art, or spatial primitives belong in
`animation/libraries/`, with their tests colocated under
`animation/libraries/tests/`. Plugin-specific simulation and rendering remain
inside the owning plugin package.
