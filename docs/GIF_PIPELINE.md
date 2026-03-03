# GIF Pipeline

## Goal
Play animated GIF files as LED wall animations using the `gif_animation` plugin.

## 1) Prepare assets
Normalize source GIFs to the wall layout (default `32x140`) and place outputs in `assets/gifs`:

```bash
python3 scripts/prepare_gif_assets.py \
  --input-dir /path/to/source-gifs \
  --output-dir assets/gifs \
  --width 32 \
  --height 140 \
  --fit-mode stretch \
  --overwrite
```

Notes:
- `fit-mode`:
  - `stretch`: force exact size, no letterboxing.
  - `contain`: preserve aspect ratio with padding.
  - `cover`: preserve aspect ratio and crop.
- `--max-fps` can clamp very fast GIF frame timings.

## 2) Start plugin
Start `gif_animation` from the web UI or API.

Example config payload:

```json
{
  "animation": "gif_animation",
  "config": {
    "gif_directory": "assets/gifs",
    "gif_name": "my_clip.gif",
    "playback_speed": 1.0,
    "brightness": 0.8,
    "gamma": 1.0,
    "brightness_mode": "rgb",
    "flip_y": true
  }
}
```

## Brightness and color model
- GIF stores RGB (palette-indexed), not a separate hardware brightness channel.
- The plugin applies brightness in software by scaling each pixel's RGB output.
- `brightness_mode` options:
  - `rgb`: scale channels directly.
  - `luma`: separate per-pixel intensity from chroma, then apply floor/global scaling.
- `gamma` applies nonlinear correction after brightness scaling.
