# GIF asset pipeline

The `gif_animation` plugin owns its implementation, curated presets, and shipped
GIF files:

```text
animation/plugins/gif_animation/
├── __init__.py
├── manifest.json
├── presets/
├── tests/
└── assets/
```

## Prepare an asset

Normalize source GIFs to the installed 32 x 138 canvas:

```bash
python3 scripts/prepare_gif_assets.py \
  --input-dir /path/to/source-gifs \
  --output-dir animation/plugins/gif_animation/assets \
  --width 32 \
  --height 138 \
  --fit-mode stretch \
  --overwrite
```

`stretch` fills the canvas exactly. `contain` preserves aspect ratio with
padding, and `cover` preserves aspect ratio while cropping. Use `--max-fps` to
clamp source files with unnecessarily short frame delays.

Keep repository-owned or appropriately licensed source material only. A GIF
that is part of a curated preset belongs in the plugin's `assets/` directory;
do not depend on an operator's runtime filesystem.

## Configure playback

Start `gif_animation` from the web UI or API. A direct configuration can select
an asset directory and filename:

```json
{
  "animation": "gif_animation",
  "config": {
    "gif_directory": "animation/plugins/gif_animation/assets",
    "gif_name": "my-clip.gif",
    "playback_speed": 1.0,
    "brightness": 0.8,
    "gamma": 1.0,
    "brightness_mode": "rgb",
    "flip_y": true
  }
}
```

Curated presets should use a repository-relative path and must pass the plugin's
preset render tests.

## Brightness and color

GIF frames contain RGB or palette-indexed color, not a separate hardware
brightness channel. The plugin scales decoded RGB values in software.

- `rgb` scales channels directly.
- `luma` separates intensity from chroma before applying its floor and global
  scaling.
- `gamma` applies nonlinear correction after brightness scaling.
