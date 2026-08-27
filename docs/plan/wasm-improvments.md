# Browser Preset Composer — Production Go Plan

## Status and scope

- **Working branch:** `update-animation-pipeline`
- **Comparison basis:** `main...update-animation-pipeline`
- **Portable authoring verdict:** **GO**
- **Production wall-activation verdict:** **NO-GO**
- **Plan owner:** `/composer`, its browser workers and generated assets, the
  browser-scene boundary, and the server/controller activation transaction

The no-go is no longer a missing-UX problem. The branch has a complete,
streamlined authoring surface. Production activation remains blocked because
the browser is still the only place that binds Check results to exact state,
while the server queues an activation without a controller-owned compare-and-
swap, correlated observation, or exact rollback.

This file is the current handoff for closing that gap. Broader rendering,
receiver, installation-profile, and physical-wall contracts remain authoritative
in:

- [Current UX acceptance](../CURRENT_UX_ACCEPTANCE.md)
- [Animation pipeline contract](../ANIMATION_PIPELINE_CONTRACT_V1.md)
- [Rendering pipeline acceptance](../RENDERING_PIPELINE_ACCEPTANCE.md)
- [Unified animation pipeline plan](../plan-revamped-animation-pipeline.md)

## What this branch adds over `main`

| Area | Current branch state |
|---|---|
| Composer shell | A spartan, three-pane desktop tool with six direct mobile destinations: Looks, Preview, Tune, Layers, Wall, and Check. |
| Browser rendering | Browser-native Python rendering in a long-lived Pyodide worker plus allowlisted repository-built native Wasm previews. All browser-selectable backgrounds and curated presets are covered by the render matrix. |
| Scene model | One background plus the optional fixed Clock overlay, with explicit provider, runtime, fallback, preset, and parameter identities. |
| Authoring | Schema-generated controls, exact numeric entry, curated starting points, local autosave, bounded Undo/Redo, import, download, copy, and server-library save. |
| Comparison | Draft, split, and original views with local playback and an explicit statement that preview is not wall or camera feedback. |
| Wall settings | Five vibes, master brightness, 0.25×–3× operator speed, 1–200 target FPS, and all 14 plant modifiers with strengths and field/surface exclusivity. These remain global installation state, separate from presets. |
| Plant masks | A 32×138 foliage/planter editor with paint, erase, stroke Undo, zoom, revert, and save, presented against the 33×138 wall preview. |
| Local Check | A 48-frame isolated render check for schema, motion, luminance, clipping, temporal change, estimated current, and target-FPS render budget. Results bind to the draft generation, component/runtime identity, geometry, wall settings, and installation-profile digest. |
| Safety UX | Opening, editing, previewing, importing, exporting, saving, and checking do not mutate the wall. Activation is blocked when offline, unsupported, unchecked, stale, failing, or when global settings are dirty, applying, or not yet observed. |
| Offline/PWA | Deterministic generated assets, a pinned browser runtime bundle, atomic cache upgrade behavior, explicit offline readiness, and current cache generation `v14`. |

The current portable suite is green. The last exact branch run before this plan
refresh passed 1,635 tests and 3,015 subtests. Cold mobile review at 390×844 and
375×667 found no clipped or unreachable composer controls, no horizontal
overflow, no sub-44-pixel action targets, and no console errors. These are
portable/browser results, not controller or physical-wall acceptance.

## Product and safety invariants

The remaining work must preserve these boundaries:

1. Draft creation, local autosave, library save, JSON import/export, Check, wall
   settings, activation, live status, and camera observation are distinct states
   and actions.
2. Presets never own vibe, brightness, operator speed, target FPS, plant
   modifiers, masks, or selected installation-profile authority.
3. Version 1 remains one opaque background plus zero or one fixed Clock overlay.
   Do not expand this work into a generic render graph.
4. Only cataloged Python components and repository-built, managed native
   identities may activate. Arbitrary native or Wasm uploads remain out of scope.
5. A browser render is authored simulation. Browser timing is not Raspberry Pi,
   receiver, electrical, framebuffer, or camera evidence.
6. Queued, applied, observed, telemetry-complete, and physically observed are
   never synonyms.
7. Every rejected, canceled, timed-out, or failed activation leaves the prior
   complete live state active or restores it exactly.

## Why production activation is still no-go

### 1. Activation authority is client-side

The composer correctly invalidates a Check when its draft, runtime, geometry,
wall settings, or installation profile changes. The server does not own that
binding. A crafted valid request can call `PUT /api/v1/scene` without a current
server-issued Check token or expected wall-state revision.

### 2. The activation payload loses installation-profile identity

The browser scene contains `installation_profile.digest`, but
`browser_scene_to_host_scene()` does not carry it into the host scene. The web
layer verifies that a profile can be resolved; it does not prove that the
controller applies the same selected profile and globals that were checked.

### 3. A queue receipt is not a live receipt

The activation route queues `start_scene` and immediately returns
`command_accepted: true`, `accepted_live_identity: null`,
`observed_status: not_observed`, and `telemetry_complete: false`. The UI labels
this honestly, but there is no correlated completion status or activation
rollback.

