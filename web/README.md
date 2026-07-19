# Web Layer

Purpose: Flask UI and REST API for controlling animations.

Key files:
- app.py: Flask app setup and route registration
- templates/: HTML templates for the UI

Notes:
- Requests are forwarded to the controller via ipc/control_channel.py.
- Keep UI-specific logic here; avoid embedding animation logic in routes.

API endpoints:
- GET /api/animations
- GET /api/animations/<animation_name>
- POST /api/start/<animation_name>
- POST /api/stop
- GET /api/status
- GET /api/stats
- GET /api/metrics
- GET /api/hardware/stats
- `POST /api/hole` — random hole with `{}`, or positioned hole with `{"x": 7.5, "y": 42, "radius": 1.5}`
- GET /api/frame
- GET /api/preview/<animation_name>
- POST /api/preview/<animation_name>/with_params
- POST /api/parameters
- POST /api/painter/updates
- POST /api/painter/frame
- POST /api/painter/clear
- GET /api/painter/presets
- GET /api/painter/presets/<preset_id>
- POST /api/painter/presets
- GET /api/animations/<animation_name>/presets
- GET /api/animations/<animation_name>/presets/<preset_id>
- POST /api/animations/<animation_name>/presets
- POST /api/animations/<animation_name>/presets/<preset_id>/apply
- DELETE /api/animations/<animation_name>/presets/<preset_id>
- POST /api/reload/<animation_name>
- POST /api/refresh
