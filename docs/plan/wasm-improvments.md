# Browser Wasm Composer Improvements

## Plan status

- **Status:** Portable implementation in progress; real-device, cross-browser,
  performance, recovery, and physical-wall gates remain open
- **Scope:** `/composer`, its Web Workers, Wasm/Pyodide runtime assets, preset
  authoring, fixed background plus Clock composition, local checking, and the
  explicit save/activate boundary
- **Target branch:** `codex/browser-preset-composer`
- **Release rule:** A phase is complete only when its implementation checklist
  and acceptance gate are checked and linked from the evidence log. Source
  inspection alone cannot accept interactive, visual, performance, offline, or
  physical-wall behavior.

This plan refines the browser-native composer introduced on this branch. It does
not replace the scene, installation-profile, receiver, or physical acceptance
contracts in:

- `docs/CURRENT_UX_ACCEPTANCE.md`
- `docs/ANIMATION_PIPELINE_CONTRACT_V1.md`
- `docs/RENDERING_PIPELINE_ACCEPTANCE.md`
- `docs/plan-revamped-animation-pipeline.md`

## Outcome

Deliver a preset composer that feels immediate and understandable in Firefox,
Chromium, Safari, and an installed iPhone web app while keeping these truths
explicit:

1. Opening, browsing, editing, importing, checking, and saving a draft are
   private and cannot change the wall.
2. Python previews run in a WebAssembly-backed Pyodide worker. Browser-native
   C++ previews run from allowlisted, repository-built Wasm modules.
3. A browser preview is authored simulation, not framebuffer or camera feedback
   from the physical installation.
4. Version 1 composition is one opaque background plus the fixed Clock overlay.
   It is not an arbitrary render graph.
5. Plant geometry is global installation state. Presets may recommend it but do
   not become its authority.
6. Activation is possible only for an activation-ready component/scene and is
   always an explicit, reviewable physical-wall action.

## Current baseline

The branch currently provides:

- A browser composer and installable PWA shell.
- Browser rendering for the valid Python animation catalog and its curated
  presets through Pyodide.
- Checked-in Wasm previews for the managed native Aurora component and the
  feature-gated compiled receiver rainbow.
- Fixed background plus Python Clock-overlay composition in browser workers.
- Import, download, local autosave, server-library save, local checking, and an
  explicit activation confirmation.
- Automated catalog, preset, native-parity, PWA, action, and compositing tests.

Known refinement gaps include mobile control overlap, ambiguous save semantics,
late discovery of activation incompatibility, stale check evidence, inconsistent
numeric commits, non-undoable preset selection, exposed implementation
parameters, a cold Python-worker cost on component changes, and incomplete
offline guarantees for cross-origin Pyodide assets.

## Completion conventions

- `[ ]` means not accepted, even when implementation exists.
- `[x]` means the required evidence is recorded below.
- P0 and P1 criteria block release. P2 criteria block the “native-feeling iPhone
  web app” milestone. P3 items are refinements and may be deferred explicitly.
- Performance evidence records device, OS, browser/standalone mode, power state,
  geometry, sample count, mean, p95, p99, and maximum. Desktop measurements are
  never presented as iPhone or Raspberry Pi results.
- Physical-wall tests are run only when explicitly authorized. Portable browser
  completion does not imply physical acceptance.

## Phase 0 — Freeze the contract and capture a baseline

### Implementation checklist

- [x] Define a versioned browser-scene document containing provider, component
  ID, component/runtime digest, parameter-schema version, authored parameters,
  Clock layer state, installation-profile reference, fallback, and document
  revision.
- [x] Define one draft-dirty generation counter. Every parameter, preset, layer,
  installation-profile, import, reset, and runtime change advances it.
- [x] Bind each Check result to the exact draft generation, component/runtime
  digest, geometry, and checker version that produced it.
- [x] Add an explicit activation-capability record to every catalog component:
  previewable, saveable, activation-ready, reason when unavailable, and required
  managed identity.
- [ ] Record the supported browser/device matrix and one named baseline iPhone
  used for performance gates.
- [ ] Capture cold and warm measurements before optimization: shell interactive,
  first Python preview, Python-to-Python switch, native switch, parameter-to-frame
  latency, 48-frame Check duration, worker count, and approximate memory where
  the browser exposes it.

