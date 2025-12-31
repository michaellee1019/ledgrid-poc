# Animation Core

Purpose: shared animation framework pieces used by all plugins.

Key files:
- base.py: AnimationBase and StatefulAnimationBase interfaces
- manager.py: AnimationManager lifecycle and timing loop
- plugin_loader.py: Discovery and hot-reload of plugins

Plugin expectations:
- Implement generate_frame(time_elapsed, frame_count).
- Return a full frame list sized to controller.total_leds.
- Keep plugin-specific state inside the class instance.
