# Browser Preset Composer — Production Go Plan

## Status and scope

- **Working branch:** `update-animation-pipeline`
- **Comparison basis:** `main...update-animation-pipeline`
- **Portable authoring verdict:** **GO**
- **Production wall-activation verdict:** **NO-GO**
- **Plan owner:** `/composer`, its browser workers and generated assets, the
  browser-scene boundary, and the server/controller activation transaction

The no-go is no longer a missing-UX or portable-transaction problem. The branch
has a complete, streamlined authoring surface, a guarded activation transaction,
and a managed installation-profile authoring and qualification contract.
Production activation remains blocked on calibrated installation budgets, fresh
controller/receiver measurements, a clean-commit retained browser qualification,
external iPhone/VoiceOver evidence, and explicitly authorized physical-wall
canary/soak gates described below.

### Third implementation slice completed — 2026-08-27

Execution-order step 7's portable browser and recovery implementation is now
green. A real loopback `AnimationWebInterface` fixture drives seven deterministic
journeys in installed Chromium, Firefox, and WebKit engines while refusing wall
consumer attachment and retaining every attempted wall mutation. The journeys
cover the core no-mutation authoring flow, offline/reconnect behavior, exact
worker recovery, five responsive viewports, all five vibes and 14 modifiers,
exact foliage plus seven-globe profile semantics, and Python/native backgrounds
with Clock composition. See [REL-01 portable browser qualification](../browser-qualification-rel01.md).

The cross-engine run found and closed four release-relevant defects rather than
merely codifying the existing behavior:

- browser catalog/default/import boundaries now reject retired mask-path
  authority while host-side filesystem compatibility remains explicit;
- Save and overwrite preserve or invalidate Check from canonical preset identity,
  so a stale Check can no longer survive an identity-changing save;
- the browser render bridge batches background plus Clock work and sends only
  changed parameters, reducing Firefox Color Gradient plus Clock p95 from 68 ms
  to 6 ms while preserving exact frame bytes and recovery snapshots;
- verified profile artifacts can be supplied by the controlling service worker
  during origin loss, and staged cache generation `v16` is promoted only after
  complete digest verification.

Current evidence and limits for this checkpoint:

- **Passed in the raw engine runs:** Chromium 151.0.7922.34, Firefox 153.0,
  and WebKit 26.5 each passed all 7/7 journeys with zero wall mutations and the
  same final manifest.
- **Retained evidence:**
  `run_state/browser_qualification/evidence/rel01-browser-evidence-final.json`
  has file SHA-256
  `1df0e394a7a48e6cfab99257e3b86ec3aa8e8dfde8d7bfcbf7e618466b541753`.
  Its portable aggregate intentionally reports `FAIL` solely because the run
  came from a dirty working tree; a clean committed rerun is still required.
- **WebKit limitation:** Playwright's native offline flag fails before service
  worker dispatch in this engine, so its deterministic test uses a 503 fixture
  origin while the verified service worker supplies cached navigation. This is
  origin-outage coverage, not physical iOS or full device-network evidence.
- **Still external/pending:** physical-iPhone Safari, installed standalone,
  VoiceOver, Raspberry Pi/controller, receiver, calibrated electrical, canary,
  and soak evidence. Production activation therefore remains **NO-GO**.
- **Complete local regression:** 1,777 Python tests plus 3,187 subtests; 24
  rendering tests plus 3 subtests and stress/scene benchmarks; 128 native
  firmware tests; all three ESP32 build variants; and 238 deployment tests plus
  157 subtests passed. No deployment, receiver write, camera workflow, or
  physical-wall command was performed.
- **Generated assets:** the pinned Python runtime and offline manifest rebuild
  byte-for-byte at SHA-256
  `2bf9937ac26526ca78ed5514ee60b5da370baae2b1e94d75c30b236ec4071047`
  and
  `60d63564d655ad895fb8dc31016ea70a3de89e3db838370f36ecd8618f018b42`.

### Second implementation slice completed — 2026-08-27