### Phase 0 acceptance gate

- [x] **CONTRACT-01 (P0):** The same scene payload validates identically in the
  browser, save API, activation API, and host composition boundary. Unknown
  schema versions, providers, component IDs, runtime digests, parameter keys,
  layer roles, and out-of-range values are rejected with field-specific errors.
- [x] **CONTRACT-02 (P0):** Any draft mutation makes the prior Check result stale
  synchronously and clears all prior metric descriptions; no stale value can be
  presented as current.
- [x] **CAP-01 (P1):** Every selectable renderer exposes preview and activation
  capability before editing begins. An activation-incompatible renderer cannot
  expose an enabled Activate action.
- [ ] **BASE-01 (P1):** A checked-in or CI-retained baseline report includes all
  required environments and measurement fields. Missing real-device evidence is
  reported as missing, not substituted with desktop emulation.

## Phase 1 — Correctness, history, and activation safety

### Implementation checklist

- [x] Use one normalized numeric commit path for range input, number input,
  Enter, blur, clamping, display precision, preview update, autosave, and history.
- [x] Make applying a starting point one undoable history transaction.
- [x] Make layer enable/disable, Clock preset changes, imports, resets, and scene
  replacement undoable without retaining orphaned workers.
- [x] Clear all Check values and explanatory text on invalidation.
- [x] Require a current non-failing Check before activation. Non-failing cautions
  remain visible in review; missing, stale, or failed Checks remain blocked. If
  an expert bypass is retained, show the stale/unchecked state and require a
  separate explicit acknowledgement in the confirmation.
- [x] Include component/provider identity, runtime digest, layers, fallback,
  check generation/status, and destination in the activation summary.
- [ ] Preserve the previous live state on validation, save, upload, staging, or
  activation failure.
- [x] Return and display an activation receipt with the requested revision,
  accepted live identity, observed status, and an honest telemetry-completeness
  field. Do not claim physical observation without camera evidence.
- [ ] Provide one-tap rollback to the previous accepted live revision when the
  server reports that rollback is available.
- [x] Rename states and actions to `Draft autosaved locally`, `Save to library`,
  `Download JSON`, and `Activate on wall`.

### Phase 1 acceptance gate

- [ ] **STATE-01 (P0):** Range, typed numeric, select, switch, preset, import,
  reset, and layer edits produce one canonical state shared by visible controls,
  preview, exported JSON, autosave, Check, and Undo/Redo.
- [ ] **STATE-02 (P1):** Fifty mixed edits followed by full Undo and Redo produce
  the exact initial and final scene documents respectively, with no worker error
  or mismatched control.
- [x] **SAFE-01 (P0):** Loading `/composer`, selecting, editing, playing,
  comparing, importing, downloading, saving to the library, and running Check
  emit no wall mutation request.
- [x] **SAFE-02 (P0):** Activate is disabled for unsupported, invalid, unchecked,
  stale, or failing scenes. The UI gives a persistent reason and the server
  independently rejects an equivalent crafted request.
- [ ] **SAFE-03 (P0):** Canceling the activation confirmation sends no mutation.
  A failed activation leaves the previous live revision active and does not show
  a success message.
- [x] **SAFE-04 (P1):** A successful activation distinguishes command acceptance,
  reported live state, telemetry completeness, and camera observation. None is
  inferred from another.
- [x] **SAVE-01 (P1):** Local autosave, library save, JSON download, JSON upload,
  and wall activation have distinct labels, confirmations, persistence effects,
  and automated request assertions.

## Phase 2 — Preset-first, native-feeling interaction

### Implementation checklist

- [ ] Make `Looks` start with featured, recent, favorite, and current-renderer
  starting points; move the full renderer catalog behind `All animations`.
- [x] Keep starting points reachable without scrolling through the renderer
  catalog at 390×844.
- [ ] Add real rendered thumbnails or short deterministic preview loops generated
  from the same browser runtime and scene contract.
- [x] Add explicit empty/filter states, including `Editing …, hidden by filters`
  and one-tap filter clearing.
- [x] Separate creative controls from an `Installation / Advanced` disclosure.
  Hide mask paths, raw modifier JSON, and runtime diagnostics from routine Tune.
- [x] Expose plant behavior once as authoritative global installation state and
  suppress duplicate per-animation controls.
