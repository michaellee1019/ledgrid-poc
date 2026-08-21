# Animation Plugins

Purpose: self-contained animation packages discovered by the unified component
catalog. Python packages are also loaded by the plugin execution system;
receiver-native packages are descriptor/build inputs only in Phase 3D.

Guidelines:
- Each Python `<plugin_id>/` package owns its implementation (`__init__.py`),
  `manifest.json`, focused `tests/`, curated `presets/`, and any `assets/`.
- Each Python package defines exactly one concrete AnimationBase subclass; its class
  and package ID must match the manifest.
- Existing packages default to Python backgrounds. New component manifests must
  declare `provider`, `role`, `entrypoint`, and `cadence` together.
- Explicit overlays return contiguous premultiplied RGBA8 `OverlayFrame` values;
  ordinary/background/full-scene plugins retain the RGB frame contract.
- Keep overlay content revision monotonic, return `changed=False` for cached
  source ticks, and dirty the union of previous/new alpha coverage when moving.
- Keep public imports stable as `animation.plugins.<plugin_id>`.
- A `receiver_native` package instead owns exactly
  `native/background.cpp`, has no `__init__.py` or Python class, and declares
  the complete ABI-v2 descriptor in `manifest.json`. It is catalog-visible but
  absent from `scan_plugins()`, `ALLOWED_PLUGINS`, Python reload/start/preview,
  and `AnimationBase` construction.
- Receiver-native manifests own their bounded `bool`, `int`, `float`, and
  option-backed `str` parameter schema; descriptor defaults are derived from
  those definitions. Curated native presets use the same JSON envelope and are
  validated against that manifest-owned schema.
- Native preview metadata declares a host-build simulation only. The builder
  may publish generated WebP metadata, but neither web nor controller code may
  import the package or execute the target ELF. Phase 3D does not activate the
  module on receivers.
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
