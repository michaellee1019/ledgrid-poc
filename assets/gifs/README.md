# Cute Pixel GIF Pack

This directory contains 32 original procedural pixel-art loops authored for the
wall's native `32x138` portrait canvas, plus the earlier
`penguin_top_center.gif` sample.

The cute pack is intentionally small and LED-friendly:

- 8 frames per loop at 140 ms per frame
- 64-color adaptive palettes
- native resolution, so the GIF plugin does not blur or crop the art
- near-black backgrounds and high-contrast silhouettes
- four vertically repeated motifs, so every scene fills the full wall
- infinite-loop metadata and matching presets in
  `presets/animations/gif_animation`

Regenerate the pack from repository-owned drawing primitives:

```bash
uv run --with pillow python scripts/generate_cute_gif_pack.py --overwrite
```

`cute-pixel-concepts.png` is the AI-generated art-direction sheet used to pick
the pack's themes and palettes. The shipped GIF pixels themselves are generated
deterministically by `scripts/generate_cute_gif_pack.py`; no downloaded or
third-party character art is embedded in them.

`cute-gif-pack-contact-sheet.png` shows the first frame of every shipped loop
at 3x scale for quick visual review.