- [x] Remove duplicate mobile navigation. Preserve one clear route among Looks,
  Preview, Tune, Layers, and Check.
- [x] Fix the Draft/Split/Original overlap and add an unambiguous split divider or
  magnified comparison mode.
- [x] Explain Draft, Split, and Original on first use and through accessible help.
- [ ] Bring every primary mobile target to at least 44×44 CSS pixels, meet text
  contrast, preserve safe-area insets, and avoid truncating user-visible values.
- [x] Implement roving focus and Arrow/Home/End behavior for tablists/listboxes;
  associate every range and number field with a useful accessible name.
- [x] Add install guidance, standalone-mode polish, and visible online/offline/
  locally-ready states without nagging an already installed user.

### Phase 2 acceptance gate

- [ ] **DISC-01 (P1):** A first-time user can choose a curated look, tune it, add
  or change the Clock, run Check, save it, and reach activation review without
  encountering an implementation path, raw JSON control, or runtime name.
- [x] **DISC-02 (P1):** Featured/current starting points are visible within the
  first viewport at 390×844. Search matches renderer and preset metadata, and
  filters never silently hide which renderer is being edited.
- [x] **RESP-01 (P0):** At 390×844 and 430×932 there is no horizontal document
  overflow, clipped action, obscured comparison control, unreachable content, or
  overlap among the preview, transport, comparison, and bottom navigation.
- [ ] **A11Y-01 (P1):** Automated accessibility tests report no serious/critical
  violations. Normal text meets 4.5:1 contrast, large text meets 3:1, and all
  actionable phone targets are at least 44×44 CSS pixels.
- [ ] **A11Y-02 (P1):** The full non-pointer journey works using keyboard only in
  desktop browsers. On the baseline iPhone, VoiceOver announces useful names,
  roles, values, selection, loading, stale-check, error, and activation states.
- [ ] **NATIVE-01 (P2):** Installed standalone mode respects safe areas, survives
  background/foreground transitions without losing the draft, restores focus
  predictably after dialogs, and provides visible feedback within 100 ms of every
  tap even when rendering continues asynchronously.
- [ ] **UX-01 (P2):** Five cold first-use reviewers complete choose → tune → layer
  → check → save without assistance. At least four correctly explain local
  autosave versus library save and browser preview versus physical observation.

## Phase 3 — Warm runtime, responsive rendering, and true offline operation

### Implementation checklist

- [x] Keep one long-lived Python worker/interpreter per composer session and
  create/dispose renderer instances inside it instead of restarting Pyodide on
  ordinary Python component switches.
- [x] Bound worker instances and buffers; release replaced renderers and recover
  cleanly after worker termination or mobile-browser eviction.
- [x] Make render requests generation-aware and latest-request-wins so obsolete
  slider frames cannot overwrite newer state.
- [x] Use transferable/reused frame buffers where measurements show that copying
  is material. Keep main-thread painting bounded.
- [ ] Preload the Python runtime opportunistically only after the shell is usable;
  show staged first-load progress and a retryable failure state.
- [x] Self-host or deliberately cache the pinned Pyodide runtime and required
  packages. Record their versions and content digests.
- [x] Version service-worker caches atomically, retain the prior working shell
  until the replacement is complete, and delete old caches only after activation.
- [x] Provide an explicit `Ready offline` state only after every asset required
  for the supported offline workflow is verified in cache.
- [x] Keep server save and wall activation disabled with clear copy while offline;
  local editing, preview, Check, autosave, import, and export remain functional.
- [x] Add runtime instrumentation for cold/warm initialization, render mean/p95/
  p99/max, dropped/obsolete frames, Check duration, worker restarts, and asset
  cache state without collecting authored content.

### Phase 3 acceptance gate

- [ ] **PERF-01 (P1):** On the recorded baseline iPhone in Safari and installed
  standalone mode, warm Python component switching is at most 750 ms p95 over 20
  switches; native switching is at most 300 ms p95 over 20 switches.
- [ ] **PERF-02 (P1):** Parameter-to-visible-frame latency is at most 100 ms p95
  over 100 mixed edits, with no obsolete frame displayed after the final input.
- [ ] **PERF-03 (P2):** During a 60-second tune/play/check journey, the main thread
  has no task longer than 100 ms, interaction feedback begins within 100 ms, and
  animation remains visibly progressive rather than frozen.