### 4. Mask editing is not a managed profile publication flow

The editor aggregates planter bowls for editing and writes the two legacy mask
files separately. It has no optimistic-concurrency token, and its UI does not
preserve the seven globe-region identities as first-class authored data. Saving
a calibration must create a new immutable profile candidate; selecting it for
the wall must be a separate reviewed transaction.

### 5. Browser Check is not production qualification

The current estimate explicitly uses an uncalibrated current model, and its p95
render time measures the browser. Production needs an installation electrical
budget plus controller/target performance evidence for the exact scene,
profile, globals, and target FPS.

## Required implementation

### P0 — Canonical activation basis and server Check token

Create one canonical activation basis containing at least:

- normalized browser-scene digest and revision;
- component/provider/runtime and managed-native identities;
- selected installation-profile digest;
- canonical global settings digest and revision;
- controller state revision and current active identity;
- checker/qualification version and expiry.

Add a server-owned Check endpoint that validates this exact basis and returns an
opaque, short-lived token. The server may retain browser metrics as advisory
evidence, but it must independently run or retrieve every release-blocking
validation. A token is single-use or idempotently bound to one activation ID.

Activation must require the token and the expected controller state revision.
The legacy direct mutation route must either use the same guarded path or reject
unguarded requests. A change to scene, runtime, globals, profile, or controller
revision after Check must return a conflict before any mutation.

### P0 — Controller-owned atomic activation

Carry one command envelope across web, IPC, and controller boundaries:

```text
activation_id
check_token / qualification identity
expected_controller_state_revision
expected_scene identity
expected_global_settings identity
expected_installation_profile_digest
desired normalized scene
desired global settings
```

At the controller:

1. Revalidate the envelope and compare the expected revision/identities with the
   current state.
2. Snapshot the prior scene, global settings, selected profile, power state, and
   provider/runtime bindings.
3. Preflight every desired artifact before changing any state.
4. Apply the profile, globals, and scene as one logical transaction.
5. Publish a new state revision only after the desired state is observed.
6. On any error or timeout, compensate to the exact snapshot and report the
   rollback outcome.

Reuse the existing installation-profile library and transaction machinery. Do
not create a second profile authority inside the composer.

### P0 — Correlated activation status and rollback

Return `202 Accepted` with an activation ID and `pending` status when the command
is only queued. Add a status resource or equivalent event stream keyed by that
ID. Its terminal record must include:

- requested and normalized identities;
- controller command ID and state revisions before/after;
- phase: queued, preflighting, applying, observing, active, rolling_back,
  rolled_back, failed, or timed_out;
- observed scene, global-settings, and profile identities;
- controller/receiver telemetry completeness and freshness;
- rollback availability and rollback result;
- camera observation only when a fresh camera workflow actually supplied one.

The composer may show “Active” only after a fresh correlated status matches the
entire activation basis. Cancel sends no mutation. Failure never displays a
success state. One-tap rollback is enabled only when the server advertises an
exact retained snapshot and must itself produce a correlated receipt.

### P0 — Immutable profile and mask workflow

Make the exact selected installation profile available to browser workers as a
content-addressed, read-only preview artifact. Verify its digest before rendering
or checking; do not silently fall back to mask copies embedded in the Python
bundle.

Replace direct mask-file authority with this flow:

1. Load a named profile revision and its explicit foliage plus seven globe
   regions.
2. Edit a local draft without changing selected wall state.
3. Save with `If-Match` or an equivalent expected revision; reject stale saves.
4. Validate geometry, disjoint semantic layers, region identity, and bounds.
5. Compile and publish one immutable, content-addressed profile artifact.
6. Select that artifact only through a separate reviewed wall transaction.
7. Invalidate Check immediately when the draft or selected profile digest
   changes.

Compatibility writers may update legacy JSON files after a successful publish,
but the pair must not be the transactional source of truth.

### P1 — Installation-aware qualification

- Define the installed wall’s voltage, current, brightness, and safety budgets
  in versioned configuration.
- Qualify the exact composed scene at the requested global brightness, vibe,
  modifiers, profile, geometry, and target FPS.
- Record browser metrics separately from Raspberry Pi/controller and receiver
  metrics.
- Fail closed when required target evidence is absent or stale. Non-blocking
  artistic cautions may reach review only with explicit acknowledgement.
- Keep semantic masks and zero-strength/unsupported modifier no-op guarantees in
  the render matrix.

### P1 — Browser and recovery qualification

Automate and retain results for current Firefox, Chromium, and WebKit plus a
physical iPhone in Safari and installed standalone mode. Cover:

- choose → tune → Undo/Redo → Clock → Check → library save → activation review
  → cancel, with zero mutation;
- global vibe/brightness/speed/FPS and all plant-modifier classes;
- foliage and each of the seven globe regions through draft, publish, reload,
  selection review, and cancel;
