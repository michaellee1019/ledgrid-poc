# Web Layer

Purpose: Flask UI and REST API for controlling animations.

Key files:
- app.py: Flask app setup and route registration
- templates/: HTML templates for the UI

Notes:
- Requests are forwarded to the controller via ipc/control_channel.py.
- Keep UI-specific logic here; avoid embedding animation logic in routes.
