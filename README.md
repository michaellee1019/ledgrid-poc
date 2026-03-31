# LED Grid Control System

High-performance SPI-controlled LED grid with a web UI and hot-swappable animation plugins.

## Quick Start

1. Install dependencies:
   ```bash
   python3 -m venv .venv
   . .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Start the controller process (hardware):
   ```bash
   python3 scripts/start_server.py --mode controller
   ```

3. Start the web UI (in a second terminal):
   ```bash
   python3 scripts/start_server.py --mode web
   ```

4. Open the UI:
   - http://localhost:5000/

## Guided Calibration

1. In the web UI, start `Plant Calibration`.
2. No-tripod flow (default): set `manual_pattern_index=-1` so patterns auto-cycle while you shoot.
3. Recommended handsfree capture settings in the animation:
   - `pattern_hold_seconds=8`
   - `transition_seconds=1`
4. Run the guided workflow:
   ```bash
   .venv-web/bin/python scripts/calibrate.py --guided-capture --capture-mode handsfree --pattern-files auto --image-dir calibration_photos
   ```
5. Optional manual flow (if you can interact during capture):
   - set `manual_pattern_index` to `0..4` and use `--capture-mode manual`
6. If you only want the camera/capture checklist:
   ```bash
   .venv-web/bin/python scripts/calibrate.py --capture-guide-only --capture-mode handsfree --image-dir calibration_photos
   ```

## Documentation

- `docs/README.md` - system overview and setup details
- `docs/ANIMATION_SYSTEM.md` - animation plugins and API
- `docs/GIF_PIPELINE.md` - GIF asset preparation and playback plugin
- `docs/DEPLOYMENT.md` - Raspberry Pi deployment guide
- `docs/HARDWARE.md` - wiring and hardware notes
- `docs/ARCHITECTURE_DIAGRAM.md` - architecture and data flow
- `refactor.md` - refactor plan and checklist

## TODO

- 2026-03-30: https://github.com/pmarreck/printable-binary for frame data

