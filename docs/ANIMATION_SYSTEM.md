# LED Grid Animation System

A plugin-based animation system with web interface for hot-swapping animations over the air.

## Features

- 🎨 **Plugin-based animations** - Easy to create and modify
- 🌐 **Web interface** - Control animations from any device
- 🔄 **Hot-swapping** - Upload and switch animations without restart
- ⚡ **Real-time parameters** - Adjust animation settings live
- 📊 **Performance monitoring** - FPS tracking and system status
- 🎯 **High performance** - Optimized for 50+ FPS

## Quick Start

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Start the animation server:**
   ```bash
   python scripts/start_server.py
   ```

3. **Open web interface:**
   - Dashboard: http://localhost:5000/
   - Control Panel: http://localhost:5000/control
   - Upload: http://localhost:5000/upload

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Web Interface │    │   Animation      │    │   LED           │
│   (Flask)       │───▶│   Manager        │───▶│   Controller    │
│                 │    │                  │    │   (SPI)         │
└─────────────────┘    └──────────────────┘    └─────────────────┘
        │                        │                        │
        ▼                        ▼                        ▼
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Plugin        │    │   Animation      │    │   ESP32/SCORPIO │
│   Loader        │    │   Plugins        │    │   Hardware      │
└─────────────────┘    └──────────────────┘    └─────────────────┘
```

## Creating Animations

### Basic Animation Structure

```python
#!/usr/bin/env python3
from typing import Dict, Any
import numpy as np
from animation import AnimationBase

class MyAnimation(AnimationBase):
    ANIMATION_NAME = "My Animation"
    ANIMATION_DESCRIPTION = "What this animation does"
    ANIMATION_AUTHOR = "Your Name"
    ANIMATION_VERSION = "1.0"
    
    def generate_frame(self, time_elapsed: float, frame_count: int) -> np.ndarray:
        """Generate a frame of animation"""
        frame = self.next_frame_buffer(clear=False)
        frame[:] = (255, 0, 0)
        return frame
```

### Adding Parameters

```python
def get_parameter_schema(self) -> Dict[str, Dict[str, Any]]:
    schema = super().get_parameter_schema()
    schema.update({
        'speed': {
            'type': 'float',
            'min': 0.1,
            'max': 5.0,
            'default': 1.0,
            'description': 'Animation speed'
        },
        'color': {
            'type': 'int',
            'min': 0,
            'max': 255,
            'default': 255,
            'description': 'Red component'
        }
    })
    return schema

def generate_frame(self, time_elapsed: float, frame_count: int) -> np.ndarray:
    speed = self.params.get('speed', 1.0)
    red = self.params.get('color', 255)
    # Use parameters in your animation...
```

## Example Animations

The system comes with several example animations:

### Rainbow (`animation/plugins/rainbow.py`)
- **RainbowAnimation**: Classic rainbow cycle
- **RainbowWaveAnimation**: Rainbow wave effect

### Solid Colors (`animation/plugins/solid.py`)
- **SolidColorAnimation**: Solid color with breathing effect
- **GradientAnimation**: Color gradients

### Effects (`animation/plugins/effects.py`)
- **SparkleAnimation**: Random sparkle effect
- **WaveAnimation**: Sine wave patterns

## Web Interface

### Dashboard (`/`)
- View available animations
- System status and performance
- Quick animation start

### Control Panel (`/control`)
- Real-time parameter adjustment
- Animation switching
- Keyboard shortcuts

### Upload (`/upload`)
- Upload Python animation files
- Create animations with code editor
- Animation templates and guidelines

## API Endpoints

- `GET /api/animations` - List available animations
- `POST /api/start/<name>` - Start animation
- `POST /api/stop` - Stop current animation
- `GET /api/status` - Get system status
- `POST /api/parameters` - Update animation parameters
- `POST /api/upload` - Upload new animation
- `POST /api/refresh` - Refresh plugin list

## Configuration

### Command Line Options

```bash
python scripts/start_server.py --help
```

Key options:
- `--host 0.0.0.0` - Bind to all interfaces
- `--port 5000` - Web server port
- `--strips 8` - Number of LED strips
- `--leds-per-strip 140` - LEDs per strip
- `--spi-speed 10000000` - SPI communication speed
- `--target-fps 40` - Animation frame rate
- `--animation-speed-scale 0.2` - Multiplier applied to animation speed parameters (lower = slower motion)

### Hardware Configuration

The system supports:
- **ESP32-S3** via SPI (recommended for high performance)
- **RP2040 SCORPIO** via SPI (8 parallel outputs)

See `HARDWARE.md` for connection details.

## Performance Tips

1. **Canonical frames** - Return a C-contiguous `(total_leds, 3)` `np.uint8` array.
2. **Reuse buffers** - Render into `next_frame_buffer()` rather than allocating per frame.
3. **Source-rate output** - Wrap cached frames with `rendered_frame(..., changed=False)` when nothing changed.
4. **Time-based motion** - Derive motion from `time_elapsed`, not achieved frame count.
5. **Cache geometry** - Precompute coordinate grids, masks, palettes, and static layers.
6. **Sparse hints** - Supply half-open `dirty_ranges` when only a small part of the frame changed.

## Troubleshooting

### Common Issues

1. **No animations showing**
   - Check `animation/plugins/` directory exists
   - Verify Python syntax in animation files
   - Check web console for errors

2. **Low FPS**
   - Reduce animation complexity
   - Lower target FPS
   - Check SPI speed settings

3. **Parameter updates not working**
   - Ensure animation implements `get_parameter_schema()`
   - Check parameter types match schema
   - Verify real-time updates in `generate_frame()`

### Debug Mode

Enable debug output:
```bash
python scripts/start_server.py --debug --controller-debug
```

This provides detailed logging for troubleshooting.
