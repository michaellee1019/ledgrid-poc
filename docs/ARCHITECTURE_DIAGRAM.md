# LED Grid System Architecture

**Visual reference for system layers and data flow**

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         USER INTERFACE                          │
│                    (Browser / Mobile Device)                    │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP/REST
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                         WEB LAYER                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │   Flask App  │  │   REST API   │  │  Templates   │         │
│  │  web/app.py  │  │   Endpoints  │  │   (HTML)     │         │
│  └──────────────┘  └──────────────┘  └──────────────┘         │
└────────────────────────────┬────────────────────────────────────┘
                             │ File-based IPC
                             │ (control.json / status.json)
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                      ANIMATION FRAMEWORK                        │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │              Animation Manager                           │  │
│  │  - Plugin loading & lifecycle                            │  │
│  │  - Frame generation loop (40 FPS)                        │  │
│  │  - Parameter management                                  │  │
│  └────────────────────┬─────────────────────────────────────┘  │
│                       │                                         │
│  ┌────────────────────┴─────────────────────────────────────┐  │
│  │              Plugin Loader                               │  │
│  │  - Hot-reload animations                                 │  │
│  │  - Discover & validate plugins                           │  │
│  └────────────────────┬─────────────────────────────────────┘  │
│                       │                                         │
│  ┌────────────────────┴─────────────────────────────────────┐  │
│  │         Animation Plugins (10+ animations)               │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │  │
│  │  │ Rainbow  │ │ Sparkle  │ │  Emoji   │ │FluidTank │   │  │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘   │  │
│  │  Each implements: generate_frame() → List[(r,g,b)]      │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────┬────────────────────────────────────┘
                             │ set_all_pixels(colors)
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                        DRIVER LAYER                             │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │         MultiDeviceLEDController                         │  │
│  │  - Splits frame across 2 ESP32 devices                   │  │
│  │  - Parallel writes (threaded)                            │  │
│  │  - Aggregates statistics                                 │  │
│  └────────────┬──────────────────────────┬──────────────────┘  │
│               │                          │                      │
│  ┌────────────▼──────────┐  ┌───────────▼──────────┐          │
│  │  LEDController (CE0)  │  │  LEDController (CE1) │          │
│  │  - 8 strips × 138 LEDs│  │  - 8 strips × 138 LEDs│          │
│  │  - SPI Mode 0         │  │  - SPI Mode 0        │          │
│  │  - 20 MHz             │  │  - 20 MHz            │          │
│  └────────────┬──────────┘  └───────────┬──────────┘          │
└───────────────┼──────────────────────────┼─────────────────────┘
                │ SPI                      │ SPI
                │ /dev/spidev0.0           │ /dev/spidev0.1
                ▼                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                      HARDWARE LAYER                             │
│  ┌────────────────────────┐  ┌────────────────────────┐        │
│  │   ESP32 Device 0       │  │   ESP32 Device 1       │        │
│  │  ┌──────────────────┐  │  │  ┌──────────────────┐  │        │
│  │  │  SPI Slave (DMA) │  │  │  │  SPI Slave (DMA) │  │        │
│  │  │  - Mode 0        │  │  │  │  - Mode 0        │  │        │
│  │  │  - Command parser│  │  │  │  - Command parser│  │        │
│  │  └────────┬─────────┘  │  │  └────────┬─────────┘  │        │
│  │           │             │  │           │             │        │
│  │  ┌────────▼─────────┐  │  │  ┌────────▼─────────┐  │        │
│  │  │ ESP-IDF LCD/I80  │  │  │  │ ESP-IDF LCD/I80  │  │        │
│  │  │  - 8-lane DMA    │  │  │  │  - 8-lane DMA    │  │        │
│  │  └────────┬─────────┘  │  │  └────────┬─────────┘  │        │
│  └───────────┼────────────┘  └───────────┼────────────┘        │
│              │                            │                      │
│  ┌───────────▼────────────┐  ┌───────────▼────────────┐        │
│  │  8 LED Strips          │  │  8 LED Strips          │        │
│  │  (138 LEDs each)       │  │  (138 LEDs each)       │        │
│  │  = 1,104 LEDs          │  │  = 1,104 LEDs          │        │
│  └────────────────────────┘  └────────────────────────┘        │
│                                                                  │
│              Total: 16 strips × 138 LEDs = 2,208 LEDs          │
└─────────────────────────────────────────────────────────────────┘
```

---

## Data Flow: User Click → LED Update

```
1. User clicks "Start Rainbow" in browser
   │
   ▼
2. Browser sends POST /api/animation/start {"name": "rainbow"}
   │
   ▼
3. Flask app (web/app.py) receives request
   │
   ▼
4. Flask writes to ipc/control_channel.py
   │
   ▼
