# Guarded automation API v1

This local HTTP API uses the current host Scene v2/controller command queue. It
adds no renderer, receiver transaction, startup write or automatic write retry.
Controller completion confirms controller execution; it does not prove that all
five receivers physically displayed an exact frame.

| Request | Result |
| --- | --- |
| `GET /api/v1/automation/capabilities` | Installation identity, API version, supported commands, control bounds and Composer link |
| `GET /api/v1/automation/catalog` | Current component descriptors, authored presets, saved Look summaries and playlist summaries; explicit preset diagnostics |
| `GET /api/v1/automation/status` | Freshness, session/revisions, observed settings, requested scene, controller playback, receiver connectivity and playlist progress |
| `POST /api/v1/automation/commands` | Validate and queue one guarded command; HTTP 202 means accepted |
| `GET /api/v1/automation/commands/{request_id}?wait_seconds=2` | Correlated accepted/completed/failed result and refreshed controller status; wait is bounded to 0..2 seconds |

All responses carry `schema` and `schema_version: 1`; command-validation errors
carry `error` and `code`. Responses are `Cache-Control: no-store`. Setup and
status/catalog reads never activate output. Routine status excludes frames,
parameter blobs, catalog data and detailed diagnostic counters.

`installation_id` is a canonical UUID stored in personal data at
`$LEDGRID_DATA_DIR/run_state/installation.json` (or the project `run_state`
directory for local use). It survives application replacement, process restart
and URL changes. Back it up alongside the other personal data. Corruption fails
explicitly; the API never silently generates a replacement identity.
`server_session_id` changes on each web-process restart. Controller session and
revision come from the controller, independently of that web session.

Refresh status immediately before each new intent. Require `fresh: true` (a
controller observation no more than five seconds old). Copy its web session,
controller session and revision into a command, and choose a Unix expiry no more
than ten seconds in the future:

```json
{
  "action": "set_device_state",
  "brightness": 0,
  "pace": 1.25,
  "expected_server_session_id": "<status.server_session_id>",
  "expected_controller_session_id": "<status.controller_session_id>",
  "expected_controller_state_revision": 7,
  "expires_at": 1792166410.0
}
```

`set_device_state` accepts any nonempty subset of `power` (boolean), `brightness`
(integer 0..255) and `pace` (0.1..25x). Omitted fields stay unchanged. Zero is a
real master-brightness setting and does not imply power off. Pace is relative to
the existing controller speed baseline, 0.3. Brightness is applied before power
on. Power on resumes the selected scene, retaining its Scene fields, widgets,
plant settings, palette and authored brightness; without a selected scene it
fails explicitly. Power off stops output and cancels playlist ownership through
the existing controller revision mechanism. The physical mains outlet is a
separate device.

The reply includes `state: accepted`, `request_id`, `command_id` and `result_url`.
Read that URL until the correlated result is completed or failed, with a client
wall-clock bound. A completed result includes its controller session and
resulting revision plus current status. The controller publishes state before
exposing its result. A newer command can already have changed that current
status, so the completion revision and current status revision remain separate.

Never repeat a POST after a timeout, conflict, disconnect or restart. A timeout
is uncertain execution, not rollback; refresh state before making a new user
intent. The controller checks session, revision and expiry again at execution,
so an intervening command rejects the older queued intent. Writes using an old
web session fail as conflicts. Completed requests are retained in a bounded
256-record web-session history; after web restart an old request returns 404,
and no intent is replayed. Existing queued commands accepted before a web
restart may finish within their original guard; the web process never resends
them. Controller startup rejects leftover queued commands.

HTTP 409 reports a session/revision conflict, 428 a missing complete guard, 503
stale controller observations, disabled activation or full pending history, and
400 invalid input. Invalid correlated results report 502. Clients must not infer
command completion from matching brightness alone.

Status has nested `settings` (`power`, `brightness`, `pace`,
`animation_speed_scale`, `target_fps`), `controller_playback`,
`receiver_connectivity` and `playlist`. Top-level `is_running`, `brightness`,
`animation_speed_scale`, `scene_digest`, `active_identity`, `last_command_id`
and `last_applied_command_id` project the current observation for existing HA
consumers. Stale output power/running is `null` and playback state is
`unavailable`. Receiver connectivity is independent: receiver 3's accepted
counter degradation does not alone make the controller unavailable.

## Shared Scene publication helper

Content and advanced actions reuse `AutomationAPI.publish_scene`, passing
a complete current Scene v2 and the same guards plus
`expected_composer_revision` from status. It normalizes through the existing
Composer catalog and queues `start_scene` once, preserving master brightness
and other global settings. A successful correlated result is required before
`result()` reconciles the exact scene into the existing Composer live state and
atomic recovery store. This local reconciliation never sends another wall
command. The content HTTP action uses this helper; arbitrary Scene submission is not advertised by this API.