Execution-order step 6 and the portable qualification portion of step 7 are
implemented and green. The host now owns restart-safe, optimistic-concurrency
profile drafts; authoring preserves exact 32x138 foliage and seven ordered globe
regions; publishing validates, compiles, and atomically stores one immutable
33x138 LGIP artifact without selecting it. Composer Save and Publish are
separate from wall selection, stale writes preserve the local draft, and Check
is invalidated by draft or selected-profile changes. Browser Python and native
workers verify the exact content-addressed artifact before use. Python no longer
falls back to bundled legacy mask JSON.

Qualification records now bind the exact scene, profile, global settings,
geometry, brightness, vibe, modifiers, and requested FPS. Browser, controller,
and receiver evidence are labeled separately. Missing or stale target and
electrical evidence fails closed; the browser current estimate remains advisory.
The checked qualification-record digest is carried in the guarded activation
basis. The installation budget file is intentionally unqualified rather than
inventing physical limits.

Current portable evidence for this checkpoint:

- **Passed:** ACT-01 through ACT-06, PROFILE-01, MASK-01, and CHECK-01.
- **Implemented but release-blocked on evidence:** PERF-01 and POWER-01 have
  deterministic, fail-closed contracts, but lack fresh Raspberry Pi/controller,
  receiver, and calibrated installed-wall electrical evidence.
- **Not yet complete:** REL-01 requires retained current Firefox, Chromium,
  WebKit, physical-iPhone Safari/standalone, and VoiceOver journeys. WALL-01 and
  WALL-02 were not run because deployment, receiver writes, camera workflows,
  and physical-wall commands require separate operator authorization.
- **Local regression:** 1,749 Python tests plus 3,117 subtests; 24 rendering
  tests plus 3 subtests and stress/scene benchmarks; 128 native firmware tests;
  all three ESP32 build variants; and 238 deployment tests plus 157 subtests
  passed. The focused profile/qualification/browser matrix passed 232 tests, and
  the final legacy-writer cleanup passed 22 focused tests.
- **Browser smoke:** the local Composer loaded without console errors, exposed
  foliage plus seven named globe layers, and failed closed by disabling profile
  Save/Publish and rendering when no managed artifact was selected. This is
  portable UI evidence, not retained cross-browser or wall acceptance.
- **Generated assets:** the pinned Python runtime and offline manifest rebuild
  byte-for-byte at SHA-256
  `901b4d0cb1d4aa836461fc52ed457c43280dac190746496acfa9f0635bffa5e0`
  and
  `187abffdd46af7bacabf8f870dffc183cae5261f8a20882a7e3cb1051d8db755`.

The legacy Painter is now read-only for mask compatibility. Its dead direct
mask writer, Save action, and keyboard shortcut were removed; profile authority
and all publication actions live in Composer. Production activation remains
**NO-GO** until every remaining evidence gate passes.

### First implementation slice completed — 2026-08-27

Execution-order steps 1–4 are implemented and green, with the minimum Composer
changes needed to consume the new lifecycle. The server now owns a short-lived,
single-use/idempotent Check authorization; web, durable IPC, and controller use
one canonical activation basis; the controller performs compare-and-swap,
preflight, serialized apply, fresh observation, restart-safe persistence, and
exact compensation; and cancel/rollback requests have correlated terminal
results. Unguarded execution aliases fail closed and their visible UI actions
hand off to Composer. Activation remains disabled by default and the explicit
`LEDGRID_GUARDED_ACTIVATION_CANARY=1` override is development/canary-only.

Historical portable evidence at that checkpoint:

- **Passed:** ACT-01 through ACT-06 and CHECK-01, including real file-channel
  restart transitions, post-bind crash repair, failure injection at every apply
  and receipt-publication boundary, exact retries after expiry/state/runtime
  drift, stale rollback invalidation, and exact receiver-native evidence.
- **Partially complete:** PROFILE-01 carries one digest through browser scene,
  server basis, controller, and receiver transaction, but browser workers do not
  yet load and verify the immutable profile artifact required by step 6.
