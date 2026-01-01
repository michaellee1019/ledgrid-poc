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

## Documentation

- `docs/README.md` - system overview and setup details
- `docs/ANIMATION_SYSTEM.md` - animation plugins and API
- `docs/DEPLOYMENT.md` - Raspberry Pi deployment guide
- `docs/HARDWARE.md` - wiring and hardware notes
- `docs/ARCHITECTURE_DIAGRAM.md` - architecture and data flow
- `refactor.md` - refactor plan and checklist