- [ ] **PERF-04 (P2):** A 48-frame composed Check shows progress within 100 ms and
  completes within 5 seconds on the baseline iPhone. The report includes render
  p95, p99, maximum, changed-frame ratio, and tested geometry.
- [ ] **PERF-05 (P2):** Fifty Python/native switches and 100 Clock toggles do not
  increase live worker count or retained renderer-instance count beyond their
  documented bounds and do not crash or reload the page.
- [ ] **OFFLINE-01 (P1):** After one explicit online preparation, airplane-mode
  reload can open the composer, switch among three Python renderers and both
  checked-in native previews, apply curated presets, compose Clock, run Check,
  autosave, import, and export without a network request succeeding.
- [x] **OFFLINE-02 (P1):** A failed or interrupted cache update leaves the prior
  offline-capable version launchable. A digest mismatch never produces `Ready
  offline`.
- [ ] **RECOVER-01 (P1):** Terminating the render worker mid-preview produces a
  comprehensible recovery state, recreates the runtime once, restores the exact
  draft, and never sends a wall mutation.

## Phase 4 — Compositing and renderer parity

### Implementation checklist

- [x] Make the versioned scene document the single input to preview, Check,
  import/export, library save, and activation validation.
- [x] Keep version 1 to one opaque background plus zero or one Clock overlay with
  source-over alpha. Reject extra roles, duplicate Clock layers, unsupported
  blend modes, and ambiguous ordering.
- [x] Make provider explicit: host Python, managed receiver-native component, or
  compiled fallback. Never expose receiver-local playback as a fake Python
  `AnimationBase` component.
- [x] Apply background cadence and Clock semantic cadence independently. A cached
  Clock frame must not freeze a changing background; a high manager/render rate
  must not multiply Clock semantic events.
- [x] Use one canonical orientation and geometry contract across host Python,
  Pyodide, native Wasm, overlay composition, exported frames, and Check.
- [ ] Apply global plant installation state consistently to every supported
  Python background and Clock-preview path. Unsupported modifiers remain exact
  no-ops; zero-strength supported modifiers preserve exact parity.
- [ ] Preserve full-scene Clock presets as their own catalog contract; do not
  misrepresent them as fixed Clock-overlay presets unless migrated deliberately.
- [ ] Generate a labeled contact sheet for every browser-selectable preset at the
  real 33×138 aspect ratio after sequential warm-up frames.

### Phase 4 acceptance gate

- [x] **CAT-01 (P0):** Every browser-selectable Python component and every shipped
  curated Python preset renders a valid C-contiguous-equivalent 33×138 RGB frame,
  produces a visible frame where intended, and exercises at least one semantic or
  source tick beyond `t=0`.
- [x] **NATIVE-01 (P0):** Every browser-selectable native component has a checked-in
  or reproducibly built Wasm artifact derived from authoritative source. Declared
  curated presets pass host/Wasm frame parity at deterministic times.
- [x] **COMP-01 (P0):** Python background + Clock and native background + Clock
  match the canonical host source-over compositor byte-for-byte for deterministic
  fixtures covering transparent, translucent, opaque, empty, and edge pixels.
- [ ] **COMP-02 (P1):** Background and Clock maintain independent cadence in a
  mixed-rate test. Cached overlay frames do not suppress background changes, and
  overlay semantic state does not advance on background-only ticks.
- [ ] **MASK-01 (P1):** Global installation state reaches supported preview paths,
  is not overridden by presets, survives draft round trips, and keeps unsupported
  and enabled-zero paths exactly equal to modifier-off output and semantic state.
- [ ] **VIS-01 (P1):** The full contact sheet has no unexplained blank preset,
  clipping, accidental duplicate, incorrect aspect ratio, illegible Clock, or
  orientation mismatch. Each exception is either fixed or documented as
  intentional with an explicit test.
- [x] **IMPORT-01 (P1):** Import accepts only bounded, versioned, schema-valid JSON;
  rejects traversal paths, unknown assets/providers, prototype-polluting keys,
  excessive nesting/size, and non-finite numbers; and never provides an arbitrary
  native-code upload surface.

## Phase 5 — Cross-browser and release qualification

### Automated matrix

- [ ] Firefox current stable: desktop functional, accessibility, and visual tests.
- [ ] Chromium current stable: desktop functional, accessibility, PWA, and visual
  tests.