- **Not yet run/complete:** MASK-01, PERF-01, POWER-01, REL-01, WALL-01, and
  WALL-02. Production activation therefore remains **NO-GO**.
- **Local regression:** 1,718 Python tests plus 3,097 subtests, rendering stress
  and scene benchmarks, 128 native firmware tests, three ESP32 build variants,
  and 238 deployment tests plus 157 subtests passed. No deployment, receiver
  write, camera workflow, or physical-wall command was performed.

The second implementation checkpoint above supersedes this snapshot; it is
retained only to preserve the execution history.

The acceptance table and next implementation context below are the current
handoff. Broader rendering, receiver, installation-profile, and physical-wall
contracts remain authoritative in:

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
| Plant masks | A managed 32×138 foliage/seven-globe editor with paint, erase, stroke Undo, zoom, revert, optimistic-concurrency Save, and immutable candidate Publish, presented against the 33×138 wall preview. Selection remains a separate reviewed wall transaction. |
| Local Check | A 48-frame isolated render check plus a server-owned qualification record for schema, motion, luminance, clipping, temporal change, advisory browser current, and target-FPS render budget. Results bind to the exact scene, runtime, profile artifact, geometry, globals, and evidence provenance/freshness. |
| Safety UX | Opening, editing, previewing, importing, exporting, saving, and checking do not mutate the wall. Activation is blocked when offline, unsupported, unchecked, stale, failing, or when global settings are dirty, applying, or not yet observed. |
| Offline/PWA | Deterministic generated assets, a pinned browser runtime bundle, verified immutable profile artifacts, atomic cache upgrade behavior, explicit offline readiness, and current cache generation `v16`. |

The current portable feature suite and raw desktop-engine matrix are green. The
exact branch run for the third checkpoint is recorded above. Automated review at
375×667, 390×844, 430×932, 768×1024, and 1440×1000 found no clipped or
unreachable composer controls, horizontal overflow, sub-44-pixel primary action
targets, or console errors. These are portable/browser results, not a clean
retained REL-01 release pass or controller, receiver, electrical, iPhone,
VoiceOver, or physical-wall acceptance.

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

Items 1–3 below record the original first-slice blockers and were closed by the
first checkpoint. Item 4 was closed by the second checkpoint. Item 5 now has a
fail-closed implementation, but calibrated target evidence is still absent.
REL-01 and the physical acceptance gates also continue to block production
activation.

### 1. Activation authority is client-side — closed in first slice

The composer correctly invalidates a Check when its draft, runtime, geometry,
wall settings, or installation profile changes. The server does not own that
binding. A crafted valid request can call `PUT /api/v1/scene` without a current
server-issued Check token or expected wall-state revision.

### 2. The activation payload loses installation-profile identity — closed in first slice

The browser scene contains `installation_profile.digest`, but
`browser_scene_to_host_scene()` does not carry it into the host scene. The web
layer verifies that a profile can be resolved; it does not prove that the
controller applies the same selected profile and globals that were checked.

### 3. A queue receipt is not a live receipt — closed in first slice

The activation route queues `start_scene` and immediately returns
`command_accepted: true`, `accepted_live_identity: null`,
`observed_status: not_observed`, and `telemetry_complete: false`. The UI labels
this honestly, but there is no correlated completion status or activation
rollback.

### 4. Mask editing is not a managed profile publication flow — closed in second slice

The prior editor aggregated planter bowls and wrote two legacy mask files
separately. Composer now preserves the seven globe-region identities, uses an
opaque restart-safe revision for optimistic concurrency, and publishes one
validated immutable profile candidate. Publishing never selects or activates
the candidate. The compatibility Painter no longer exposes a writer.

### 5. Browser Check is not production qualification — contract complete, evidence pending

