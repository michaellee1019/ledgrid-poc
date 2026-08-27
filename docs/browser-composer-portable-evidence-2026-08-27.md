# Browser composer portable evidence — 2026-08-27

## Scope and environment

- Branch: `codex/browser-preset-composer`
- Implementation commits: `377849a` through `08fb459`
- Worktree: isolated from the user's existing checkout
- Host: Apple Silicon (`arm64`), macOS 26.6.2 (25G83)
- Interactive browser: Codex in-app browser
- Responsive viewports: 390×844 and 430×932 CSS pixels
- Geometry: 33 strips × 138 LEDs = 4,554 pixels
- Physical wall: not contacted or observed; no physical qualification was authorized

This is portable browser evidence only. It is not iPhone Safari, installed-iPhone,
controller, receiver, camera, or physical-wall acceptance.

## Implemented contract and safety boundary

- Added the versioned `ledgrid.browser-scene` v1 document and strict validation
  for provider/component/runtime/schema identities, authored parameters, the fixed
  Clock slot, fallback, installation-profile digest, and document revision.
- Bound Check results to the exact draft generation, runtime digest, geometry, and
  checker version. A parameter or layer mutation clears the prior result and every
  displayed metric immediately.
- Added explicit preview/save/activation capabilities and a persistent reason for
  preview-only renderers.
- Made the same scene document the input to local draft/export, scene-library save,
  server validation, and activation.
- Kept command acceptance, reported live identity, telemetry completeness, camera
  observation, and rollback availability separate in the activation receipt.
- Allowed a current Check with non-failing cautions to reach explicit review while
  continuing to block missing, stale, failed, offline, or capability-incompatible
  Checks.

## Runtime and offline behavior

- Python renderers share one warm Pyodide worker; ordinary component switches use
  bounded renderer instances instead of restarting the interpreter.
- Render requests carry generations and obsolete results cannot replace a newer
  frame. Worker recovery is bounded and exposes diagnostics.
- The service worker installs an atomic, digest-verified v11 shell and records the
  pinned Pyodide runtime and packages before reporting `Ready offline`.
- The offline manifest, Python bundle, and native Wasm artifacts rebuild
  deterministically with no unexpected working-tree changes.

## Interactive browser results

### Responsive and accessibility-oriented checks

- 390×844 Looks view: document width 390, no horizontal overflow, featured presets
  visible in the initial viewport, and all visible actions at least 44 CSS pixels
  high.
- 390×844 Preview view: comparison control occupied y=603–655, transport y=661–726,
  and bottom navigation y=776–844; the regions did not overlap.
- 430×932 Preview view: document width 430, no horizontal overflow, minimum action
  height 44; comparison y=691–743, transport y=749–814, and bottom navigation
  y=864–932.
- The single five-destination mobile navigation reached Looks, Preview, Tune,
  Layers, and Check. Starting-point Arrow navigation moved roving focus to the next
  option.
- Entering `1.5` for a 0–1 Brightness field normalized to `1.00`. Entering `0.5`
  created one history entry, and Undo restored `1.00`.
- Enabling Clock invalidated the prior Check synchronously, returned all metrics to
  `Waiting`, and disabled activation until a new Check completed.
- A managed native preview displayed the persistent reason “This provider is
  catalog-visible but not executable in host scenes” and kept activation disabled.

### Offline journey

1. Explicit preparation changed the state to `Ready offline`.
2. The preview-only local server was stopped.
3. Reload succeeded from the service-worker cache and restored the named draft.
4. A Python/Pyodide preview rendered with the connection state `Local only`.
5. A 48-frame Check completed without any successful network request.
6. `Save to library` and `Review & activate` remained disabled with explicit
   offline explanations.

### Activation review safety

- A non-failing cautioned Check enabled `Review & activate` and labeled its state
  `Activation-ready with cautions`.
- Review showed component/provider identity, runtime digest, document revision,
  exact Check generation/status, overlay, fallback, and physical-wall destination.
- Cancel closed the review. The preview-only server log contained no scene `PUT`
  and no browser warnings or errors were recorded.

## Automated validation

- Deterministic asset build: passed; Python bundle 2,473,025 bytes with SHA-256
  `3433b0217f6ade688cd9faf9be9ec21553222c31220e373cfe4fa5d437d8b435`.
- Final integrated browser contract/runtime/PWA/mobile/native suite: 56 tests
  passed in 4.963 seconds, including the pass/warn/fail/stale activation matrix.
- Full repository discovery: 1,595 tests ran in 105.836 seconds. One unrelated
  semantic-palette timing/state test failed in the aggregate run and immediately
  passed alone in 0.185 seconds. The browser-composer-focused suites were green.
- Host animation render benchmark: every measured plugin remained below the
  configured 4.0 ms p95 ceiling; the highest observed p95 was 1.5700 ms.

## Gates intentionally left open

- Named physical iPhone Safari and installed-standalone performance, VoiceOver,
  background/foreground, and offline evidence.
- Firefox, standalone Chromium, and automated WebKit matrix journeys.
- Five-person first-use study.
- Fifty-switch/100-Clock-toggle retained-memory run and worker-kill browser journey.
- Full offline journey across three Python and both native renderers.
- Physical activation, live observation, rollback, controller, receiver, and camera
  qualification.
- The one aggregate-suite semantic-palette failure should be tracked as a flaky
  repository test unless it can be reproduced independently.