- [ ] WebKit automation: phone viewports, touch-oriented workflows, offline, and
  visual tests.
- [ ] Baseline physical iPhone: Safari plus installed standalone journey.
- [ ] Secondary iPhone/iOS version when available: smoke journey and recovery.

### Required end-to-end journeys

- [ ] **JOURNEY-01:** First visit → choose featured Python preset → tune with
  slider and number input → Undo/Redo → add Clock → Check → save to library →
  activation review → cancel. Assert zero live mutation.
- [ ] **JOURNEY-02:** Choose activation-ready native preset → compare Draft/Split/
  Original → add Clock → Check → activation review → cancel. Assert parity and
  zero live mutation.
- [ ] **JOURNEY-03:** Choose preview-only native renderer → edit → Check → verify
  persistent incompatibility reason and disabled activation.
- [ ] **JOURNEY-04:** Upload valid scene → edit → download → reload → restore exact
  local draft. Repeat malformed, oversized, unsupported-version, and unknown-
  provider imports and verify safe rejection.
- [ ] **JOURNEY-05:** Prepare offline → enter airplane mode → reload standalone app
  → complete offline edit/preview/Check/export → verify server save and activation
  are disabled → reconnect and reconcile without losing the draft.
- [ ] **JOURNEY-06:** Begin Check → edit during Check → verify the old run cannot
  publish current results → rerun → verify generation-bound passing result.
- [ ] **JOURNEY-07:** Kill the worker during Python rendering → recover exact draft
  → switch to native and back → verify bounded workers and no stale frame.
- [ ] **JOURNEY-08:** Keyboard-only desktop and VoiceOver iPhone completion of the
  routine authoring journey.

### Phase 5 acceptance gate

- [ ] **REL-01 (P0):** All P0 and P1 criteria in this plan pass in CI or linked
  manual evidence. No criterion is accepted solely because its implementation
  exists.
- [ ] **REL-02 (P1):** All eight journeys pass with no uncaught browser error,
  broken control, stale success, unexpected reload, or unrequested live mutation.
- [ ] **REL-03 (P1):** Every browser-selectable component and preset is covered by
  catalog/render acceptance, and every activation-ready provider has activation-
  contract coverage.
- [ ] **REL-04 (P2):** All Phase 2 and Phase 3 native-feel criteria pass on the
  baseline physical iPhone, not only an emulated viewport.
- [ ] **REL-05 (P1):** Browser completion is reported separately from controller,
  receiver, and physical-wall acceptance. Any missing hardware evidence remains
  an explicit open gate.

## Automated test deliverables

- [x] Unit tests for scene migration/validation, generation invalidation, history,
  numeric normalization, capability derivation, import limits, cache manifests,
  and runtime recovery.
- [x] Deterministic compositor fixtures shared by Python host and browser tests.
- [x] Full Python component/preset browser-runtime matrix with non-`t=0` frames.
- [x] Native build reproducibility and host/Wasm parity tests for each exposed
  native component and curated preset.
- [x] Browser integration tests asserting request methods/URLs so private actions
  cannot regress into live mutations.
- [ ] Playwright functional, accessibility, offline, keyboard, and visual tests at
  390×844, 430×932, tablet, and desktop widths.
- [ ] Performance runner that emits machine-readable cold/warm/switch/input/Check
  measurements and preserves raw samples.
- [ ] Contact-sheet generator and committed or CI-retained visual artifact for all
  curated presets.
- [ ] Service-worker upgrade, interrupted-install, digest-mismatch, and offline-
  reload tests.

## Validation commands

Use the repository's canonical environment. Commands may evolve as the test
deliverables are added; update this section when they do.

```bash
just browser-composer-assets
python3 tools/build_browser_offline_manifest.py

uv run --with numpy --with pillow --with flask --with 'werkzeug>=2.0.0' \
  python -m unittest \
  tests.unit.test_browser_scene_contract \
  tests.unit.test_browser_composer_state \
  tests.unit.test_browser_composer \
  tests.unit.test_browser_composer_actions \
  tests.unit.test_browser_composer_catalog_acceptance \
  tests.unit.test_browser_composer_pwa \
  tests.unit.test_browser_composer_mobile_ux \
  tests.unit.test_browser_composer_runtime \
  tests.unit.test_browser_native_preview \
  tests.unit.test_browser_compiled_rainbow_preview -v

uv run --with numpy --with pillow --with flask --with 'werkzeug>=2.0.0' \
  --with opencv-python-headless \
  python -m unittest discover -s tests -p 'test_*.py'

uv run --with numpy --with pillow tools/benchmarks/animation_render.py \
  --frames 100 --check --max-p95-ms 4.0 --json
```

