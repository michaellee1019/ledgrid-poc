# Animation Plugin Compatibility Inventory

Inventory schema: `2`.

This compatibility inventory is generated from the shipped `manifest.json` files,
the production plugin loader, and each loaded concrete class. It describes
compatibility with the current fixed host background-plus-overlay stack; it is
not the future unified component descriptor catalog.

Regenerate with:

```bash
uv run --with numpy --with pillow tools/generate_animation_compatibility_inventory.py
uv run --with numpy --with pillow tools/generate_animation_compatibility_inventory.py --check
```

## Classification rules

- `ordinary_background`: a concrete frame renderer with no direct controller
  mutation. The compatibility adapter treats it as a Python background.
- `compatibility_full_scene`: a deliberate Phase 1 exception that owns a
  complete authored scene. The existing `clock` stays here for preset and
  command compatibility while `clock_overlay` supplies composition.
- `python_overlay`: an explicit Python overlay that returns premultiplied
  RGBA8 and is accepted only by the manager-owned composition path.
- `unsupported_direct_hardware_stateful`: a `StatefulAnimationBase` subclass
  or a class that calls a controller mutation method directly. It cannot join
  composition without conversion to the manager-owned frame contract.

Reads such as `controller.total_leds` are ordinary geometry access, not
hardware ownership. The scanner conservatively records direct mutations in
the concrete class; inherited manager/base presentation is outside plugin code.

## Summary

| Classification | Count |
| --- | ---: |
| `ordinary_background` | 48 |
| `compatibility_full_scene` | 1 |
| `python_overlay` | 1 |
| `unsupported_direct_hardware_stateful` | 0 |
| **Total shipped packages** | **50** |

## Shipped packages

| Plugin ID | Concrete class | Gallery | Compatibility | Evidence |
| --- | --- | --- | --- | --- |
| `ascii_drop` | `AsciiDropAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `aurora_curtains` | `AuroraCurtainsAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `canopy_cup` | `CanopyCupAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `cellular_tapestry` | `CellularTapestryAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `christmas_tree` | `ChristmasTreeAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `circadian_window` | `CircadianWindowAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `clock` | `ClockAnimation` | `show` | `compatibility_full_scene` | Owns both the clock face and its opaque authored background. |
| `clock_overlay` | `ClockOverlayAnimation` | `test` | `python_overlay` | Explicit Python overlay manifest; returns premultiplied RGBA8 through the manager-owned composition path. |
| `cloud_canyon` | `CloudCanyonAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `conway_life` | `ConwayLifeAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `cyclic_reef` | `CyclicReefAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `desert_wind` | `DesertWindAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `emoji` | `EmojiAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `firefly_synchrony` | `FireflySynchronyAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `fireworks` | `FireworksAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `flame_burst` | `FlameBurstAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `flow_field_silk` | `FlowFieldSilkAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `fluid_tank` | `FluidTankAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `frostwork` | `FrostworkAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `gif_animation` | `GifAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `gradient` | `GradientAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `lava_lamp` | `LavaLampAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `living_ecosystem` | `LivingEcosystemAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `living_stained_glass` | `LivingStainedGlassAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `maze_chase` | `MazeChaseAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `moonlit_fog_banks` | `MoonlitFogBanksAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `night_train_windows` | `NightTrainWindowsAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `physarum_network` | `PhysarumNetworkAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `pinball` | `PinballAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `pixel_chase` | `PixelChaseAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `pixel_quest` | `PixelQuestAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `plant_calibration` | `PlantCalibrationAnimation` | `test` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `plant_glow` | `PlantGlowAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `plant_mask_highlight` | `PlantMaskHighlightAnimation` | `test` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `quasicrystal_bloom` | `QuasicrystalBloomAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `rain_on_glass` | `RainOnGlassAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `rainbow` | `RainbowAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `reaction_diffusion_garden` | `ReactionDiffusionGardenAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `simple_test` | `SimpleTestAnimation` | `test` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `snake` | `SnakeAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `solid` | `SolidColorAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `sparkle` | `SparkleAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `spiral_single` | `SpiralSingleAnimation` | `test` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `strip_order` | `StripOrderAnimation` | `test` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `tetris` | `TetrisAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `tidal_bioluminescence` | `TidalBioluminescenceAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `waterfall_veil` | `WaterfallVeilAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `wave` | `WaveAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `wind_in_the_reeds` | `WindInTheReedsAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |
| `world_flags` | `WorldFlagsAnimation` | `show` | `ordinary_background` | Concrete AnimationBase renderer; no direct controller mutation. |

## Current conclusion

Every shipped package has exactly one classification. The current tree has no
stateful or direct-hardware plugin package. Existing opaque renderers retain the
ordinary Python-background compatibility path, the original Clock remains the
sole compatibility full scene, and explicit overlay packages enter only through
manager-owned composition. This is not permission to make backgrounds transparent
or to infer overlay semantics from black RGB.