If Composer changed after acceptance, reconciliation reports `conflict` and
preserves the newer editor/recovery state. This includes Save As, Save, opened
Look updates/deletion, recovery discard and cursor hydration even when the
Scene publication revision stays unchanged. Recovery/cursor writes and
reconciliation share one lock and a separate recovery generation; the response still reports the true
controller execution result separately. A different controller scene/session or
stale controller observation also prevents reconciliation. Callers must surface
that reconciliation result, refresh and resolve new intent explicitly. Result
polling is required; there is no background retry or scene restoration task.


## Content commands and observed selection

`GET /catalog` adds `content`, a list of records with `id`, `kind`, `name`,
`label`, `supported` and optional `error`. It includes every current Animation,
checked authored/user component preset, saved Look and saved playlist.
Unsupported saved references are retained with diagnostics. Stable IDs are
`animation:python:<component>:v<version>`,
`preset:python:<component>:<preset_id>`, `look:<UUID>` and `playlist:<UUID>`.
Labels include IDs to disambiguate equal names; renames preserve IDs.

`POST /commands` accepts `action: play_content` with `content_id`, or
`action: stop_playlist` with the observed `run_id`. Both require the existing
web/controller session, revision and expiry guards plus
`expected_composer_revision` from fresh status. The common accepted/result
receipt contract applies. Resolve against the current store at dispatch,
never a cached name. Missing/unsupported content fails explicitly. A nonempty
unsupported current scene also fails instead of substituting a base scene.
An initial explicit animation/preset choice without a selected scene starts
from the existing complete starter scaffold and replaces the chosen component.

Animation/preset publication and complete saved Looks start playback while
preserving master brightness and other global settings. Scene fields outside
the requested component/preset effects remain unchanged. A sole matching
Widget keeps its ID and placement; adding a missing clock uses the stable
`automation-clock_overlay` ID. Multiple matching Widgets fail as ambiguous.

Status adds `active_content: {animation, preset, look, playlist}` with stable
IDs or null. Scene matches come from actual controller Scene v2 bytes,
parameters/effects and identity, never accepted intent. Ambiguous matches are
null. `playlist` also exposes immutable current/next entry summaries, phase,
progress, errors and reconciliation status.

Playlist commands use the existing immutable file queue and controller runner.
New API commands include an execution expiry for start and stop. A guarded
stop advances the controller ordering revision while retaining current Scene
playback. Existing Composer queue clients remain supported. Starting another
playlist while one runs is rejected with a stop-current-first error, preserving
the current run and reconciliation context. A second start awaiting controller
acknowledgement is also rejected before queueing or replacing that context. Accepted starts are not renewed or
replayed by HA. Observed advances from an accepted run reconcile into Composer
and recovery while its captured editor/recovery generation remains current;
a newer editor change blocks further reconciliation with a visible conflict.
No alternate scheduler or playlist transition subsystem is added.

## Advanced Scene operations

Capabilities advertise `patch_scene`, `set_target_fps` and `interact`. Status
adds a compact `scene_controls` projection: selected canonical digest, Look
fields, background brightness, active plant effects/strengths, Animation
identity, stable Widget IDs/identities/placement and supported gesture kinds.
It carries no frame or component parameter blobs. A selected stopped Scene is
editable; stale status still makes native controls unavailable.

`patch_scene` requires `patch`, the existing guards,
`expected_composer_revision` and `expected_scene_digest`. A patch may contain:

- `look`: any nonempty subset of `palette_id` (neutral/mist/ember/spectrum),
  `pace` (0..2) and `presentation_brightness` (0..2).
- `background_brightness`: 0..1, mapped to the existing solid background gain.
- `plants`: effect ID to `{enabled?, strength?}` mappings. Strength is 0..1 and
  requires that effect to be enabled. Current combination validation applies.
- `component`: `{target, component_id, parameters, widget_id?}`. Targets are
  animation/background/widget; Widget targets require their stable ID.
  Parameters patch only declared local fields and use current normalizers.
- `widget`: `{operation: add, widget: <complete Scene Widget>}`,
  `{operation: remove, id}`, or `{operation: update, id, component_id,
  parameters?, visible?, placement?}`. Every Widget identity is explicit.

The complete candidate is normalized before queueing one current host Scene.
Omitted Scene/global fields and master zero are preserved. Successful
correlated execution reconciles into Composer/recovery through the shared
publication lock and generation guard. Explicit valid Scene edits resume output
as Composer edits do; invalid or conflicting requests never queue.

`set_target_fps` carries an integer `target_fps` from 1..200 plus the ordinary
web/controller guard. It uses the independent current controller setting and
does not require a Scene or start stopped playback.

`interact` requires `component_id`, `kind`, `x`, `y`, optional `strength` (default
1), ordinary guards, `expected_composer_revision` and `expected_scene_digest`.
Only active controller Animation gestures are supported. Coordinates satisfy
`0 <= x < 33`, `0 <= y < 138`; strength is in `(0,1]`. The controller rechecks
ownership and exact active Scene/component before delegating to the existing
Scene v2 runtime gesture dispatcher. It cannot publish, start or replace a
Scene. Correlated completion/failure is distinct from editor preview acceptance.
Unsupported requests fail explicitly; accepted handler rejection changes no
Scene or renderer instance (the command ordering revision still advances).
