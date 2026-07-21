# Animation Plugins

Purpose: concrete animation implementations loaded by the plugin system.

Guidelines:
- Each file defines at least one AnimationBase subclass.
- Set ANIMATION_NAME and ANIMATION_DESCRIPTION for UI display.
- Avoid hardware calls directly; use the provided controller.
- Prefer deterministic output for easier debugging.
- Render into reusable canonical NumPy buffers; avoid per-pixel Python objects.
- Use elapsed time for motion and mark unchanged/source-rate frames explicitly.
- For effects derived from logical masks, use `animation.core.mask_effects` so
  dilation/halo geometry respects strip and panel boundaries. `Plant Glow` is
  the reference implementation for separate semantic cores and falloff rings.
