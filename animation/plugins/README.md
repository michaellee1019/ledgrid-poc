# Animation Plugins

Purpose: concrete animation implementations loaded by the plugin system.

Guidelines:
- Each file defines at least one AnimationBase subclass.
- Set ANIMATION_NAME and ANIMATION_DESCRIPTION for UI display.
- Avoid hardware calls directly; use the provided controller.
- Prefer deterministic output for easier debugging.
