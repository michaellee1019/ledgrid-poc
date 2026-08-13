# Animation Plugins

Purpose: self-contained animation packages loaded by the plugin system.

Guidelines:
- Each `<plugin_id>/` package owns its implementation (`__init__.py`),
  `manifest.json`, focused `tests/`, curated `presets/`, and any `assets/`.
- Each package defines exactly one concrete AnimationBase subclass; its class
  and package ID must match the manifest.
- Existing packages default to Python backgrounds. New component manifests must
  declare `provider`, `role`, `entrypoint`, and `cadence` together.
- Explicit overlays return contiguous premultiplied RGBA8 `OverlayFrame` values;
  ordinary/background/full-scene plugins retain the RGB frame contract.
- Keep overlay content revision monotonic, return `changed=False` for cached
  source ticks, and dirty the union of previous/new alpha coverage when moving.
- Keep public imports stable as `animation.plugins.<plugin_id>`.
- Root `presets/animations/` is reserved for writable runtime presets. Curated
  presets belong to the plugin package and are read-only at runtime.
- Set ANIMATION_NAME and ANIMATION_DESCRIPTION for UI display.
- Avoid hardware calls directly; use the provided controller.
- Prefer deterministic output for easier debugging.
- Render into reusable canonical NumPy buffers; avoid per-pixel Python objects.
- Use elapsed time for motion and mark unchanged/source-rate frames explicitly.
- Do not apply universal plant optics or vibe luminance inside an overlay; the
  manager owns both operations once after fixed-point host composition.
- For effects derived from logical masks, use `animation.libraries.mask_effects` so
  dilation/halo geometry respects strip and panel boundaries. `Plant Glow` is
  the reference implementation for separate semantic cores and falloff rings.
