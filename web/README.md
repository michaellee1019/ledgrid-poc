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
- POST /api/hole
- GET /api/frame
- GET /api/preview/<animation_name>
- POST /api/preview/<animation_name>/with_params
- POST /api/parameters
- POST /api/upload
- POST /api/reload/<animation_name>
- POST /api/refresh