The browser estimate still explicitly uses an uncalibrated current model, and
its p95 render time still measures only the browser. The server now produces a
deterministic qualification record that separates those measurements and fails
closed when calibrated installation electrical limits or fresh
controller/receiver performance evidence are absent. Production still needs
that real evidence for the exact scene, profile, globals, and target FPS.

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
- service-worker upgrade from release cache `v15` to verified cache generation
  `v16`;
- keyboard-only desktop and VoiceOver iPhone journeys;
- 390×844 and 375×667 phone layouts, 430×932, tablet, and desktop.

## Acceptance gates

| Gate | Status at 2026-08-27 | Release-blocking result |
|---|---|---|
| **ACT-01** | **PASS** | An otherwise valid crafted activation without a current server Check token is rejected with no command queued. |
| **ACT-02** | **PASS** | Changing scene, runtime, globals, profile, or controller revision after Check returns a conflict with zero mutation. |
| **ACT-03** | **PASS** | The controller receives and observes the same scene, globals, runtime, and profile identities that the server checked. |
| **ACT-04** | **PASS** | Queue acknowledgement remains `pending`; only correlated fresh observation can produce `active`. |
| **ACT-05** | **PASS** | Failure injection at every apply boundary restores the exact prior complete state and reports the rollback result. |
| **ACT-06** | **PASS** | Cancel, timeout, duplicate submission, reconnect, and retry are idempotent and cannot create an untracked activation. |
| **PROFILE-01** | **PASS (portable)** | Browser worker, server, controller, and receiver-facing transaction resolve the same immutable profile digest. |
| **MASK-01** | **PASS (portable)** | Stale mask saves are rejected; publish is atomic; all seven globe identities survive edit/publish/reload exactly. |
| **CHECK-01** | **PASS** | Every Check token is bound to the full activation basis, expires, and cannot authorize another draft or state. |
| **PERF-01** | **NO-GO — evidence absent** | Browser, controller/Pi, and receiver measurements are labeled separately and pass their declared target-FPS budgets. |
| **POWER-01** | **NO-GO — uncalibrated** | The exact activation passes a versioned installation electrical budget; an uncalibrated browser estimate cannot satisfy this gate. |
| **REL-01** | **PENDING EXTERNAL + CLEAN RETENTION** | All seven automated journeys pass in current Firefox, Chromium, and WebKit; a clean committed retained rerun plus physical-iPhone Safari, installed standalone, and VoiceOver evidence remain required. |
| **WALL-01** | **PENDING AUTHORIZATION** | An explicitly authorized wall canary proves active identity, fresh telemetry, visible output, failure recovery, and rollback. |
| **WALL-02** | **PENDING AUTHORIZATION** | The accepted build survives the required soak with no new controller, receiver, queue, display, or electrical fault. |

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

Steps 1–7's portable implementation and raw desktop-engine journeys are green.
The next slice must rerun and retain the matrix from this committed revision,
capture the external iPhone/standalone/VoiceOver journeys, and record separately
labeled target performance and electrical evidence. Step 8 remains forbidden
until an operator explicitly authorizes physical-wall work and all portable
release gates pass.

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
  tests.unit.test_browser_composer_accessibility_acceptance \
  tests.unit.test_browser_qualification_rel01 \
  tests.unit.test_guarded_ui_legacy_debt \
  tests.unit.test_browser_composer_mobile_ux \
  tests.unit.test_browser_composer_profile_contract \
  tests.unit.test_browser_composer_profile_runtime \
  tests.unit.test_activation_qualification \
  tests.unit.test_installation_profile_authoring \
  tests.unit.test_installation_profile_authoring_api \
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

The next implementation context should finish **P1 — Browser and recovery
qualification** (execution-order step 7): rerun and retain the current
Firefox/Chromium/WebKit evidence from this committed revision, capture
physical-iPhone Safari/standalone and VoiceOver evidence, then capture separately
labeled target performance and calibrated electrical evidence. Do not remove the
development/canary-only activation restriction or begin step 8 until PERF-01,
POWER-01, and REL-01 pass and physical-wall work is explicitly authorized.
