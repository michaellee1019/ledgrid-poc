# REL-01 portable browser qualification

This lane retains machine-readable Chromium, Firefox, and WebKit evidence for
the portable Composer contract. It cannot qualify physical iPhone Safari, iOS
installed standalone mode, VoiceOver, physical wall output,
controller/receiver performance, or electrical safety. Those results belong in
separate evidence records and remain required before REL-01 can be closed.

## Acceptance contract

[`tools/browser_qualification/rel01_manifest.json`](../tools/browser_qualification/rel01_manifest.json)
is the versioned source of truth. Every required engine must execute all eight
journeys and retain its reported engine identity and version, Playwright
version, timestamps, viewport observations, and individual assertion outcomes.

- `core_no_mutation` chooses the activation-ready Color Gradient background, tunes it,
  proves Undo/Redo and the Clock toggle, runs a local Check, saves to the
  authoring library, proves the assigned preset identity invalidates that Check,
  reruns Check for the saved identity, and cancels guarded activation review.
- `offline_reconnect` seeds a real `v17` cache on the fixture origin, proves the
  verified `v18` promotion removes it, blocks the dynamic bootstrap endpoint,
  and proves the versioned static catalog still starts a Python renderer with
  Play enabled and zero wall-state reads. It then prepares the pinned offline
  runtime, proves the active worker retains the bundled catalog and exact
  profile, reloads and edits while offline, proves a stale Check cannot enable
  review, runs a new Check, exports, reconnects, refreshes digest-compatible
  server capabilities, and verifies the local draft did not change.
  Chromium uses browser-native offline emulation. Because Playwright Firefox 153
  and WebKit 26.5 reject navigation before dispatching to an active service
  worker under that flag, they use the no-wall fixture's origin-outage gate:
  every origin request returns 503, the fixture retains the rejected count and
  path, and `/composer` must still reload from verified cache before the gate is
  removed for reconnect.
  Evidence labels these strategies `native_network_offline` and
  `fixture_origin_outage` respectively. Before and after the offline Check, the
  runner compares the complete Python runtime cache inventory by URL, byte
  count, SHA-256, and recorded metadata; any new, missing, or changed runtime
  asset fails the journey.
- `worker_recovery` captures a nested Aurora managed-native draft, records a labeled
  synthetic Worker error immediately followed by real `Worker.terminate()`,
  requires exactly one bounded restart, compares the exact restored draft,
  runs a recovered Check, and detects later stale overwrites.
- `responsive_layouts` observes 375×667, 390×844, 430×932, 768×1024, and
  1440×1000. Each viewport must have no horizontal overflow, reachable
  navigation and actions, and at least 44×44 primary targets.
- `keyboard_only_desktop` uses only Tab, Shift+Tab, Enter, Space, arrow,
  Home/End, and declared keyboard shortcuts after navigation. It follows the
  skip link, filters and chooses Color Gradient, tunes with Undo/Redo, operates
  the inspector tablist and Clock switch, completes Check and library Save,
  then proves native dialog focus containment, Escape cancellation, exact
  focus return, and zero wall mutation.
- `global_controls` exercises all five vibes, brightness, speed, FPS, and all
  fourteen plant-modifier classes, then opens and cancels review.
- `profile_masks` uses the managed profile draft to paint foliage plus all
  seven named globe regions. It binds the exact eight painted cell identities
  to the saved profile, publishes an immutable candidate, cancels
  profile-selection review, reloads, and compares the exact saved masks. Each
  engine uses a distinct declared LED row so a single fresh fixture can run the
  three-engine matrix without one engine's saved draft turning the next run
  into a no-op.
- `python_native_clock` runs Clock checks on the activation-ready Color Gradient
  background and the browser-previewable Aurora managed-native background. It also
  requires the native card's exact declared host-activation ineligibility reason
  so browser preview evidence cannot be mistaken for receiver readiness. A
  missing pinned Python runtime is an honest blocker, not a reason to skip this
  journey.

Browser console errors and any non-authoring API mutation fail the relevant
journey. Missing engines, missing or duplicate assertions, engine identity
mismatches, missing versions, viewport mismatches, probe crashes, Playwright
version drift, and dirty working trees all fail closed. A passing portable
matrix still records REL-01 as `PENDING_EXTERNAL_EVIDENCE`.

## No-wall fixture

Use the repository fixture rather than a production server. It binds only to a
loopback host, serves the real `AnimationWebInterface` and Composer assets,
seeds a temporary `InstallationProfileLibrary` from the checked-in profile
fixture, builds and publishes the checked-in Aurora package into a temporary
managed-native library with the required scene-policy gates, keeps Check and
guarded review available for components that declare activation readiness,
attaches no controller consumer, and retains then rejects every attempted wall
command. The real Aurora descriptor still fails closed if the product marks its
host implementation unloaded; the fixture does not override that declaration.
The fixture gives its web and synthetic controller surfaces one identical
SHA-256 release identity derived from the checked-out Git commit, publishes a
canonical active-state digest, and records both identities plus the source
commit in fixture status. Retained evidence rejects inconsistent identities, an
identity not derived from that commit, or a fixture commit different from the
clean commit recorded after the matrix.
Native build output is content-addressed under the ignored
`run_state/browser_qualification_native_builds` directory; authoritative library
state remains inside the selected fixture state directory.

```bash
uv run python -m tools.browser_qualification.fixture_server \
  --state-dir /tmp/ledgrid-rel01-fixture \
  --host 127.0.0.1 \
  --port 8765
```

The fixture status is available at `/__qualification__/status`. A nonzero
`wall_mutation_attempts` count or any line in
`wall-mutation-attempts.jsonl` is a qualification failure.
`network_outage_blocks` is separate: WebKit offline qualification requires a
positive count including `/composer`, proving the cached navigation did not
reach a usable origin while the outage gate was active.
This desktop WebKit fixture result is not physical iPhone Safari, installed iOS
standalone, or a claim about full-device network behavior.

## Running and retaining evidence

Install the exact Playwright version declared by the tooling package, or point
the runner at an existing matching module without fetching during the run:

```bash
LEDGRID_PLAYWRIGHT_MODULE=/absolute/path/to/node_modules/playwright \
  uv run python -m tools.browser_qualification.evidence \
  --base-url http://127.0.0.1:8765 \
  --output /absolute/path/to/retained/rel01-browser-evidence.json
```

The command exits successfully only when all three real engines execute every
required portable assertion from a clean commit. It always writes JSON,
including launch, runtime, or journey failures, so an unavailable engine can
never be mistaken for a pass. The output location is operator-selected and the
record is not committed automatically.

`manifest_sha256` is the SHA-256 of canonical JSON (sorted keys with compact
separators), not the byte digest of the pretty-printed manifest file. This keeps
the evidence binding stable across whitespace-only formatting while still
changing for every semantic manifest edit.
