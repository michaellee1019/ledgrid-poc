# Composer Web Layer

Purpose: Flask serves Composer, the sole browser product, and its bounded REST
surface. There are no alternate browser control surfaces.

Key files:
- app.py: Flask app setup and route registration
- templates/: HTML templates for the UI

Notes:
- Requests are forwarded to the controller via ipc/control_channel.py.
- Keep UI-specific logic here; avoid embedding animation logic in routes.
- Rebuild the checked-in browser runtime assets with
  `just browser-composer-assets`. This generates a versioned static bootstrap,
  an immutable content-addressed installation profile, the Python runtime
  archive, native Wasm previews, and the offline manifest. Composer loads that
  bundled catalog before probing for a wall server, so rendering, parameter and
  timeline editing, local Check, autosave, import, and export need no controller
  or running animation. The first Python preview on a device downloads the
  pinned Pyodide runtime; “Prepare for offline use” recaches and verifies every
  exact runtime resource observed by the worker. Rendering, Clock compositing,
  and checking stay inside Web Workers. Receiver-native C++ previews cover both
  the managed Aurora pilot and feature-gated firmware builtin
  `compiled_rainbow`; each is built from its authoritative C++ source and can
  be composed with the Python Clock overlay entirely in the browser.
- Reaching a wall server refreshes only its advertised capabilities. Composer
  does not read current wall settings or the managed mask draft until the
  operator explicitly chooses the corresponding Wall action. Shared-library
  save, wall settings, server qualification, activation, observation,
  cancellation, and rollback remain server-gated and exact-digest-bound.

Browser entry points:

- `GET /` — redirects to the canonical Composer URL
- `GET /composer` — installable browser-Wasm preset composer; opening it is
  private and non-mutating
- `GET /static/generated/composer/bootstrap.v1.json` — generated offline-first
  provider-qualified renderer catalog, geometry, and bundled profile identity
- `GET /api/v1/composer/bootstrap?catalog_only=1` — optional read-only server
  capability and component-identity refresh without observing live wall state
- `GET /api/v1/composer/bootstrap` — full server bootstrap that may include the
  selected managed profile for an explicit server workflow
- `GET /api/v1/composer/connectivity` — uncached server reachability probe
- `POST /api/v1/composer/presets/validate` — read-only component or composed-scene
  JSON validation
- `POST /api/v1/composer/presets` — save a provider-qualified component preset
  without changing live playback
- `GET /composer-service-worker.js` — root-scoped offline shell worker
- `GET /api/v1/components` — unified descriptor catalog, filterable by provider
  and role

- `GET /api/v1/scene` — read the fixed scene draft/state contract
- `POST /api/v1/scene/checks` — issue one short-lived, exact development/canary
  activation Check; disabled by default
- `POST|PUT /api/v1/scene` — submit the exact checked activation and receive a
  pending resource; raw tokens never enter IPC
- `GET|DELETE /api/v1/scene/activations/<activation_id>` — read correlated
  status or request cancellation
- `POST /api/v1/scene/activations/<activation_id>/rollback` — request a
  correlated exact-snapshot rollback when advertised
- `POST /api/v1/scene/validate` — validate without changing live output
- `PATCH /api/v1/scene/components/<target>` — rejected with 428 by default;
  Composer live edit may submit `live_edit: true` with an exact expected active
  component to stream validated parameter updates without replacing the scene
- `GET|POST /api/v1/scene-presets` — list or save scene-only presets
- `GET|DELETE /api/v1/scene-presets/<preset_id>` — inspect or delete a scene preset
- `POST /api/v1/scene-presets/<preset_id>/apply` — rejected with 428; load the
  draft and use Composer Check
- `POST /api/stop` — request a stop; returned and observed status remains the
  authority for output state
- `GET /api/v1/composer/settings/observed` — bounded revision-qualified
  settings observation for Composer reconciliation
- `GET /api/v1/composer/operations/status` — bounded operational health
- `GET /api/v1/composer/operations/telemetry` — deployment, receiver-diagnostic,
  and qualification telemetry
- `POST /api/config/brightness` — set the receiver-wide 0–255 output brightness
- `POST /api/config/target-fps`, `/api/config/animation-speed`, and
  `/api/config/plant-modifiers` — bounded Composer wall-setting updates
