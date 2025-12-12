# ASCII Drop Animation

A new LED animation that drops ASCII characters like Tetris pieces, inspired by the fluid tank animation but completely reimagined for character-based visuals.

## Features

✅ **Character Support**: A-Z, 0-9, underscore (_), and space  
✅ **5×7 Pixel Bitmaps**: Hand-crafted character patterns for clear visibility  
✅ **Tetris-like Physics**: Characters fall from random positions at the top  
✅ **Configurable Phrase**: Set any phrase to drop (e.g., "HELLO WORLD")  
✅ **1px Buffer**: Characters maintain spacing as requested  
✅ **Auto Screen Clear**: Clears when 80% full to start fresh  
✅ **No Rotation**: Characters maintain orientation (as requested)  
✅ **Collision Detection**: Characters stack properly on landing  

## Configuration Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `phrase` | string | "HELLO WORLD" | Text to drop (A-Z, 0-9, _, space) |
| `drop_speed` | float | 2.0 | Speed of falling characters |
| `spawn_rate` | float | 1.5 | Characters spawned per second |
| `character_color_red` | int | 0 | Character red component (0-255) |
| `character_color_green` | int | 255 | Character green component (0-255) |
| `character_color_blue` | int | 100 | Character blue component (0-255) |
| `background_red` | int | 0 | Background red component (0-255) |
| `background_green` | int | 0 | Background green component (0-255) |
| `background_blue` | int | 5 | Background blue component (0-255) |
| `serpentine` | bool | false | Flip alternate strips for serpentine wiring |

## Character Bitmaps

Each character is defined as a 5×7 pixel bitmap using 'X' for lit pixels and '.' for empty pixels:

```
Example 'A':
.XXX.
X...X
X...X
XXXXX
X...X
X...X
.....
```

All 38 characters (A-Z, 0-9, _, space) are included with carefully designed bitmaps.

## Animation Behavior

1. **Spawning**: Characters from the phrase spawn at random X positions at the top
2. **Falling**: Characters drop down at configurable speed with gravity-like physics  
3. **Landing**: Characters stop when they hit the bottom or collide with existing characters
4. **Stacking**: Characters stack on top of each other maintaining 1px spacing
5. **Clearing**: When screen reaches 80% capacity, it clears completely and restarts

## Integration

- ✅ Added to `animation_manager.py` ALLOWED_PLUGINS list
- ✅ Follows AnimationBase interface for seamless integration
- ✅ Compatible with web interface and parameter controls
- ✅ Supports all standard animation features (brightness, serpentine, etc.)

## Files Created

- `animations/ascii_drop.py` - Main animation implementation
- `ASCII_DROP_ANIMATION.md` - This documentation

## Usage

The animation can be selected through the web interface or programmatically:

```python
from animations.ascii_drop import AsciiDropAnimation
from led_controller_spi import LEDController

controller = LEDController()
config = {
    'phrase': 'CUSTOM TEXT',
    'drop_speed': 3.0,
    'character_color_green': 255
}
animation = AsciiDropAnimation(controller, config)
```

The animation provides a unique visual effect that's completely different from the water-based fluid tank animation while maintaining the same high-quality, physics-based approach.
