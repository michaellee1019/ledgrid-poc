# Composer web layer

Flask serves the Composer shell at `/` and `/composer`. The browser renders
previews while connected and submits validated Scene edits as they happen.
Opening the shell loads the current editing context. It does not start playback.
The wall controller owns physical rendering and playback. A request, controller
playback, and receiver connectivity are separate observations.

`web/app.py` registers the HTTP routes. `web/templates/composer.html` serves the
shell and `web/static/js/composer_slice.js` handles editing. Run
`just browser-composer-assets` to build the generated catalog, Python preview
runtime, fixed wall calibration asset, and gallery thumbnails. The service worker
endpoints retire caches from older installed clients.

## Current endpoints

| Path | Purpose |
| --- | --- |
| `GET /api/composer/status`, `GET /api/composer/recovery` | Read playback/editing status and recover the current Scene. |
| `POST /api/composer/preview`, `POST /api/composer/check` | Preview the Scene and run advisory diagnostics. |
| `POST /api/composer/scene`, `POST /api/composer/stop` | Submit a Scene and request output stop. |
| `/api/composer/looks`, `/api/composer/library`, `/api/composer/playlists` | Manage saved Looks, library state, and playlists. |
| `GET /api/v1/composer/bootstrap`, `GET /api/v1/components` | Read catalog and browser runtime capabilities. |
| `PUT /api/v1/scene`, `DELETE /api/v1/scene` | Queue a host Scene start or stop and return a correlated request. |
| `GET /api/v1/composer/operations/status`, `GET /api/v1/composer/operations/telemetry` | Observe controller playback, receiver connectivity, and diagnostics. |
| `GET /api/v1/composer/settings/observed` | Read revision-qualified output settings. |
| `POST /api/config/power`, `POST /api/config/brightness`, `POST /api/config/animation-speed` | Set output power, master brightness, or wall speed. Turning on resumes the selected Scene. |
| `/play/tetris`, `POST /api/v1/tetris/play` | Show the Play page and request a player round with a connected Nimbus. |

Home Assistant can guard power, brightness, and animation-speed mutations with
`expected_controller_session_id`, `expected_controller_state_revision`, and
`expires_at` (Unix seconds). The controller checks the guard before mutation.
To verify an applied change, poll observed settings for the same session, a
newer revision, the matching `last_applied_command_id`, and the requested
value. Read observed settings after reconnect before accepting new input.

See the [Composer product contract](../docs/CURRENT_UX_ACCEPTANCE.md),
[validation](../docs/CURRENT_COMPOSER_VALIDATION.md), and
[Nimbus controls](../docs/DEPLOYMENT.md#nimbus-gamepad).
