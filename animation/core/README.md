# Animation Core

Purpose: shared animation framework pieces used by all plugins.

Key files:
- base.py: AnimationBase, RenderedFrame, reusable frame buffers, and StatefulAnimationBase
- manager.py: AnimationManager lifecycle and timing loop
- plugin_loader.py: Discovery and hot-reload of plugins

Plugin expectations:
- Implement generate_frame(time_elapsed, frame_count).
- Return a C-contiguous uint8 NumPy frame shaped `(controller.total_leds, 3)`.
- Use `next_frame_buffer()` for dynamic output and `RenderedFrame` hints for unchanged or sparse frames.
- Keep plugin-specific state inside the class instance.