When browser automation commands are introduced, add them here and ensure their
exit status is preserved without piping through a command that can hide failure.

## Physical-wall qualification

Portable completion ends before this section. Run it only with explicit operator
authorization and the installed topology/configuration required by
`docs/RENDERING_PIPELINE_ACCEPTANCE.md`.

- [ ] Activate one already-managed Python scene from a checked browser revision;
  record request, accepted revision, reported live state, telemetry completeness,
  and a fresh camera observation.
- [ ] Activate one already-managed native background plus Clock scene only after
  its receiver/provider gates pass; retain all-board staging and rollback evidence.
- [ ] Prove cancel, failed validation, failed staging, and failed activation leave
  the previous live revision visible.
- [ ] Prove browser rollback returns to the recorded previous revision.
- [ ] Record wall-only camera crops and digests after the final restart/deploy;
  never reuse historical camera evidence after geometry or mapping changes.

## Explicit non-goals for this plan

- Arbitrary user-supplied C/C++ or Wasm execution.
- A generic multi-layer render graph or arbitrary blend-mode editor.
- Receiver frame-track/GIF/WebP package playback.
- Strict receiver v-sync claims.
- Treating browser preview as physical-wall observation or framebuffer readback.
- Replacing the host/controller's authoritative library with browser storage.
- Moving global plant calibration paths into presets.

## Evidence log

Add one row when a gate is accepted. Link durable reports, screenshots, contact
sheets, raw benchmark output, or test logs rather than writing “tested manually.”

| Date | Gate/criterion | Commit | Environment | Evidence | Result |
|---|---|---|---|---|---|
| 2026-08-27 | CONTRACT-01, CONTRACT-02, CAP-01 | `377849a..08fb459` | Unit/API contract tests; 33×138 browser draft | [Portable evidence](../browser-composer-portable-evidence-2026-08-27.md) | Pass |
| 2026-08-27 | SAFE-01, SAFE-02, SAFE-04, SAVE-01 | `ad09338`, `2859135`, `08fb459` | Unit/API request tests plus explicit review/cancel journey | [Portable evidence](../browser-composer-portable-evidence-2026-08-27.md) | Pass; no physical observation claimed |
| 2026-08-27 | DISC-02, RESP-01 | `7364fe6`, `9f69ba2`, `2859135` | Codex in-app browser at 390×844 and 430×932 | [Portable evidence](../browser-composer-portable-evidence-2026-08-27.md) | Pass |
| 2026-08-27 | OFFLINE-02 | `95c26f6`, `2859135`, `08fb459` | Digest/cache unit tests and server-stopped reload | [Portable evidence](../browser-composer-portable-evidence-2026-08-27.md) | Pass |
| 2026-08-27 | CAT-01, Phase 4 NATIVE-01, COMP-01, IMPORT-01 | `95c26f6`, `ad09338`, `2859135` | Catalog, deterministic Wasm parity, compositor, and bounded-import tests | [Portable evidence](../browser-composer-portable-evidence-2026-08-27.md) | Pass |

## Final definition of done

- [ ] Every P0 and P1 criterion is checked and represented in the evidence log.
- [ ] Every browser-selectable animation and curated preset renders through its
  declared browser runtime and passes the compositing/catalog matrix.
- [ ] The routine mobile workflow exposes creative concepts, not raw installation
  or runtime implementation details.
- [ ] The baseline physical iPhone passes responsive, accessibility, performance,
  recovery, and offline journeys in Safari and installed standalone mode.
- [ ] Draft, local autosave, library save, Check, activation, live status, and
  physical observation remain distinct in UI language and system behavior.
- [ ] The full automated suite, browser matrix, visual artifacts, and performance
  reports pass from a clean checkout.
- [ ] Any physical-wall work is separately authorized, evidenced, and accepted;
  otherwise it remains explicitly incomplete without blocking honest portable
  browser completion.
