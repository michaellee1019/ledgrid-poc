# Web Layer

Purpose: Flask UI and REST API for controlling animations.

Key files:
- app.py: Flask app setup and route registration
- templates/: HTML templates for the UI

Notes:
- Requests are forwarded to the controller via ipc/control_channel.py.
- Keep UI-specific logic here; avoid embedding animation logic in routes.
- Rebuild the checked-in browser runtime assets with
  `just browser-composer-assets`. The first Python preview on a device downloads
  the pinned Pyodide runtime; rendering, Clock compositing, and checking then stay
  inside Web Workers. The browser bundle contains the complete valid Python
  animation catalog and its curated assets. Receiver-native C++ previews use the
  compact checked-in Wasm modules. That lane covers both the managed Aurora
  pilot and the feature-gated firmware builtin `compiled_rainbow`; each is
  built from its authoritative C++ source and can be composed with the Python
  Clock overlay entirely in the browser.

API endpoints:
- `GET /composer` — installable browser-Wasm preset composer; opening it is
  private and non-mutating
- `GET /api/v1/composer/bootstrap` — provider-qualified schemas, full authored
  preset parameters, geometry, and explicit browser runtime capabilities
- `GET /api/v1/composer/connectivity` — uncached server reachability probe
- `POST /api/v1/composer/presets/validate` — read-only component or composed-scene
  JSON validation
- `POST /api/v1/composer/presets` — save a provider-qualified component preset
  without changing live playback
- `GET /composer-service-worker.js` — root-scoped offline shell worker
- GET /api/animations
- `GET /api/v1/components` — unified descriptor catalog, filterable by provider
  and role

- `GET|POST|PUT|DELETE /api/v1/scene` — read, start, or stop the fixed scene
- `POST /api/v1/scene/validate` — validate without changing live output
- `PATCH /api/v1/scene/components/<target>` — targeted background or
  `clock_overlay` update
- `POST /api/v1/scene/preview` — isolated scene preview using the selected vibe
  and plant state
- `GET|POST /api/v1/scene-presets` — list or save scene-only presets
- `GET|DELETE /api/v1/scene-presets/<preset_id>` — inspect or delete a scene preset
- `POST /api/v1/scene-presets/<preset_id>/apply` — start a saved scene without
  changing the independent vibe
- GET /api/animations/<animation_name>
- POST /api/start/<animation_name>
- POST /api/stop
- `POST /api/device/state` — atomically apply `power`, hardware `brightness`,
  `animation`, and optional `preset`
- GET /api/status
- GET /api/stats
- GET /api/metrics
- GET /api/hardware/stats
- `POST /api/config/brightness` — set the receiver-wide 0–255 output brightness
- `POST /api/hole` — random hole with `{}`, or positioned hole with `{"x": 7.5, "y": 42, "radius": 1.5}`
- `POST /api/interaction` — primary interaction for the live animation with `{"kind":"primary","x":7.5,"y":42,"strength":1}`
- GET /api/frame
- GET /api/preview/<animation_name>
- POST /api/preview/<animation_name>/with_params
- POST /api/preview/<animation_name>/interaction
- POST /api/parameters
- POST /api/painter/updates
- POST /api/painter/frame
- POST /api/painter/clear
- GET /api/painter/masks
- POST /api/painter/masks
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