- Python and managed-native backgrounds with the Clock overlay;
- offline preparation, offline reload/edit/Check/export, reconnect, and stale
  activation rejection;
- worker termination and bounded recovery with exact draft restoration;
- service-worker upgrade from the previous release cache to `v14` or its
  successor;
- keyboard-only desktop and VoiceOver iPhone journeys;
- 390×844 and 375×667 phone layouts, 430×932, tablet, and desktop.

## Acceptance gates

| Gate | Release-blocking result |
|---|---|
| **ACT-01** | An otherwise valid crafted activation without a current server Check token is rejected with no command queued. |
| **ACT-02** | Changing scene, runtime, globals, profile, or controller revision after Check returns a conflict with zero mutation. |
| **ACT-03** | The controller receives and observes the same scene, globals, runtime, and profile identities that the server checked. |
| **ACT-04** | Queue acknowledgement remains `pending`; only correlated fresh observation can produce `active`. |
| **ACT-05** | Failure injection at every apply boundary restores the exact prior complete state and reports the rollback result. |
| **ACT-06** | Cancel, timeout, duplicate submission, reconnect, and retry are idempotent and cannot create an untracked activation. |
| **PROFILE-01** | Browser worker, server, controller, and receiver-facing transaction resolve the same immutable profile digest. |
| **MASK-01** | Stale mask saves are rejected; publish is atomic; all seven globe identities survive edit/publish/reload exactly. |
| **CHECK-01** | Every Check token is bound to the full activation basis, expires, and cannot authorize another draft or state. |
| **PERF-01** | Browser, controller/Pi, and receiver measurements are labeled separately and pass their declared target-FPS budgets. |
| **POWER-01** | The exact activation passes a versioned installation electrical budget; an uncalibrated browser estimate cannot satisfy this gate. |
| **REL-01** | Required journeys pass in Firefox, Chromium, WebKit, physical iPhone Safari, and installed standalone mode. |
| **WALL-01** | An explicitly authorized wall canary proves active identity, fresh telemetry, visible output, failure recovery, and rollback. |
| **WALL-02** | The accepted build survives the required soak with no new controller, receiver, queue, display, or electrical fault. |

Production activation becomes **GO** only when every gate above passes. Portable
authoring stays **GO** while hardware gates are pending, provided activation
remains disabled or clearly marked unavailable in production.

## Execution order

1. Add failing contract tests for Check tokens, state conflicts, activation
   receipts, terminal observation, and rollback.
2. Add the canonical activation-basis and status schemas in `ipc/scene_contract.py`.
3. Carry profile/global identities and expected revisions through
   `web/app.py`, `ipc/control_channel.py`, and `ipc/runtime_control.py`.
4. Implement controller compare-and-swap, snapshot, compensation, and correlated
   status in the runtime/manager boundary.
5. Change the composer from direct activation to Check-token creation,
   activation submission, status observation, and exact rollback.
6. Move mask edits onto managed profile draft/publish/select APIs and load the
   exact immutable profile into browser workers.
7. Add browser/recovery automation and installation-aware qualification.
8. Run a guarded physical canary and soak only after explicit operator
   authorization and all portable gates pass.

The first implementation slice should stop after steps 1–4 are green. It should
not combine activation authority with further visual redesign; the current UX is
already accepted and should change only where the new transaction states require
clearer controls or language.

## Verification

Run focused tests throughout, then the complete portable gate:

```bash
just browser-composer-assets

uv run --with numpy --with pillow --with flask --with 'werkzeug>=2.0.0' \
  python -m unittest \
  tests.unit.test_browser_scene_contract \
  tests.unit.test_browser_composer_state \
  tests.unit.test_browser_composer_actions \
  tests.unit.test_browser_composer_runtime \
  tests.unit.test_browser_composer_pwa \
  tests.unit.test_browser_composer_mobile_ux \
  tests.unit.test_installation_profile_library \
  tests.unit.test_installation_profile_transaction -v

just test-unit
just test-rendering
just test
git diff --check
```

Generated composer assets must reproduce with no diff. New activation tests must
assert both responses and absence of downstream mutation. Failure-injection
tests must compare the complete before/after desired and observed state, not only
the scene identifier.

Physical-wall commands, deployment, receiver writes, and camera acceptance are
outside the portable implementation loop and require explicit authorization.

## Definition of done

- The existing composer remains fast, spartan, complete, and usable on desktop
  and phone.
- Preset authoring includes the full browser catalog, Clock composition, vibes,
  global output controls, all plant modifiers, and managed plant-mask editing.
- The server, not browser UI state, authorizes activation for one exact,
  immutable activation basis.
- Web, controller, and receiver-facing layers agree on scene, runtime, globals,
  installation profile, and state revision.
- Pending work is never described as active; fresh observation is correlated to
  one activation ID.
- Every failure path preserves or restores the prior exact live state.
- Cross-browser, performance, electrical, controller, receiver, and physical-wall
  evidence are recorded separately and all release gates pass.

The next context should begin with **P0 — Canonical activation basis and server
Check token**, using failing API/IPC tests before changing the composer UI.