5. control_channel writes run_state/control.json
   {
     "command_id": 123456789.0,
     "action": "start_animation",
     "data": {"animation_name": "rainbow"}
   }
   │
   ▼
6. Controller process polls control.json (every 0.1s)
   │
   ▼
7. AnimationManager reads command
   │
   ▼
8. PluginLoader loads rainbow.py
   │
   ▼
9. RainbowAnimation instance created
   │
   ▼
10. Animation loop starts (40 FPS target)
    │
    ▼
11. Every 25ms: RainbowAnimation.generate_frame()
    │  Returns: [(r,g,b), (r,g,b), ...] × 2,208 LEDs
    ▼
12. AnimationManager calls controller.set_all_pixels(colors)
    │
    ▼
13. MultiDeviceLEDController splits frame:
    │  - Device 0: LEDs 0-1119
    │  - Device 1: LEDs 1120-2239
    ▼
14. Parallel threads send to both devices:
    │  Thread 1 → LEDController(CE0)
    │  Thread 2 → LEDController(CE1)
    ▼
15. Each LEDController sends SPI command:
    │  [CMD_SET_ALL, r0, g0, b0, r1, g1, b1, ...]
    │  [CMD_SHOW]
    ▼
16. ESP32 SPI slave receives via DMA
    │
    ▼
17. ESP32 parses command, updates LED buffer
    │
    ▼
18. LCD/I80 DMA outputs to 8 strips in parallel
    │
    ▼
19. LEDs light up! 🎉
    │
    ▼
20. Controller writes status to run_state/status.json
    {
      "is_running": true,
      "current_animation": "rainbow",
      "fps": 42.5,
      "frame_data_encoded": "..."
    }
    │
    ▼
21. Web UI polls status.json (every 0.5s)
    │
    ▼
22. Browser updates preview canvas
```

**Total Latency:** ~50-100ms (UI click → LED update)

---

## Process Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Raspberry Pi                             │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Process 1: Web Server (scripts/start_server.py)   │  │
│  │  - Flask app on port 5000                            │  │
│  │  - Serves HTML/CSS/JS                                │  │
│  │  - REST API endpoints                                │  │
│  │  - Preview rendering                                 │  │
│  │  - Writes: run_state/control.json                    │  │
│  │  - Reads:  run_state/status.json                     │  │
│  └──────────────────────────────────────────────────────┘  │
│                           │                                 │
│                           │ File-based IPC                  │
│                           │                                 │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Process 2: Controller (scripts/start_server.py)   │  │
│  │  - Animation loop (40 FPS)                           │  │
│  │  - Plugin management                                 │  │
│  │  - SPI communication                                 │  │
│  │  - Reads:  run_state/control.json                    │  │
│  │  - Writes: run_state/status.json                     │  │
│  └──────────────────────────────────────────────────────┘  │
│                           │                                 │
│                           │ SPI                             │
│                           ▼                                 │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  /dev/spidev0.0  ←→  ESP32 Device 0                  │  │
│  │  /dev/spidev0.1  ←→  ESP32 Device 1                  │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

**Why Two Processes?**
- Web UI can crash without affecting LED control
- Hardware control runs with real-time priority
- Easier to develop/debug web UI separately
- Can restart web UI without interrupting animations

---

## File System Layout

```
run_state/
├── control.json       # Commands: web → controller
├── status.json        # Status: controller → web
├── web.pid            # Web process ID
└── controller.pid     # Controller process ID

Web writes control.json:
  {"action": "start_animation", "data": {...}}

Controller reads control.json, executes command

Controller writes status.json:
  {"is_running": true, "fps": 42.5, "frame_data": "..."}

Web reads status.json, updates UI
```

---

## SPI Protocol

```
Raspberry Pi                    ESP32
    │                             │
    │  [CMD_SET_ALL, r0,g0,b0,    │
    │   r1,g1,b1, ... × 1120]     │
    ├────────────────────────────>│
    │                             │ Parse command
    │                             │ Update LED buffer
    │                             │
    │  [CMD_SHOW]                 │
    ├────────────────────────────>│
    │                             │ Queue latest complete frame
    │                             │ LCD/I80 DMA → 8 strips
    │                             │
    │  [CMD_GET_STATS]            │
    ├────────────────────────────>│
    │                             │
    │  <stats data>               │
    │<────────────────────────────┤
```

**Commands:**
- `0x01` SET_PIXEL - Set single pixel
- `0x02` SET_BRIGHTNESS - Global brightness
- `0x03` SHOW - Update display
- `0x04` CLEAR - Clear all LEDs
- `0x05` SET_RANGE - Set pixel range
- `0x06` SET_ALL - Set all pixels (most efficient)
- `0x07` CONFIG - Configure strips/LEDs
- `0xFF` PING - Test connection

---

**Last Updated:** 2025-12-25
